# 거시경제 분석 수집기 + 대시보드

매시간 글로벌 시장·거시지표·뉴스·경제 캘린더를 자동 수집하고, **Claude Code(`claude -p`)** 가
그 데이터를 근거로 **거시 시황 브리핑**을 자동 작성하는 로컬 웹 대시보드.

> Anthropic API 키 없이 동작합니다. 이미 로그인된 Claude Code 인증을 그대로 사용합니다.

## 화면 구성
- **LLM 시황 브리핑** — 우선순위 정렬·다기간 모멘텀·직전 대비 변화·레짐·임박(±12h) 발표를 반영하고, 향후 시나리오(트리거→영향→관전지표)와 출력 수치 자동검증까지 포함한 한국어 분석(실패 시 직전 캐시 폴백)
- **지표 그리드** — 주가지수·환율·금리·원자재·암호화폐·미국/한국 거시 (변화율 + 스파크라인, 클릭 시 확대 차트). 비율지표는 %p, 변화 기준(전일/전월) 라벨, 오래된 데이터엔 신선도 경고
- **지표 심화 컨텍스트** — 역사적 백분위(%ile)·다기간 모멘텀(1M)·통계적 이상치(|z|≥3) 플래그·데이터 신뢰 구분(시장/공식/파생)
- **검색·필터·즐겨찾기·CSV 내보내기** — 지표 검색, 핵심/즐겨찾기 필터(localStorage), 표시 지표 CSV 다운로드
- **실질금리·기대인플레** — 명목 10년 − 10년 BEI(파생지표)로 실제 통화긴축 강도 표시
- **시장 레짐 + 자산간 상관 히트맵** — 리스크온/오프 자동 판정, 10개 자산 30일 롤링 상관
- **소스 상태 배지 + 운영 알림** — 정상/부분/오류/비활성 + 스케줄러 정지·지연·소스 연속실패 배너(이벤트 로그)
- **경제 뉴스 / 발표 일정** — RSS 헤드라인, 고·중요도 지표 발표 캘린더
- **이력** — 과거 스냅샷별 브리핑 다시 보기

## 데이터 소스 (모두 키 없이 동작, 일부는 키로 강화)
| 소스 | 키 | 내용 |
|---|---|---|
| Yahoo Finance | 불필요 | 지수·환율·금리·원자재·암호화폐 (시간별 핵심) |
| FRED | 선택 | 미 CPI·실업률·기준금리·장단기차·하이일드 스프레드 등 |
| 한국은행 ECOS | 선택 | 한국 기준금리·물가 (키 입력 시 활성) |
| RSS | 불필요 | WSJ·Investing·CNBC·MarketWatch 경제 헤드라인 |
| ForexFactory | 불필요 | 주간 경제지표 발표 일정 |

## 빠른 시작 (Windows)

```powershell
# 1) 의존성 설치 (.venv 사용)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2) (선택) 환경설정 — 키 없어도 동작
copy .env.example .env

# 3) 실행
.\.venv\Scripts\python.exe run.py
```

브라우저에서 <http://127.0.0.1:8000> 접속. 서버 시작 시 1회 즉시 수집하고,
이후 **매시 정각(.env 의 `COLLECT_MINUTE`)** 마다 자동 수집합니다.
상단의 **⟳ 지금 수집** 버튼으로 즉시 갱신할 수도 있습니다.

## 설정 (.env)
- `COLLECT_MINUTE` — 수집 분(예: `0`=매시 정각, `0,30`=30분마다)
- `ENABLE_LLM_BRIEFING` — LLM 브리핑 on/off
- `CLAUDE_MODEL` — 브리핑 모델(예: `claude-sonnet-4-6`). 비우면 CLI 기본값
- `CLAUDE_BIN` — claude 실행 파일 경로(비우면 PATH 자동 탐색)
- `FRED_API_KEY` / `ECOS_API_KEY` — 넣으면 해당 거시 소스 활성/강화
- `HISTORY_DAYS` — 스냅샷·뉴스 보관 일수
- `HISTORY_POINTS` — 차트 이력 보존 개수(키별 최근 N개; 월별 거시도 다년치 차트)

## 지표 추가/수정
`app/config.py` 의 `INDICATORS` 리스트에 한 줄 추가하면 수집·저장·화면에 자동 반영됩니다.

## 구조
```
run.py                  진입점 (uvicorn)
app/
  config.py             설정 + 지표 레지스트리(SSOT)
  db.py / repository.py  SQLite 스키마 + 저장/조회
  models.py             데이터 구조
  collectors/           markets·fred·ecos·news·calendar 수집기
  analysis/briefing.py  claude -p 헤드리스 브리핑 생성(직전 delta·레짐·임박 발표 반영)
  analysis/regime.py    자산간 상관 매트릭스 + 리스크온/오프 레짐 판정
  analysis/stats.py     역사적 백분위·z-score·다기간 모멘텀
  pipeline.py           수집 오케스트레이션 + APScheduler + 파생지표·장애 에스컬레이션
  presenter.py          DB → 화면/API 뷰모델 조립
  server.py             FastAPI 라우트(HTML + JSON API)
  logging_setup.py      콘솔+파일 로깅 설정
  web/                  templates + static (Chart.js 대시보드)
run.py                  진입점
verify_realenv.py       실네트워크 수집 점검 스크립트
data/economic.db        SQLite (자동 생성, .gitignore)
logs/app.log            수집 로그 (자동 생성, .gitignore)
```

## 동작 메모
- 모든 수집기는 **개별 실패 격리** — 한 소스가 죽어도 나머지는 정상 수집되고, 상태 배지로 표시됩니다.
- FRED 가 네트워크에서 막히면 해당 카드만 '수집 실패'로 표시되고 앱은 계속 동작합니다(키 입력 시 공식 API 사용).
- LLM 브리핑 실패 시에도 데이터 대시보드는 그대로 표시됩니다.
- 수집 진행상황은 콘솔과 `logs/app.log`(UTF-8, 자동 회전)에 단계별로 기록됩니다.
