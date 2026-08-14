# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

로컬 거시경제 수집기 + 웹 대시보드. 매시간 시장·거시지표·뉴스·경제 캘린더를 자동 수집(APScheduler)하고, LLM(`claude -p` 헤드리스 또는 Gemini REST)이 그 데이터를 근거로 한국어 거시 시황 브리핑을 자동 작성한다. 한국 거주 개인투자자의 의사결정(지킴/성장/가치) 지원이 북극성. 단일 프로세스 FastAPI 앱, SQLite(WAL) 저장, Chart.js 프론트. 코드·주석·UI는 한국어.

## Commands (Windows, `.venv` 사용)

```bash
# 의존성 설치
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 실행 (스케줄러 + 웹서버 한 프로세스) -> http://127.0.0.1:8000
.\.venv\Scripts\python.exe run.py

# 실네트워크 수집 점검 (결과 verify_report.json)
.\.venv\Scripts\python.exe verify_realenv.py

# 페이지별 테스트 서버 (스케줄러·자동수집 비활성 -> 8000 서버와 DB 충돌 없이 공존)
.\.venv\Scripts\python.exe run_hyundai_test.py   # 8001 /hyundai
.\.venv\Scripts\python.exe run_shinhan_test.py   # 8002 /shinhan
.\.venv\Scripts\python.exe run_flows_test.py     # 8003 /flows

# 문법 점검 (전용 테스트 프레임워크 없음 - 검증은 스크립트/수동 HTTP로)
.\.venv\Scripts\python.exe -m py_compile app/<file>.py
```

정식 테스트 스위트가 없다. 변경 검증은 (1) `py_compile`/`node --check`, (2) 임시 포트에 서버 띄워 풀 HTTP 렌더 확인, (3) 실수집 1회(수동 `pipeline.run_collection()` 또는 `/api/collect`)로 한다. 프론트(app.js) 변경은 `node --check`.

## Non-obvious operational facts

- **새 지표 추가/배포 후 `run.py` 재시작 필수.** 실행 중인 프로세스는 기동 시점 config를 메모리에 들고 있어, `INDICATORS`에 지표를 추가해도 그 프로세스의 스케줄 수집은 옛 지표 집합만 수집한다(-> '최신 스냅샷'에서 신규 지표 누락). 수동 `pipeline.run_collection()`(새 import)은 최신 config라 정상.
- **서버 가동 중 수동 수집은 주의** - DB 락 경합으로 매우 느려진다.
- **콘솔 인코딩**: 한글 Windows는 cp949라 `₩`(U+20A9)·`—` 등이 콘솔 출력 시 깨지거나 크래시. 앱 로깅/웹 렌더는 utf-8이라 무관하지만, **테스트 스크립트는 로그 문구에 ASCII 구두점만 쓰거나 stdout을 utf-8로 reconfigure**해야 한다.
- **FRED**: 키 없으면 CSV 호스트(`fred.stlouisfed.org`), 키 있으면 공식 API(`api.stlouisfed.org`) 경유. 이 환경에서 CSV 호스트가 간헐 차단되므로 무료 `FRED_API_KEY` 설정이 안정적.
- **ECOS**(한국은행)**는 실키 필요**(공개 'sample' 키는 10건 제한). `ECOS_API_KEY` 없으면 한국 거시 일부만 비고, 단 한국 금리(`kr_10y`/`kr_3m`)는 FRED OECD 경유라 ECOS 없이도 동작.
- **브리핑 생성은 100% 출력(decode) 바운드**로 ~130-170s 소요(입력 프롬프트 크기는 무관 - prefill은 사실상 공짜). `LLM_TIMEOUT` 기본 300s. 프롬프트 트리밍으로는 가속되지 않으니 시도하지 말 것.
- **대량 DB 쓰기는 명시 트랜잭션 필수**: `db.py`가 `isolation_level=None`(autocommit)이라 `executemany`를 그냥 돌리면 행마다 커밋해 수십 초 걸린다. `upsert_history`/`save_observations`는 `BEGIN IMMEDIATE`/`COMMIT`로 묶여 있음(367x 차이).

## Architecture

데이터 흐름은 단방향: **collectors -> pipeline(파생·저장·브리핑) -> repository(SQLite) -> presenter(뷰모델) -> server(HTML/JSON) -> web(Chart.js)**.

### SSOT: 지표 레지스트리 (`app/config.py`)
전 계층이 공유하는 단일 출처. `INDICATORS: list[Indicator]`에 한 줄 추가하면 수집·저장·통계·화면에 자동 반영된다. `Indicator`의 핵심 필드:
- `source`: `"yahoo"` | `"fred"` | `"ecos"` | `"derived"` - 어느 수집기가 처리할지 결정.
- `derived=(base_key, op, key, op, key, ...)`: 파생지표. `pipeline._compute_derived`가 피연산 지표로부터 N항 가감/`*`/`/`로 계산(공통 날짜 교집합에서만). 예) 실질금리 = `us10y - us_be10y`, 순유동성 = `us_walcl - us_rrp - us_tga`.
- `transform="yoy"`: FRED/ECOS 지수 시리즈를 전년동월비 %로 변환.
- `scale`: 원시값 배율(FRED 단위 정규화 예 백만$->조$=1e-6; 비율 가독화 x1000). **FRED 단위가 시리즈마다 제각각이라 함정** - WALCL=백만$·RRP=십억$·TGA=백만$·M2=십억$.
- `freq` D/W/M: 신선도 경고·변화기준 라벨(전일/전주/전월)·통계 룩백 윈도우 게이팅에 사용.
- `ccy`: 비USD 자산의 호가 통화(원화 환산용). `priority_of()`/`source_tier()`/`so_what()`/`is_krw_convertible()`이 이 레지스트리 위에서 화면 강조·툴팁·환산 대상을 결정.

### Collectors (`app/collectors/`)
`Collector` ABC(`base.py`)를 상속. `collect()`는 **예외를 던지지 않고** `CollectResult` 반환 - `run()` 래퍼가 타이밍·예외를 잡아 항상 health가 채워진 결과를 보장한다(개별 소스 실패 격리의 핵심). `ALL_COLLECTORS`가 레지스트리. markets=Yahoo chart API, fred=FRED, ecos=한국은행, news=RSS, calendar=ForexFactory.

### Pipeline (`app/pipeline.py`)
`run_collection()`이 진입점. `_lock`(threading)으로 중복 실행 방지, `_status` dict로 API/스케줄러가 상태 공유. 5단계: `[1/5]` 수집기 병렬(ThreadPoolExecutor) -> `[2/5]` 파생 계산 -> `[3/5]` 저장 + 장애 에스컬레이션(`source_events`, 연속실패 1/3/6/12/24회에만 기록) -> `[4/5]` 브리핑 생성 -> `[5/5]` 저장 + 현대차 갱신 + prune. 예외 시 미완성 스냅샷(finished_utc=NULL)을 삭제해 고스트 방지. `start_scheduler()`는 UTC cron(`COLLECT_MINUTE`).

### Analysis (`app/analysis/`) - 결정층
수집과 분리된 순수 계산 모듈. 대부분 **추가 수집 없이** 저장된 시계열을 재활용:
- `llm.py`: **LLM provider 추상화**. `complete()`가 claude(`claude -p` 헤드리스 - 사용자 구독 인증, 키 불필요) / gemini(REST) 백엔드를 감춘다. `LLM_PROVIDER`로 선택. JSON 파싱·재시도·인용점검은 호출부(`briefing`) 몫이라 provider 교체에 영향 없음. **LLM 관련 작업 전 `/claude-api` 스킬 참고**(모델 ID·파라미터).
- `briefing.py`: 시황 브리핑 생성(우선순위 정렬·직전 delta·레짐·임박 발표 반영 + 출력 수치 자동검증 `_citation_audit`). 실패 시 presenter가 직전 캐시로 폴백.
- `regime.py`: 8~9개 신호(VIX·HY·NFCI·주가추세·섹터폭·달러·실질금리·장단기차·CFNAI) 가중합성 -> 0~100 위험선호 점수 + 5단계.
- `stats.py`: 백분위·z-score·모멘텀·`risk_metrics`(실현변동성·MDD·52주). `enrich()`가 freq/unit 게이트로 지표별 적용.
- `allocation.py`: 레짐 점수 -> 5단계 자산배분 프리셋(휴리스틱, 라이브 전용).
- `alerts.py`: 6~7개 위험 게이지 임계선 감시. 수집실패(ok=0)는 stale 오발화 방지 위해 `na` 처리.
- `overlays.py`: 섹터 상대강도·수익률곡선·크로스에셋 모멘텀(라이브 전용).
- `portfolio.py`: 보유액->원화환산 역사적 VaR·변동성. **보유정보 서버 미저장**(프론트 localStorage, POST로 계산만).
- `translate.py`: 뉴스 영문->한글(신규 항목만 배치 번역·캐시).
- `flows.py`: 자금 회전지도(RRG 좌표·순위 변동·LLM 해설, 별도 페이지 `/flows` + 대시보드 인라인 요약).
- `hyundai.py`/`shinhan.py`: 개별종목 분석(`/hyundai`·`/shinhan`). **둘은 수집·파싱·캐시 골격이 거의 동일한 사본** - presenter/config/DB를 우회하고 모듈 전역 메모리 캐시만 쓴다. 세 번째 종목을 추가하기 전에 공용 엔진으로 추출할 것.

### Persistence & views
- `db.py`/`repository.py`: SQLite 스키마 + 저장/조회. `_migrate()`가 멱등 컬럼 추가로 기존 DB 마이그레이션. 이력은 날짜가 아니라 **키별 최근 N개**(`HISTORY_POINTS`, 일별≈5년)로 보존.
- `presenter.py`: DB -> 화면/API 뷰모델 조립. **`snapshot_id`가 주어지면(과거 `/report/{id}`) 라이브 전용 요소(overlays·alerts·allocation·portfolio)는 None**으로 숨겨 시점 불일치 방지. 원화 환산 교차환율 계산도 여기.
- `server.py`: FastAPI 라우트. HTML(`/`, `/report/{id}`, `/hyundai`, `/shinhan`, `/flows`, `/history`) + JSON API(`/api/dashboard`·`/api/series`·`/api/regime`·`/api/alerts`·`/api/portfolio` POST 등) + `/api/collect` 수집 트리거. `lifespan`에서 스케줄러 시작 + `COLLECT_ON_START` 즉시 1회.
- `web/`: Jinja2 템플릿 + 정적(app.js/style.css). **서버사이드 렌더 우선(JS0)** - 대부분 화면은 Jinja로 그리고 JS는 차트·토글·포트폴리오 계산만.

## Config (.env, 전부 선택)
`COLLECT_MINUTE`(cron 분) · `LLM_PROVIDER`(claude|gemini) · `CLAUDE_MODEL`/`CLAUDE_BIN` · `GEMINI_API_KEY`/`GEMINI_MODEL` · `FRED_API_KEY` · `ECOS_API_KEY` · `HISTORY_POINTS`(백분위 룩백 상한 겸용) · `LLM_TIMEOUT`. `.env.example` 참고.
