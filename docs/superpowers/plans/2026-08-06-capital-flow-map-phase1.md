# 자금 회전지도 1단계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 저장된 시계열만 재가공해 RRG 회전지도 2종(자산군 10종 · 지역 6종)을 계산하고, 검증용 별도 페이지 `/flows`(포트 8003)에 렌더한다.

**Architecture:** `app/analysis/flows.py` 단일 모듈이 두 지도를 같은 `_rrg()` 함수로 만든다 — 자산 목록과 USD 환산 규칙만 주입한다. 추가 수집은 `tlt` 지표 1개뿐이고 나머지는 `repo.get_series_batch`로 기존 이력을 재활용한다. 1단계에서는 `presenter.py`를 건드리지 않고 `server.py`가 `flows`를 직접 호출한다(현대차·신한 페이지와 동일 패턴). 대시보드 합류는 2단계.

**Tech Stack:** Python 3 / FastAPI / Jinja2 / SQLite / Chart.js 4.4.6(이미 `base.html`에 로드됨)

## Global Constraints

- 원본 스펙: `docs/superpowers/specs/2026-08-06-capital-flow-map-design.md`
- **테스트 프레임워크 없음.** 이 저장소에는 pytest가 없다(CLAUDE.md). 검증은 `.venv` 파이썬으로 돌리는 단독 assert 스크립트 + `py_compile` + 실제 HTTP 렌더로 한다. pytest를 도입하지 말 것.
- 모든 명령은 `.\.venv\Scripts\python.exe` 로 실행한다.
- **콘솔 인코딩**: 한글 Windows는 cp949라 스크립트가 한글을 print 하면 크래시한다. 검증 스크립트 최상단에 반드시 `sys.stdout.reconfigure(encoding="utf-8")` 를 넣는다.
- 코드·주석·UI는 **한국어**.
- 기존 서버(8000)가 떠 있을 수 있다. **전체 수집(`pipeline.run_collection()`)을 실행하지 말 것** — DB 락 경합이 발생한다. `tlt` 백필은 단일 종목 스크립트로만 한다.
- 포트: 8000 대시보드 / 8001 현대차 / 8002 신한 / **8003 flows**
- 커밋은 각 Task 끝에서 한다. `main`에 미커밋 작업(현대차·신한)이 섞여 있으므로 **`git add`는 해당 Task가 만든 파일만 명시적으로 지정**한다. `git add -A` / `git add .` 금지.

## File Structure

| 파일 | 책임 |
| --- | --- |
| `app/analysis/flows.py` (신규) | RRG 좌표 계산, 순위 변동, 규칙 기반 요약. 두 지도 공용 |
| `scripts/verify_flows.py` (신규) | 기준 좌표 대조 assert 스크립트 (이 계획의 테스트) |
| `scripts/backfill_tlt.py` (신규) | TLT 5년치 이력만 단독 백필 |
| `app/config.py` (수정) | `tlt` 지표 1줄 추가 |
| `app/server.py` (수정) | `/flows` 라우트 추가 |
| `app/web/templates/flows.html` (신규) | 검증용 페이지 (표 → 차트 순으로 완성) |
| `app/web/static/flows.js` (신규) | Chart.js 산점도 2종 |
| `run_flows_test.py` (신규) | 포트 8003 테스트 서버 (스케줄러 off) |

---

### Task 1: RRG 코어 계산 + 기준 좌표 검증

두 지도의 좌표를 산출하는 심장부. `tlt`는 아직 수집 전이므로 **자산군 지도는 9종으로 시작**하고, 스펙 6절의 기준 좌표와 정확히 일치하는지로 검증한다.

**Files:**
- Create: `app/analysis/flows.py`
- Create: `scripts/verify_flows.py`

**Interfaces:**
- Consumes: `app.repository.get_series_batch(keys: list[str], points: int) -> dict[str, list[dict]]` — 각 dict는 `{"date": str, "value": float|None}`, 날짜 오름차순
- Produces:
  - `flows.asset_map() -> dict | None` / `flows.region_map() -> dict | None`
  - 반환 dict: `{"as_of": str, "days": int, "rows": list[dict]}`
  - `rows[i]`: `{"key": str, "label": str, "group": str, "group_label": str, "x": float, "y": float, "quadrant": str, "quadrant_prev": str, "tail": list[{"x","y","date"}]}`
  - `quadrant` 값은 `"주도" | "약화" | "개선" | "지체"` 중 하나

- [ ] **Step 1: 검증 스크립트(실패하는 테스트) 작성**

`scripts/verify_flows.py`:

```python
"""자금 회전지도 기준 좌표 검증.

스펙 docs/superpowers/specs/2026-08-06-capital-flow-map-design.md 6절의 기준 좌표와
구현 결과가 일치하는지 확인한다. 값이 어긋나면 계산 오류다.

실행:  .\.venv\Scripts\python.exe scripts/verify_flows.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")          # cp949 콘솔 크래시 방지
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import flows                     # noqa: E402

TOL = 0.02          # 부동소수 오차 허용치

# 기준일 2026-08-05. tlt 수집 전 9종 기준.
GOLDEN_ASSET = {
    "copper": (101.93, 100.62),
    "sp500":  (101.48, 99.48),
    "nasdaq": (101.43, 100.49),
    "gold":   (100.53, 102.99),
    "hyg":    (100.39, 98.88),
    "btc":    (99.48, 100.15),
    "kospi":  (99.43, 99.54),
    "usdkrw": (99.23, 97.38),
    "wti":    (99.01, 99.33),
}
GOLDEN_REGION = {
    "sp500":    (101.30, 100.19),
    "eustoxx":  (101.05, 100.61),
    "nikkei":   (101.00, 100.00),
    "hangseng": (100.23, 100.63),
    "kospi":    (99.16, 99.34),
    "shanghai": (98.69, 98.64),
}
GOLDEN_DAYS = {"자산군": 744, "지역": 915}

failures: list[str] = []


def check(name: str, m: dict | None, golden: dict, days: int) -> None:
    if m is None:
        failures.append(f"[{name}] 지도가 None — 데이터 부족 또는 계산 실패")
        return
    print(f"[{name}] as_of={m['as_of']}  거래일={m['days']}  자산={len(m['rows'])}개")
    if m["days"] != days:
        failures.append(f"[{name}] 공통 거래일 {m['days']} != 기준 {days}")
    got = {r["key"]: (r["x"], r["y"]) for r in m["rows"]}
    if set(got) != set(golden):
        failures.append(f"[{name}] 자산 구성 불일치: {sorted(got)} != {sorted(golden)}")
    for key, (gx, gy) in golden.items():
        if key not in got:
            failures.append(f"[{name}] {key} 누락")
            continue
        x, y = got[key]
        ok = abs(x - gx) <= TOL and abs(y - gy) <= TOL
        print(f"   {'OK  ' if ok else 'FAIL'} {key:9s} x={x:7.2f}(기준 {gx:7.2f})  "
              f"y={y:7.2f}(기준 {gy:7.2f})")
        if not ok:
            failures.append(f"[{name}] {key} 좌표 불일치 ({x:.2f},{y:.2f}) != ({gx},{gy})")
    for r in m["rows"]:
        if not r["tail"]:
            failures.append(f"[{name}] {r['key']} 꼬리 비어 있음")
        if r["quadrant"] not in ("주도", "약화", "개선", "지체"):
            failures.append(f"[{name}] {r['key']} 사분면 값 이상: {r['quadrant']}")


check("자산군", flows.asset_map(), GOLDEN_ASSET, GOLDEN_DAYS["자산군"])
print()
check("지역", flows.region_map(), GOLDEN_REGION, GOLDEN_DAYS["지역"])

print()
if failures:
    print(f"실패 {len(failures)}건")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("전부 통과")
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe scripts/verify_flows.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.analysis.flows'`

- [ ] **Step 3: `app/analysis/flows.py` 구현**

```python
"""자금 회전지도 — RRG(Relative Rotation Graph) 좌표 계산.

두 지도를 같은 로직으로 만든다:
  1) 자산군 회전 — 벤치마크 = 크로스에셋 동일가중 바스켓
  2) 지역 회전   — 벤치마크 = 글로벌 주식 동일가중 바스켓

추가 수집 없이 history 시계열만 재가공한다(repo.get_series_batch 재활용).
좌표 정의는 스펙 4.3절. 계산 기준통화는 USD 로 통일한다(4.2절) —
원화 기준은 채권을 환율 복제본으로 만들어 기각됐다.
"""
from __future__ import annotations

import math

from .. import repository as repo

_W_RS = 63        # 상대강도 정규화 창(3개월)
_W_MOM = 21       # 상대모멘텀 정규화 창(1개월)
_TAIL_STEP = 5    # 꼬리 샘플 간격(거래일 ≈ 1주)
_TAIL_N = 8       # 꼬리 점 개수(8주)
_POINTS = 1300    # DB 에서 끌어올 이력 길이
_MIN_DAYS = 130   # RRG 최소 요건(63 + 21 + 꼬리 여유)

# (지표키, 표시라벨, 자산군, 환산 환율키, 환산 연산) — 환산은 USD 기준 통일용
_ASSETS: list[tuple[str, str, str, str, str]] = [
    ("sp500",  "S&P500",   "equity", "", ""),
    ("nasdaq", "나스닥",     "equity", "", ""),
    ("kospi",  "코스피",     "equity", "usdkrw", "/"),
    ("hyg",    "하이일드채",  "bond",   "", ""),
    ("gold",   "금",        "real",   "", ""),
    ("copper", "구리",       "real",   "", ""),
    ("wti",    "WTI",      "real",   "", ""),
    ("btc",    "비트코인",    "crypto", "", ""),
    ("usdkrw", "원/달러",    "fx",     "", ""),
]

# 지역 지도. 홍콩(HKD)은 달러 페그라 상수 배율 → t0 정규화에서 약분되므로 환산 불필요.
_REGIONS: list[tuple[str, str, str, str, str]] = [
    ("sp500",    "미국",     "region", "", ""),
    ("kospi",    "한국",     "region", "usdkrw", "/"),
    ("eustoxx",  "유럽",     "region", "eurusd", "*"),
    ("shanghai", "중국본토",  "region", "usdcny", "/"),
    ("hangseng", "홍콩",     "region", "", ""),
    ("nikkei",   "일본",     "region", "usdjpy", "/"),
]

GROUP_LABEL = {"equity": "주식", "bond": "채권", "real": "실물",
               "crypto": "코인", "fx": "통화", "region": "지역"}

_QUADRANTS = ("주도", "약화", "개선", "지체")


def _quadrant(x: float, y: float) -> str:
    """RRG 사분면. 자금은 통상 시계방향으로 순환: 개선→주도→약화→지체."""
    if x >= 100.0:
        return "주도" if y >= 100.0 else "약화"
    return "개선" if y >= 100.0 else "지체"


def _norm(vals: list[float], win: int) -> list[float | None]:
    """롤링 z-정규화: 100 + (x − 평균) / 표준편차(모집단). 창이 안 차면 None."""
    out: list[float | None] = [None] * len(vals)
    for i in range(win - 1, len(vals)):
        w = vals[i - win + 1:i + 1]
        mean = sum(w) / win
        sd = math.sqrt(sum((v - mean) ** 2 for v in w) / win)
        out[i] = 100.0 + (vals[i] - mean) / sd if sd else 100.0
    return out


def _usd_prices(spec, S, dates) -> dict[str, list[float]]:
    """USD 기준 가격열. 환율키가 있으면 지정 연산으로 환산."""
    out: dict[str, list[float]] = {}
    for key, _, _, fx_key, op in spec:
        if not fx_key:
            out[key] = [S[key][d] for d in dates]
        elif op == "/":
            out[key] = [S[key][d] / S[fx_key][d] for d in dates]
        else:
            out[key] = [S[key][d] * S[fx_key][d] for d in dates]
    return out


def _rrg(spec) -> dict | None:
    """자산 목록 → RRG 좌표 묶음. 데이터 부족·결측 시 None."""
    keys = {k for k, _, _, _, _ in spec}
    keys |= {f for _, _, _, f, _ in spec if f}
    raw = repo.get_series_batch(sorted(keys), _POINTS)
    S = {k: {p["date"]: p["value"] for p in raw.get(k, []) if p.get("value") is not None}
         for k in keys}
    if any(not S[k] for k in keys):
        return None

    # 공통 거래일 교집합에서만 계산(휴장일·주말 불일치 제거)
    dates = [d for d in sorted(S[spec[0][0]]) if all(S[k].get(d) is not None for k in keys)]
    if len(dates) < _MIN_DAYS:
        return None

    price = _usd_prices(spec, S, dates)
    order = [k for k, _, _, _, _ in spec]
    # t0 = 교집합 첫 날을 100 으로 정규화(전 자산 동일 기준일)
    N = {k: [100.0 * v / price[k][0] for v in price[k]] for k in order}
    B = [sum(N[k][i] for k in order) / len(order) for i in range(len(dates))]

    X: dict[str, list[float | None]] = {}
    Y: dict[str, list[float | None]] = {}
    for k in order:
        rs = [100.0 * N[k][i] / B[i] for i in range(len(dates))]
        r = _norm(rs, _W_RS)
        # 모멘텀은 RS-Ratio 위에 다시 정규화한다. 미확정 구간을 100 으로 채운 뒤
        # 마스킹하는 이 순서 그대로 기준 좌표가 산출됐다 — 순서를 바꾸면 값이 달라진다.
        m = _norm([v if v is not None else 100.0 for v in r], _W_MOM)
        X[k] = r
        Y[k] = [None if r[i] is None else m[i] for i in range(len(r))]

    last = len(dates) - 1
    rows = []
    for key, label, group, _, _ in spec:
        tail = []
        for j in range(_TAIL_N - 1, -1, -1):
            i = last - _TAIL_STEP * j
            if i < 0 or X[key][i] is None or Y[key][i] is None:
                continue
            tail.append({"x": round(X[key][i], 3), "y": round(Y[key][i], 3), "date": dates[i]})
        if not tail:
            continue
        cur = tail[-1]
        rows.append({
            "key": key, "label": label, "group": group,
            "group_label": GROUP_LABEL.get(group, group),
            "x": cur["x"], "y": cur["y"],
            "quadrant": _quadrant(cur["x"], cur["y"]),
            "quadrant_prev": _quadrant(tail[0]["x"], tail[0]["y"]),
            "tail": tail,
        })
    if not rows:
        return None
    rows.sort(key=lambda r: -r["x"])
    return {"as_of": dates[last], "days": len(dates), "rows": rows}


def asset_map() -> dict | None:
    """지도 1 — 자산군 회전(벤치마크 = 크로스에셋 동일가중)."""
    return _rrg(_ASSETS)


def region_map() -> dict | None:
    """지도 2 — 지역 회전(벤치마크 = 글로벌 주식 동일가중)."""
    return _rrg(_REGIONS)
```

- [ ] **Step 4: 검증 통과 확인**

Run: `.\.venv\Scripts\python.exe scripts/verify_flows.py`
Expected: PASS — 마지막 줄에 `전부 통과`. 자산군 744일/9종, 지역 915일/6종.

실패하면 좌표를 하나씩 대조한다. 흔한 원인 두 가지:
- 교집합 날짜가 다름 → `days` 불일치로 먼저 드러난다
- `_norm` 을 표본표준편차로 계산 → 기준값은 **모집단** 표준편차(`/ win`)다

- [ ] **Step 5: 문법 점검**

Run: `.\.venv\Scripts\python.exe -m py_compile app/analysis/flows.py scripts/verify_flows.py`
Expected: 출력 없음(성공)

- [ ] **Step 6: 커밋**

```bash
git add app/analysis/flows.py scripts/verify_flows.py
git commit -m "feat(flows): RRG 회전지도 좌표 계산 + 기준 좌표 검증 스크립트

자산군 9종/지역 6종 두 지도를 같은 _rrg() 로 계산. USD 기준 통일,
공통 거래일 교집합, 8주 꼬리. 스펙 6절 기준 좌표와 일치 확인.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `tlt` 지표 추가 + 백필 + 자산군 지도 10종 전환

채권 축의 방어 극(순수 금리)을 넣는다. 현 DB 국채는 전부 *수익률*이라 RRG에 못 쓴다.

**Files:**
- Create: `scripts/backfill_tlt.py`
- Modify: `app/config.py` (INDICATORS 리스트, `lqd` 줄 바로 뒤)
- Modify: `app/analysis/flows.py` (`_ASSETS`)
- Modify: `scripts/verify_flows.py` (기준 좌표 갱신)

**Interfaces:**
- Consumes: `app.collectors.markets._fetch_symbol(symbol: str, rng: str) -> dict`, `app.collectors.markets._parse(symbol, raw) -> (price, prev, as_of, history)`, `app.repository.upsert_history(quotes: list[Quote])`, `app.models.Quote`
- Produces: `history` 테이블에 `key="tlt"` 5년치 행

- [ ] **Step 1: `app/config.py` 에 지표 추가**

`app/config.py` 의 `Indicator("lqd", ...)` 줄 **바로 다음**에 삽입한다:

```python
    Indicator("tlt",   "미 장기국채 ETF(TLT)", "rate", "yahoo", "TLT", unit="$", decimals=2, up_is_good=None,
              note="미 20년+ 국채 ETF. 금리 하락(=채권 강세) 시 상승 — 회전지도의 방어 극"),
```

- [ ] **Step 2: 백필 스크립트 작성**

`scripts/backfill_tlt.py`:

```python
"""TLT 이력만 단독 백필.

테스트 서버(8003)는 스케줄러가 꺼져 있어 tlt 를 수집하지 않는다.
전체 run_collection() 은 8000 서버와 DB 락 경합을 일으키므로
이 한 종목만 받아서 history 에 넣는다.

실행:  .\.venv\Scripts\python.exe scripts/backfill_tlt.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import repository as repo               # noqa: E402
from app.collectors.markets import _fetch_symbol, _parse   # noqa: E402
from app.models import Quote                     # noqa: E402

SYMBOL = "TLT"
KEY = "tlt"

print(f"{SYMBOL} 5y 이력 요청 중...")
raw = _fetch_symbol(SYMBOL, "5y")
price, prev, as_of, history = _parse(SYMBOL, raw)
if not history:
    print("실패: 이력이 비어 있음")
    sys.exit(1)

print(f"수신 {len(history)}개  {history[0][0]} ~ {history[-1][0]}  최신가 {price}")
repo.upsert_history([Quote(key=KEY, value=price, prev_close=prev, as_of=as_of,
                           history=history, ok=True)])

rows = repo.get_series_batch([KEY], 2000)[KEY]
print(f"DB 저장 확인: {len(rows)}개  {rows[0]['date']} ~ {rows[-1]['date']}")
if len(rows) < 1000:
    print("경고: 5년치(약 1250개)에 못 미친다. Yahoo 응답을 확인할 것")
    sys.exit(1)
print("완료")
```

- [ ] **Step 3: 백필 실행**

Run: `.\.venv\Scripts\python.exe scripts/backfill_tlt.py`
Expected: `수신 12xx개` → `DB 저장 확인: 12xx개` → `완료`

- [ ] **Step 4: 기존 대시보드 회귀 확인**

지표를 추가했으므로 최신 스냅샷에 `tlt` 관측이 없는 상태에서 메인 화면이 깨지지 않는지 본다.

Run: `.\.venv\Scripts\python.exe -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); from app import presenter; d=presenter.build_dashboard(); print('OK empty=', d['empty'], 'groups=', len(d['groups']))"`
Expected: 예외 없이 `OK empty= False groups= <숫자>` 출력

> `build_dashboard()` 가 돌려주는 키는 `groups`/`snapshot`/`overlays` 등이다(`cards` 는 없다).
> 여기서 예외가 나면 새 지표가 관측 없는 상태를 어딘가에서 전제하고 있다는 뜻이므로 그 지점을 고친다.

- [ ] **Step 5: `flows.py` 를 10종으로 전환**

`_ASSETS` 리스트에서 `("kospi", ...)` 줄 다음에 삽입한다:

```python
    ("tlt",    "미 장기국채",  "bond",   "", ""),
```

- [ ] **Step 6: 새 기준 좌표 산출**

10종으로 바뀌면 바스켓이 달라져 좌표가 전부 이동한다. 실제 값을 뽑아 기준으로 삼는다.

임시 파일을 저장소 루트에 만든다:

```python
# scratch_golden.py (커밋하지 않는다)
import sys
sys.stdout.reconfigure(encoding="utf-8")
from app.analysis import flows
m = flows.asset_map()
print("days =", m["days"], " as_of =", m["as_of"])
for r in m["rows"]:
    print(f'    "{r["key"]}": ({r["x"]:.2f}, {r["y"]:.2f}),   # {r["label"]} {r["quadrant"]}')
```

Run: `.\.venv\Scripts\python.exe scratch_golden.py`

- [ ] **Step 7: `verify_flows.py` — 독립 오라클 보존 + 회귀 기준 갱신**

> **중요.** Step 6 출력으로 기준값을 덮어쓰기만 하면, 기준값이 구현 자신의 출력이 되어
> **독립적인 검증력이 사라진다**(테스트를 코드에 맞추는 것). 스펙 문서에서 나온 9종
> 기준 좌표는 영구 오라클로 남기고, 10종 값은 회귀 잠금으로 따로 둔다.

먼저 9종 오라클을 **영구 검사로 승격**한다. `GOLDEN_ASSET` 딕셔너리의 이름을
`GOLDEN_ASSET_9` 로 바꾸고(값은 그대로 둔다) 주석을 이렇게 단다:

```python
# 스펙 문서에서 나온 독립 오라클 — tlt 를 뺀 9종. 구현 출력으로 덮어쓰지 말 것.
# 이 값이 깨지면 _rrg 의 계산이 바뀐 것이다.
GOLDEN_ASSET_9 = {
```

`GOLDEN_DAYS` 도 `{"자산군9": 744, "자산군": <Step 6 의 days>, "지역": 915}` 로 바꾼다.

Step 6 출력을 새 `GOLDEN_ASSET` 에 붙여넣는다:

```python
# 회귀 잠금 — tlt 포함 10종. Task 2 구현 시점 출력을 고정한 값이라
# 독립 검증력은 없다(계산 검증은 GOLDEN_ASSET_9 담당). 좌표가 흔들리면 여기서 잡힌다.
GOLDEN_ASSET = {
```

`check("자산군", flows.asset_map(), ...)` 호출 **앞**에 9종 오라클 검사를 추가한다:

```python
# tlt 를 뺀 9종으로 계산해 스펙 기준 좌표와 대조 — 계산 로직의 독립 검증
_spec9 = [s for s in flows._ASSETS if s[0] != "tlt"]
check("자산군9(오라클)", flows._rrg(_spec9), GOLDEN_ASSET_9, GOLDEN_DAYS["자산군9"])
print()
```

`check(...)` 안의 `GOLDEN_DAYS["자산군"]` 참조는 그대로 두고, 지역 지도 값은 건드리지 않는다(`tlt` 와 무관).

- [ ] **Step 8: 검증 통과 확인**

Run: `.\.venv\Scripts\python.exe scripts/verify_flows.py`
Expected: PASS — 자산군 10종, 지역 6종. `전부 통과`

- [ ] **Step 9: `tlt` 만성 겹침 재측정 (스펙 6절 검증 5번)**

`tlt` 는 미검증 자산이다. `hyg`·`usdkrw` 와 30% 초과로 겹치면 구성을 재검토해야 한다.

```python
# scratch_overlap.py (커밋하지 않는다)
import sys, math
sys.stdout.reconfigure(encoding="utf-8")
from app.analysis import flows

m = flows.asset_map()
rows = m["rows"]
# 꼬리 8점을 표본으로 쌍별 거리 비교(그날 점구름 지름 대비 8% 미만 = 겹침)
n = min(len(r["tail"]) for r in rows)
bad = []
for i in range(n):
    pts = {r["key"]: (r["tail"][i]["x"], r["tail"][i]["y"]) for r in rows}
    diam = max(math.dist(a, b) for a in pts.values() for b in pts.values())
    for ka in pts:
        for kb in pts:
            if ka < kb and math.dist(pts[ka], pts[kb]) < diam * 0.08:
                bad.append((ka, kb))
from collections import Counter
for (a, b), c in Counter(bad).most_common(8):
    print(f"{a:8s} ~ {b:8s} {c}/{n} 회 겹침 ({c/n*100:.0f}%)")
```

Run: `.\.venv\Scripts\python.exe scratch_overlap.py`
Expected: `tlt` 가 낀 쌍이 8주 중 3회(약 30%)를 넘지 않을 것.

> 8주 표본은 통계적으로 얇다. 30%를 넘으면 즉시 구성을 바꾸지 말고, 스펙 4.1절의 만성 겹침 측정(전 기간)을 다시 돌려 확인한 뒤 판단한다. 결과를 스펙 9절 미해결 항목에 기록한다.

- [ ] **Step 10: 임시 파일 정리 + 커밋**

```bash
rm -f scratch_golden.py scratch_overlap.py
git add app/config.py app/analysis/flows.py scripts/backfill_tlt.py scripts/verify_flows.py
git commit -m "feat(flows): tlt 지표 추가 + 5년치 백필, 자산군 지도 10종 전환

현 DB 국채는 전부 수익률이라 RRG 불가 -> TLT(20년+ 국채 ETF)로 방어 극 확보.
전체 수집은 DB 락 경합이 있어 단일 종목 백필 스크립트로 처리.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 위험 성격(색) + 순위 변동표 + 규칙 기반 요약

지도에 얹을 해석 요소 3종. 색은 연속값이라 임계선이 없다(스펙 4.4절 — 3단계 분류는 경계 자산이 룩백에 따라 깜빡여 폐기).

**Files:**
- Modify: `app/analysis/flows.py`
- Modify: `scripts/verify_flows.py`

**Interfaces:**
- Produces:
  - `flows.build_flows() -> dict` — `{"asset": dict|None, "region": dict|None, "ranks": list[dict], "summary": dict|None}`
  - `asset` 지도의 각 `rows[i]`에 `risk: float` 추가(음수=공격, 양수=방어). VIX 급등일 평균수익률(%)
  - `ranks[i]`: `{"key","label","now","prev","shift","m3"}` — `shift`는 `prev − now`(양수면 순위 상승)
  - `summary`: `{"into": list[str], "out": list[str], "text": str}`

- [ ] **Step 1: 검증 스크립트에 기대 항목 추가**

`scripts/verify_flows.py` 의 `check(...)` 호출 두 줄 **뒤**, `print()` 와 `if failures:` 사이에 삽입:

```python
# ── Task 3 요소 검증 ──
bundle = flows.build_flows()
print()
print(f"[묶음] asset={'있음' if bundle['asset'] else '없음'} "
      f"region={'있음' if bundle['region'] else '없음'} "
      f"ranks={len(bundle['ranks'])}개")

if bundle["asset"]:
    missing = [r["key"] for r in bundle["asset"]["rows"] if r.get("risk") is None]
    if missing:
        failures.append(f"[위험성격] risk 누락: {missing}")
    else:
        for r in sorted(bundle["asset"]["rows"], key=lambda r: r["risk"]):
            print(f"   risk {r['key']:9s} {r['risk']:+7.3f}%")
        # 방향 상식 검증: 비트코인은 공격(음수), 원/달러는 방어(양수)여야 한다
        by = {r["key"]: r["risk"] for r in bundle["asset"]["rows"]}
        if by.get("btc", 0) >= 0:
            failures.append(f"[위험성격] btc 가 공격(음수)이 아님: {by.get('btc')}")
        if by.get("usdkrw", 0) <= 0:
            failures.append(f"[위험성격] usdkrw 가 방어(양수)가 아님: {by.get('usdkrw')}")

if not bundle["ranks"]:
    failures.append("[순위표] 비어 있음")
else:
    for r in bundle["ranks"][:5]:
        print(f"   rank {r['label']:10s} {r['prev']:2d}위 -> {r['now']:2d}위 ({r['shift']:+d})")
    shifts = {r["key"]: r["now"] for r in bundle["ranks"]}
    if len(shifts) != len(bundle["ranks"]):
        failures.append("[순위표] key 중복")
    if sorted(r["now"] for r in bundle["ranks"]) != list(range(1, len(bundle["ranks"]) + 1)):
        failures.append("[순위표] now 순위가 1..N 연속이 아님")

if not bundle["summary"] or not bundle["summary"].get("text"):
    failures.append("[요약] text 없음")
else:
    print(f"   요약: {bundle['summary']['text']}")
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe scripts/verify_flows.py`
Expected: FAIL — `AttributeError: module 'app.analysis.flows' has no attribute 'build_flows'`

- [ ] **Step 3: `flows.py` 에 위험 성격 계산 추가**

파일 상단 상수 블록의 `_QUADRANTS` 줄 아래에 추가:

```python
_VIX_KEY = "vix"
_VIX_SHOCK_PCT = 0.10     # VIX 상위 10% 급등일을 '위험 국면'으로 본다
```

`_rrg` 함수 **아래**에 추가:

```python
def _log_returns(vals: list[float]) -> list[float]:
    return [math.log(b / a) if a > 0 and b > 0 else 0.0 for a, b in zip(vals, vals[1:])]


def _risk_scores(spec, S, dates) -> dict[str, float]:
    """위험 성격 — VIX 급등일(상위 10%) 평균 수익률 %. 음수=공격, 양수=방어.

    임계선으로 3분류하지 않고 연속값 그대로 쓴다(스펙 4.4절):
    경계 자산이 룩백에 따라 색이 깜빡이는 것을 원천 차단한다.
    """
    vix_raw = repo.get_series_batch([_VIX_KEY], _POINTS).get(_VIX_KEY) or []
    vix = {p["date"]: p["value"] for p in vix_raw if p.get("value") is not None}
    common = [d for d in dates if d in vix]
    if len(common) < _W_RS:
        return {}
    vr = _log_returns([vix[d] for d in common])
    k = max(1, int(len(vr) * _VIX_SHOCK_PCT))
    shock = sorted(range(len(vr)), key=lambda i: -vr[i])[:k]

    price = _usd_prices(spec, S, common)
    out: dict[str, float] = {}
    for key, _, _, _, _ in spec:
        r = _log_returns(price[key])
        vals = [r[i] for i in shock if i < len(r)]
        if vals:
            out[key] = round(sum(vals) / len(vals) * 100.0, 3)
    return out
```

`_rrg` 안에서 `rows.sort(...)` **바로 앞**에 두 줄을 넣어 risk 를 붙인다:

```python
    risk = _risk_scores(spec, S, dates)
    for r in rows:
        r["risk"] = risk.get(r["key"])
```

- [ ] **Step 4: 순위 변동표 + 요약 + 묶음 추가**

`flows.py` 맨 아래(`region_map()` 뒤)에 추가:

```python
# 순위표 대상 — 지도에 없는 자산까지 넓게 본다(지도는 좁게, 표는 넓게).
_RANK_KEYS: list[tuple[str, str]] = [
    ("sp500", "S&P500"), ("nasdaq", "나스닥"), ("kospi", "코스피"),
    ("eustoxx", "유럽"), ("nikkei", "일본"), ("hangseng", "홍콩"), ("shanghai", "중국본토"),
    ("tlt", "미 장기국채"), ("hyg", "하이일드채"), ("lqd", "투자등급채"),
    ("gold", "금"), ("silver", "은"), ("copper", "구리"), ("wti", "WTI"),
    ("btc", "비트코인"), ("eth", "이더리움"), ("usdkrw", "원/달러"), ("dxy", "달러인덱스"),
]
_RANK_LOOKBACK = 63       # 3개월 모멘텀
_MIN_RANK_DAYS = _RANK_LOOKBACK * 2 + 5


def rank_shift() -> list[dict]:
    """3개월 모멘텀 순위 — 현재와 63거래일 전을 비교해 ▲▼ 를 낸다.

    각 자산의 자기 이력만 쓰므로(교집합 불필요) 지도보다 대상을 넓게 잡을 수 있다.
    """
    keys = [k for k, _ in _RANK_KEYS]
    raw = repo.get_series_batch(keys, _POINTS)
    now_m, prev_m = {}, {}
    for key, _ in _RANK_KEYS:
        vals = [p["value"] for p in raw.get(key, []) if p.get("value") is not None]
        if len(vals) < _MIN_RANK_DAYS:
            continue
        base_now = vals[-1 - _RANK_LOOKBACK]
        base_prev = vals[-1 - _RANK_LOOKBACK * 2]
        if not base_now or not base_prev:
            continue
        now_m[key] = (vals[-1] / base_now - 1.0) * 100.0
        prev_m[key] = (vals[-1 - _RANK_LOOKBACK] / base_prev - 1.0) * 100.0
    if not now_m:
        return []

    def ranked(d: dict[str, float]) -> dict[str, int]:
        order = sorted(d, key=lambda k: -d[k])
        return {k: i + 1 for i, k in enumerate(order)}

    rn, rp = ranked(now_m), ranked(prev_m)
    label = dict(_RANK_KEYS)
    rows = [{"key": k, "label": label[k], "now": rn[k], "prev": rp[k],
             "shift": rp[k] - rn[k], "m3": round(now_m[k], 2)} for k in rn]
    rows.sort(key=lambda r: r["now"])
    return rows


def _summary(m: dict | None) -> dict | None:
    """사분면 진입/이탈을 규칙으로 문장화. LLM 미사용(브리핑 지연 없음)."""
    if not m:
        return None
    into = [r["label"] for r in m["rows"]
            if r["quadrant"] == "주도" and r["quadrant_prev"] != "주도"]
    out = [r["label"] for r in m["rows"]
           if r["quadrant"] in ("약화", "지체") and r["quadrant_prev"] in ("주도", "개선")]
    if into and out:
        text = f"지난 8주 {' · '.join(out)}에서 {' · '.join(into)}(으)로 자금이 이동했습니다."
    elif into:
        text = f"지난 8주 {' · '.join(into)}(이)가 새로 주도권을 잡았습니다."
    elif out:
        text = f"지난 8주 {' · '.join(out)}에서 자금이 빠졌습니다."
    else:
        text = "지난 8주 사분면 이동이 없었습니다 — 기존 흐름이 유지되고 있습니다."
    return {"into": into, "out": out, "text": text}


def build_flows() -> dict:
    """페이지용 묶음. 개별 실패는 None/빈 리스트로 격리해 전체를 무력화하지 않는다."""
    def safe(fn, fallback):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return fallback

    asset = safe(asset_map, None)
    return {
        "asset": asset,
        "region": safe(region_map, None),
        "ranks": safe(rank_shift, []),
        "summary": _summary(asset),
    }
```

- [ ] **Step 5: 검증 통과 확인**

Run: `.\.venv\Scripts\python.exe scripts/verify_flows.py`
Expected: PASS. 위험 성격에서 `btc` 가 가장 음수, `usdkrw`/`wti` 가 양수. 순위표 18종 내외. 요약 문장 출력.

> `usdkrw` 가 양수가 아니면 실패한다. 스펙 4.4절 실측에서 `usdkrw +0.251%`, `btc −1.880%` 였다. 부호가 뒤집히면 `_log_returns` 나 shock 인덱스 정렬 방향을 확인한다(VIX **상위** 10%여야 하므로 `-vr[i]` 로 내림차순).

- [ ] **Step 6: 커밋**

```bash
git add app/analysis/flows.py scripts/verify_flows.py
git commit -m "feat(flows): 위험성격 연속값 + 순위 변동표 + 규칙 기반 요약

색은 VIX 급등일 민감도 연속 스케일 — 3단계 분류는 경계 자산이 룩백에 따라
깜빡여 폐기(스펙 4.4). 요약은 사분면 전이 규칙 기반이라 LLM 지연 없음.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `/flows` 라우트 + 페이지(표) + 8003 테스트 서버

먼저 **차트 없이 서버사이드 표만** 렌더해 데이터가 화면까지 도달하는 것을 확인한다. 차트는 Task 5.

**Files:**
- Modify: `app/server.py`
- Create: `app/web/templates/flows.html`
- Create: `run_flows_test.py`

**Interfaces:**
- Consumes: `flows.build_flows()` (Task 3), `templates.TemplateResponse` 패턴은 `app/server.py:85` `hyundai_page` 와 동일
- Produces: `GET /flows` → HTTP 200 HTML

- [ ] **Step 1: `app/server.py` 에 import 추가**

`from .analysis import ...` 계열 import 가 모인 곳에 `flows` 를 추가한다. 기존 import 블록에서 `hyundai as hyundai_mod` 를 찾아 그 근처에 삽입:

```python
from .analysis import flows as flows_mod
```

- [ ] **Step 2: 라우트 추가**

`app/server.py` 의 `shinhan_page` 함수 **뒤**에 삽입:

```python
@app.get("/flows", response_class=HTMLResponse)
def flows_page(request: Request):
    """자금 회전지도 — 1단계 검증용 별도 페이지(2단계에서 대시보드로 이관)."""
    return templates.TemplateResponse("flows.html", {
        "request": request,
        "f": flows_mod.build_flows(),
        "title": "자금 회전지도",
    })
```

- [ ] **Step 3: 템플릿 작성**

`app/web/templates/flows.html`:

```html
{% extends "base.html" %}
{% block content %}

<header class="topbar">
  <div class="brand">
    <span class="logo">🧭</span>
    <div>
      <h1>자금 회전지도</h1>
      <div class="sub">
        어디서 어디로 돈이 옮겨가는 중인가
        <span class="pill neutral" style="font-size:10px">검증용 · 8003</span>
      </div>
    </div>
  </div>
  <div class="actions">
    <a class="btn ghost" href="/">← 메인 대시보드</a>
    <button class="btn primary" onclick="location.reload()">⟳ 새로고침</button>
  </div>
</header>

{% if f.summary %}
<section class="card" style="margin-top:16px">
  <h2 style="margin:0 0 6px">지금 무슨 일이 일어나는가</h2>
  <p style="font-size:15px;margin:0">{{ f.summary.text }}</p>
</section>
{% endif %}

{% macro rrg_table(m, title, note) %}
<section class="card" style="margin-top:16px">
  <h2 style="margin:0 0 4px">{{ title }}</h2>
  {% if m %}
  <p class="muted" style="margin:0 0 10px;font-size:12px">
    기준 {{ m.as_of }} · 공통 거래일 {{ m.days }}일 · {{ note }}
  </p>
  <table class="tbl" style="width:100%">
    <thead>
      <tr>
        <th style="text-align:left">자산</th><th>분류</th>
        <th>상대강도<br><span class="muted">3개월 위치</span></th>
        <th>상대모멘텀<br><span class="muted">1개월 방향</span></th>
        <th>사분면</th><th>8주 전</th>
        {% if m.rows and m.rows[0].risk is not none %}<th>위험성격</th>{% endif %}
      </tr>
    </thead>
    <tbody>
      {% for r in m.rows %}
      <tr>
        <td style="text-align:left"><strong>{{ r.label }}</strong></td>
        <td class="muted">{{ r.group_label }}</td>
        <td class="mono">{{ '%.2f'|format(r.x) }}</td>
        <td class="mono">{{ '%.2f'|format(r.y) }}</td>
        <td>
          <span class="pill {{ 'good' if r.quadrant in ('주도','개선') else 'bad' }}">{{ r.quadrant }}</span>
        </td>
        <td class="muted">{{ r.quadrant_prev }}{% if r.quadrant != r.quadrant_prev %} →{% endif %}</td>
        {% if r.risk is not none %}
        <td class="mono {{ 'bad' if r.risk < 0 else 'good' }}">{{ '%+.2f'|format(r.risk) }}%</td>
        {% endif %}
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="muted">데이터가 부족해 계산할 수 없습니다(최소 130 거래일 필요).</p>
  {% endif %}
</section>
{% endmacro %}

{{ rrg_table(f.asset, '지도 1 — 자산군 회전', '주식이냐 채권이냐 금이냐') }}
{{ rrg_table(f.region, '지도 2 — 지역 회전', '주식 안에서 어느 나라냐') }}

{% if f.ranks %}
<section class="card" style="margin-top:16px">
  <h2 style="margin:0 0 10px">3개월 모멘텀 순위 변동</h2>
  <table class="tbl" style="width:100%">
    <thead><tr><th style="text-align:left">자산</th><th>3개월 전</th><th>현재</th><th>변동</th><th>3개월 수익률</th></tr></thead>
    <tbody>
      {% for r in f.ranks %}
      <tr>
        <td style="text-align:left">{{ r.label }}</td>
        <td class="muted mono">{{ r.prev }}위</td>
        <td class="mono"><strong>{{ r.now }}위</strong></td>
        <td class="mono {{ 'good' if r.shift > 0 else ('bad' if r.shift < 0 else 'muted') }}">
          {% if r.shift > 0 %}▲{{ r.shift }}{% elif r.shift < 0 %}▼{{ -r.shift }}{% else %}—{% endif %}
        </td>
        <td class="mono {{ 'good' if r.m3 > 0 else 'bad' }}">{{ '%+.2f'|format(r.m3) }}%</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}

<section class="card" style="margin-top:16px">
  <h2 style="margin:0 0 8px">이 지도가 말하지 않는 것</h2>
  <p class="muted" style="margin:0 0 6px">
    가격 상대강도는 <strong>실제 자금유입이 아니라 그 대리지표</strong>입니다.
    사모대출에 1조 달러가 들어가도 관련 상장 자산 가격은 안 움직일 수 있습니다.
    진짜 펀드플로우(ETF 설정·환매, EPFR)는 유료라 이 앱에서 쓸 수 없습니다.
  </p>
  <p class="muted" style="margin:0">
    <strong>관측 불가 영역</strong> — 사모 금융(Private Credit·PE·VC)은 비공개·분기지연·유료라,
    토큰증권(STO)·미술품·IP는 공개 시계열이 없어 이 지도에 담기지 않습니다.
  </p>
</section>

{% endblock %}
```

- [ ] **Step 4: 테스트 서버 작성**

`run_flows_test.py`:

```python
"""자금 회전지도 테스트 서버 — 기존 대시보드(8000)·현대차(8001)·신한(8002)과 공존.

스케줄러·자동수집 모두 비활성 -> SQLite 충돌 없음.

실행:  python run_flows_test.py
접속:  http://127.0.0.1:8003/flows
"""
from __future__ import annotations

import os

# pydantic-settings 가 읽기 전에 env 설정
os.environ.setdefault("COLLECT_ON_START", "false")

# ── 스케줄러·수집 전체 비활성 (기존 서버와 공존) ──────────────────────────
from app import pipeline as _pl  # noqa: E402

_pl.start_scheduler    = lambda: type("_FakeSched", (), {"running": False})()
_pl.shutdown_scheduler = lambda: None
_pl.trigger_async      = lambda: False

# ── 서버 기동 ─────────────────────────────────────────────────────────────
import uvicorn  # noqa: E402

PORT = 8003

print()
print("=" * 54)
print("  Capital Flow Map test server")
print(f"  -> http://127.0.0.1:{PORT}/flows")
print()
print("  - dashboard(8000)/hyundai(8001)/shinhan(8002) keep running")
print("  - scheduler/auto-collect disabled (no DB conflict)")
print("  Stop: Ctrl+C")
print("=" * 54)
print()

uvicorn.run("app.server:app", host="127.0.0.1", port=PORT, reload=False, log_level="info")
```

> 콘솔 문구는 ASCII 만 쓴다 — cp949 환경에서 한글·특수문자 print 가 크래시한다(CLAUDE.md).

- [ ] **Step 5: 문법 점검**

Run: `.\.venv\Scripts\python.exe -m py_compile app/server.py run_flows_test.py`
Expected: 출력 없음

- [ ] **Step 6: 서버 기동 + 실제 렌더 확인**

터미널 A: `.\.venv\Scripts\python.exe run_flows_test.py`

터미널 B:
```bash
.\.venv\Scripts\python.exe -c "import sys,urllib.request; sys.stdout.reconfigure(encoding='utf-8'); h=urllib.request.urlopen('http://127.0.0.1:8003/flows',timeout=30); b=h.read().decode('utf-8'); print('status',h.status,'len',len(b)); [print('  found:',t) for t in ['자산군 회전','지역 회전','순위 변동','관측 불가'] if t in b]"
```
Expected: `status 200`, 길이 5000 이상, 4개 문구 전부 `found:`

브라우저로 `http://127.0.0.1:8003/flows` 를 열어 표 3개가 값과 함께 보이는지 눈으로도 확인한다. 서버는 Task 5 에서 계속 쓰므로 켜 둔다.

- [ ] **Step 7: 커밋**

```bash
git add app/server.py app/web/templates/flows.html run_flows_test.py
git commit -m "feat(flows): /flows 라우트 + 검증 페이지(표) + 8003 테스트 서버

차트 이전에 서버사이드 표로 데이터 도달을 먼저 확인. 스케줄러 비활성이라
8000/8001/8002 와 DB 충돌 없이 공존. 한계·관측불가 영역을 화면에 명시.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Chart.js 산점도 2종 (꼬리 · 이중 인코딩)

**Files:**
- Create: `app/web/static/flows.js`
- Modify: `app/web/templates/flows.html`
- Modify: `app/web/static/style.css` (`.tbl` 클래스 추가 — 아래 Step 4b)

**Interfaces:**
- Consumes: `window.__FLOW_ASSET__` / `window.__FLOW_REGION__` — Task 1·3의 지도 dict 를 `tojson` 으로 주입한 것
- Produces: 없음(최종 산출물)

> **Task 4 에서 넘어온 정리 항목 2건** (계획 누락분 — 이 Task 에서 해결한다):
> 1. `flows.html` 이 쓰는 `.tbl` 클래스가 `style.css` 에 없어 표 3개가 무스타일로 렌더된다 → Step 4b
> 2. 저장소 루트에 `flows_test_stdout.log` / `flows_test_stderr.log` 가 untracked 로 남아 있다 → Step 7b

- [ ] **Step 1: 색상 팔레트 확정**

이 단계에서 `dataviz` 스킬을 호출해 발산(diverging) 스케일과 대비 기준을 확인한다.

Run: `Skill` 도구로 `dataviz` 호출

스펙 4.4절이 요구하는 것:
- **색 = 연속 발산 스케일** (공격 ← 0 → 방어). 임계선 3분류 금지
- **모양 = 자산군 5종** (주식 원 / 채권 사각 / 실물 삼각 / 코인 마름모 / 통화 별)
- 지도 2(지역)는 전부 주식이라 **모양·색 구분 없이 라벨만**
- 라이트·다크 양쪽에서 읽혀야 함

- [ ] **Step 2: `flows.js` 작성**

`app/web/static/flows.js`:

```javascript
/* 자금 회전지도 — RRG 산점도.
   색 = 위험 성격 연속 발산 스케일(공격 <- 0 -> 방어), 모양 = 자산군.
   점 구름 지름 중앙값이 4~5 로 좁아 축은 반드시 데이터에 맞춰 자동 스케일한다
   (94~106 고정 축으로 그리면 전부 가운데 뭉쳐 보인다). */

const SHAPE = {
  equity: 'circle', bond: 'rect', real: 'triangle',
  crypto: 'rectRot', fx: 'star', region: 'circle',
};

/* 위험 성격 -> 색. risk 는 VIX 급등일 평균수익률(%), 음수=공격 / 양수=방어.
   ±2.5% 를 양 끝으로 클램프한 뒤 두 색 사이를 선형 보간한다. */
function riskColor(risk) {
  if (risk === null || risk === undefined) return 'rgb(140,140,150)';
  const t = Math.max(-1, Math.min(1, risk / 2.5));       // -1(공격) ~ +1(방어)
  const attack = [214, 69, 65], calm = [46, 125, 186], mid = [150, 150, 158];
  const [a, b, w] = t < 0 ? [mid, attack, -t] : [mid, calm, t];
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * w));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function padRange(vals, pad) {
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = Math.max(hi - lo, 0.5);
  return { min: lo - span * pad, max: hi + span * pad };
}

function drawRRG(canvasId, map, opts) {
  const el = document.getElementById(canvasId);
  if (!el || !map || !map.rows || !map.rows.length) return;
  const useRisk = !!opts.useRisk;

  const datasets = map.rows.map((r) => {
    const color = useRisk ? riskColor(r.risk) : 'rgb(90,120,200)';
    return {
      label: r.label,
      data: r.tail.map((p) => ({ x: p.x, y: p.y, date: p.date })),
      showLine: true,
      borderColor: color,
      borderWidth: 1.5,
      backgroundColor: color,
      pointStyle: SHAPE[r.group] || 'circle',
      /* 꼬리는 작게, 현재 위치(마지막 점)만 크게 */
      pointRadius: (c) => (c.dataIndex === c.dataset.data.length - 1 ? 7 : 2.5),
      pointHoverRadius: 9,
      tension: 0.25,
    };
  });

  const xs = map.rows.flatMap((r) => r.tail.map((p) => p.x));
  const ys = map.rows.flatMap((r) => r.tail.map((p) => p.y));
  const xr = padRange(xs.concat([100]), 0.12);
  const yr = padRange(ys.concat([100]), 0.12);

  /* 100 기준 십자선 + 사분면 이름 */
  const crosshair = {
    id: 'crosshair',
    beforeDatasetsDraw(chart) {
      const { ctx, chartArea: a, scales } = chart;
      const cx = scales.x.getPixelForValue(100), cy = scales.y.getPixelForValue(100);
      ctx.save();
      ctx.strokeStyle = 'rgba(128,128,140,0.45)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(a.left, cy); ctx.lineTo(a.right, cy);
      ctx.moveTo(cx, a.top); ctx.lineTo(cx, a.bottom); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(128,128,140,0.75)';
      ctx.font = '11px Inter, sans-serif';
      ctx.fillText('주도', a.right - 34, a.top + 14);
      ctx.fillText('개선', a.left + 8, a.top + 14);
      ctx.fillText('약화', a.right - 34, a.bottom - 8);
      ctx.fillText('지체', a.left + 8, a.bottom - 8);
      ctx.restore();
    },
  };

  /* 현재 위치 옆 라벨 직접 부착(범례 왕복 제거) */
  const tags = {
    id: 'tags',
    afterDatasetsDraw(chart) {
      const { ctx } = chart;
      ctx.save();
      ctx.font = '600 11px Inter, sans-serif';
      ctx.fillStyle = getComputedStyle(chart.canvas).color || '#333';
      chart.data.datasets.forEach((ds, i) => {
        const meta = chart.getDatasetMeta(i);
        const last = meta.data[meta.data.length - 1];
        if (last) ctx.fillText(ds.label, last.x + 10, last.y + 4);
      });
      ctx.restore();
    },
  };

  new Chart(el, {
    type: 'scatter',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { right: 56 } },     // 오른쪽 라벨 잘림 방지
      scales: {
        x: { min: xr.min, max: xr.max, title: { display: true, text: '상대강도 (3개월 위치) →' } },
        y: { min: yr.min, max: yr.max, title: { display: true, text: '상대모멘텀 (1개월 방향) →' } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => items[0].dataset.label,
            label: (c) => `${c.raw.date}  강도 ${c.parsed.x.toFixed(2)} / 모멘텀 ${c.parsed.y.toFixed(2)}`,
          },
        },
      },
    },
    plugins: [crosshair, tags],
  });
}

document.addEventListener('DOMContentLoaded', () => {
  drawRRG('flow-asset', window.__FLOW_ASSET__, { useRisk: true });
  drawRRG('flow-region', window.__FLOW_REGION__, { useRisk: false });
});
```

- [ ] **Step 3: 문법 점검**

Run: `node --check app/web/static/flows.js`
Expected: 출력 없음

> `node` 가 없으면 이 단계는 건너뛰고 Step 6 의 브라우저 콘솔 확인으로 대체한다.

- [ ] **Step 4: 템플릿에 캔버스 + 데이터 주입**

`flows.html` 의 `rrg_table` 매크로 안, `<p class="muted" ...>기준 {{ m.as_of }}...</p>` 바로 **뒤**에 캔버스를 넣는다:

```html
  <div style="position:relative;height:420px;margin:0 0 14px">
    <canvas id="{{ canvas_id }}"></canvas>
  </div>
```

매크로 선언을 인자 하나 늘린 형태로 바꾼다:

```html
{% macro rrg_table(m, title, note, canvas_id) %}
```

호출부도 바꾼다:

```html
{{ rrg_table(f.asset, '지도 1 — 자산군 회전', '주식이냐 채권이냐 금이냐', 'flow-asset') }}
{{ rrg_table(f.region, '지도 2 — 지역 회전', '주식 안에서 어느 나라냐', 'flow-region') }}
```

파일 맨 아래 `{% endblock %}` **앞**에 스크립트 블록을 추가한다:

```html
{% block scripts %}
<script>
  window.__FLOW_ASSET__  = {{ f.asset | tojson }};
  window.__FLOW_REGION__ = {{ f.region | tojson }};
</script>
<script src="/static/flows.js"></script>
{% endblock %}
```

> `{% block scripts %}` 는 `base.html:26` 에 이미 있고 `app.js` 뒤에 온다. Chart.js 는 `base.html:10` 에서 전역 로드되므로 별도 추가 불필요.

- [ ] **Step 4b: `.tbl` 표 스타일 추가**

`app/web/static/style.css` 끝에 추가한다. 기존 `.histtable`(197행)의 서식을 따르되,
회전지도 표는 열이 많아 헤더를 중앙 정렬하고 숫자 열은 오른쪽 정렬한다.
라이트/다크 겸용을 위해 기존 파일이 쓰는 CSS 변수를 그대로 쓴다 —
`style.css` 상단에서 실제로 정의된 변수명을 확인하고 그것을 쓸 것(임의로 색을 하드코딩하지 마라).

```css
/* 회전지도 표(자산군·지역·순위 변동) */
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th,.tbl td{padding:7px 8px;text-align:right;white-space:nowrap}
.tbl th{font-weight:600;font-size:11.5px;line-height:1.35;opacity:.72;
        border-bottom:1px solid rgba(128,128,140,.28)}
.tbl td{border-bottom:1px solid rgba(128,128,140,.13)}
.tbl tbody tr:last-child td{border-bottom:none}
.tbl tbody tr:hover td{background:rgba(128,128,140,.07)}
.tbl th:first-child,.tbl td:first-child{text-align:left}
.tbl .mono{font-family:'IBM Plex Mono',ui-monospace,monospace}
```

- [ ] **Step 5: 서버 재기동**

Task 4 에서 띄운 서버를 Ctrl+C 로 끄고 다시 실행한다(템플릿·정적파일 변경 반영).

Run: `.\.venv\Scripts\python.exe run_flows_test.py`

- [ ] **Step 6: 렌더 검증**

```bash
.\.venv\Scripts\python.exe -c "import sys,urllib.request; sys.stdout.reconfigure(encoding='utf-8'); b=urllib.request.urlopen('http://127.0.0.1:8003/flows',timeout=30).read().decode('utf-8'); print('len',len(b)); [print('  found:',t) for t in ['flow-asset','flow-region','__FLOW_ASSET__','flows.js'] if t in b]"
```
Expected: 4개 전부 `found:`

브라우저에서 `http://127.0.0.1:8003/flows` 를 열고 확인한다:
1. 산점도 2개가 그려지고 점이 **가운데 뭉치지 않는다**(축 자동 스케일 작동)
2. 각 점에서 꼬리(8주 궤적)가 이어지고, 현재 위치 점이 더 크다
3. 점 옆에 자산 이름이 직접 붙어 있고 오른쪽에서 잘리지 않는다
4. 지도 1에서 `비트코인`·`나스닥` 계열은 공격색, `원/달러`·`WTI`는 방어색
5. 십자선과 사분면 이름(주도·개선·약화·지체)이 보인다
6. 개발자도구 콘솔에 에러가 없다
7. 표의 좌표와 산점도 위치가 일치한다 (예: 표에서 `금` y가 가장 큰 값이면 지도에서도 금이 가장 위)

- [ ] **Step 7: 전체 검증 재실행**

Run: `.\.venv\Scripts\python.exe scripts/verify_flows.py`
Expected: `전부 통과` (차트 작업이 계산을 건드리지 않았음을 확인)

- [ ] **Step 7b: 테스트 서버 정리**

브라우저 확인이 끝났으면 8003 서버를 종료하고 로그 잔여물을 지운다.
(1단계 검증이 끝났으므로 서버를 계속 띄워둘 이유가 없다.)

```bash
rm -f flows_test_stdout.log flows_test_stderr.log
```

서버 프로세스는 실행 중인 터미널에서 Ctrl+C 로 끄거나, 백그라운드로 띄웠다면
해당 PID 를 종료한다. **포트 8000 프로세스는 절대 건드리지 마라** — 사용자의 실서버다.

- [ ] **Step 8: 커밋**

```bash
git add app/web/static/flows.js app/web/templates/flows.html app/web/static/style.css
git commit -m "feat(flows): RRG 산점도 2종 — 8주 꼬리 + 색·모양 이중 인코딩

색은 위험성격 연속 발산 스케일, 모양은 자산군 5종, 라벨은 점에 직접 부착.
점구름 지름이 4~5로 좁아 축은 데이터 기준 자동 스케일(고정축이면 뭉침).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

---

### Task 3.5: 검증을 데이터 픽스쳐로 고정 (계획 개정)

**왜 필요한가.** Task 1~2의 기준 좌표는 **장중 진행형 마지막 봉**에서 산출됐다.
그 봉이 정규 종가로 확정되면서 값이 바뀌었고(금 2026-08-05: 4306.60 → 4245.80),
RS-Ratio의 마지막 점과 바스켓이 통째로 이동해 골든 검사 전부가 깨졌다.
교집합 마지막 날짜(08-05)와 일수(744)는 그대로라 날짜 검사로는 잡히지 않았다.
`flows.py` 와 무관한 독립 스크립트에서도 동일하게 재현됐으므로 **계산 오류가 아니라
계획의 검증 설계 결함**이다. 살아 있는 데이터에 골든값을 못 박으면 매일 깨진다.

**해결.** 검증이 DB가 아니라 동결된 픽스쳐를 읽게 한다. 두 개의 독립 구현이
같은 고정 데이터에서 일치하는지 보는 구조라 진짜 검증력을 갖는다.

**Files:**
- Create: `scripts/fixtures/flows_history.json` (동결 데이터, 약 600KB)
- Create: `scripts/gen_flows_golden.py` (픽스쳐 생성 + **독립 구현**으로 골든 산출)
- Modify: `scripts/verify_flows.py` (픽스쳐 주입 + 골든값 교체)

**Interfaces:**
- Produces: `scripts/fixtures/flows_history.json` 구조
  `{"generated": "<ISO 날짜>", "note": "<설명>", "series": {"<키>": [["<날짜>", <값>], ...], ...}}`
  날짜 오름차순. 값은 소수 4자리로 반올림.

- [ ] **Step 1: 픽스쳐 + 독립 골든 생성기 작성**

`scripts/gen_flows_golden.py` — **`app.analysis.flows` 를 import 하지 않는다.**
스펙 4.3절 공식을 직접 구현해 골든을 낸다. 이 독립성이 검증의 전부다.

```python
"""회전지도 검증용 픽스쳐 동결 + 기준 좌표 산출.

verify_flows.py 가 살아 있는 DB 를 읽으면 장중 진행형 마지막 봉이 확정될 때마다
좌표가 이동해 검사가 깨진다. 그래서 필요한 시계열을 픽스쳐로 동결한다.

이 스크립트는 app.analysis.flows 를 import 하지 않는다 — 스펙 4.3절 공식을
독립 구현해 골든을 낸다. 두 개의 독립 구현이 같은 고정 데이터에서 일치하는지가
verify_flows.py 가 검증하는 것이다.

재생성이 필요할 때만 실행한다(데이터 갱신은 골든을 무효화하므로 신중히):
  .\\.venv\\Scripts\\python.exe scripts/gen_flows_golden.py
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "economic.db"
OUT = ROOT / "scripts" / "fixtures" / "flows_history.json"
POINTS = 1300          # flows._POINTS 와 같아야 한다

# (지표키, 라벨, 환율키, 연산) — flows._ASSETS / _REGIONS 와 같은 구성
ASSET = [("sp500", "S&P500", "", ""), ("nasdaq", "나스닥", "", ""),
         ("kospi", "코스피", "usdkrw", "/"), ("tlt", "미 장기국채", "", ""),
         ("hyg", "하이일드채", "", ""), ("gold", "금", "", ""),
         ("copper", "구리", "", ""), ("wti", "WTI", "", ""),
         ("btc", "비트코인", "", ""), ("usdkrw", "원/달러", "", "")]
REGION = [("sp500", "미국", "", ""), ("kospi", "한국", "usdkrw", "/"),
          ("eustoxx", "유럽", "eurusd", "*"), ("shanghai", "중국본토", "usdcny", "/"),
          ("hangseng", "홍콩", "", ""), ("nikkei", "일본", "usdjpy", "/")]
# 픽스쳐에 담을 전체 시계열(위 두 지도 + 환율 + vix 위험성격용)
FIX_KEYS = sorted({k for k, _, _, _ in ASSET + REGION}
                  | {"usdkrw", "eurusd", "usdcny", "usdjpy", "vix"})


def load_db(key: str) -> list[tuple[str, float]]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT date, value FROM history WHERE key=? AND value IS NOT NULL "
        "ORDER BY date DESC LIMIT ?", (key, POINTS)).fetchall()
    con.close()
    return [(d, round(v, 4)) for d, v in reversed(rows)]


def norm(vals: list[float], win: int) -> list[float | None]:
    """롤링 z-정규화 — 모집단 표준편차(스펙 4.3절)."""
    out: list[float | None] = [None] * len(vals)
    for i in range(win - 1, len(vals)):
        w = vals[i - win + 1:i + 1]
        m = sum(w) / win
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / win)
        out[i] = 100.0 + (vals[i] - m) / sd if sd else 100.0
    return out


def rrg(spec, series):
    need = {k for k, _, _, _ in spec} | {f for _, _, f, _ in spec if f}
    S = {k: dict(series[k]) for k in need}
    dates = [d for d in sorted(S[spec[0][0]]) if all(S[k].get(d) is not None for k in need)]
    P = {}
    for k, _, fk, op in spec:
        if not fk:
            P[k] = [S[k][d] for d in dates]
        elif op == "/":
            P[k] = [S[k][d] / S[fk][d] for d in dates]
        else:
            P[k] = [S[k][d] * S[fk][d] for d in dates]
    order = [k for k, _, _, _ in spec]
    N = {k: [100.0 * v / P[k][0] for v in P[k]] for k in order}
    B = [sum(N[k][i] for k in order) / len(order) for i in range(len(dates))]
    out = {}
    for k in order:
        rs = [100.0 * N[k][i] / B[i] for i in range(len(dates))]
        r = norm(rs, 63)
        m = norm([v if v is not None else 100.0 for v in r], 21)
        out[k] = (round(r[-1], 2), round(m[-1], 2))
    return dates, out


series = {k: load_db(k) for k in FIX_KEYS}
for k, v in series.items():
    if len(v) < 200:
        print(f"경고: {k} 이력이 {len(v)}개뿐 — 픽스쳐로 부적합")
        sys.exit(1)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    "generated": date.today().isoformat(),
    "note": "회전지도 검증용 동결 시계열. 살아 있는 DB 대신 이 파일로 검증한다. "
            "재생성하면 골든 좌표가 무효가 되므로 scripts/gen_flows_golden.py 를 다시 돌려야 한다.",
    "series": series,
}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"픽스쳐 저장: {OUT}  시계열 {len(series)}개  {OUT.stat().st_size // 1024}KB")

spec9 = [s for s in ASSET if s[0] != "tlt"]
for name, spec, var in [("자산군9(오라클)", spec9, "GOLDEN_ASSET_9"),
                        ("자산군", ASSET, "GOLDEN_ASSET"),
                        ("지역", REGION, "GOLDEN_REGION")]:
    dates, pts = rrg(spec, series)
    print(f"\n# {name} — 교집합 {len(dates)}일, {dates[0]} ~ {dates[-1]}")
    print(f"{var} = {{")
    for k, lbl, _, _ in spec:
        print(f'    "{k}": ({pts[k][0]}, {pts[k][1]}),   # {lbl}')
    print(f"}}   # days = {len(dates)}")
```

- [ ] **Step 2: 실행해 픽스쳐와 골든 생성**

Run: `.\.venv\Scripts\python.exe scripts/gen_flows_golden.py`
Expected: 픽스쳐 저장 메시지 + 세 개의 골든 딕셔너리 출력. 출력을 보관한다.

- [ ] **Step 3: `verify_flows.py` 를 픽스쳐로 전환**

`from app.analysis import flows` **직후**에 픽스쳐 주입을 넣는다. 프로덕션 코드는 건드리지 않는다:

```python
# ── 검증은 동결 픽스쳐로 한다 ──
# 살아 있는 DB 를 읽으면 장중 진행형 마지막 봉이 확정될 때마다 좌표가 이동해
# 검사가 무의미해진다(2026-08-06 실제 발생). repo 접근만 픽스쳐로 갈아끼운다.
_FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "flows_history.json")
                  .read_text(encoding="utf-8"))


def _fixture_batch(keys, points: int = 60):
    return {k: [{"date": d, "value": v} for d, v in _FIX["series"].get(k, [])][-points:]
            for k in keys}


flows.repo.get_series_batch = _fixture_batch
```

파일 상단 import 에 `import json` 을 추가한다(`Path` 는 이미 있다).

Step 2 출력으로 `GOLDEN_ASSET_9` · `GOLDEN_ASSET` · `GOLDEN_REGION` · `GOLDEN_DAYS` 를 교체한다.
`GOLDEN_ASSET_9` 의 주석을 이렇게 갱신한다:

```python
# 독립 오라클 — scripts/gen_flows_golden.py 가 flows.py 를 import 하지 않고
# 스펙 4.3절 공식을 직접 구현해 산출한 값. 동결 픽스쳐 기준이라 재현 가능하다.
# flows.py 의 출력으로 덮어쓰지 말 것 — 두 독립 구현의 일치가 이 검사의 전부다.
```

`GOLDEN_ASSET` 주석도 "10종 회귀 잠금 — 위와 같은 독립 산출" 로 갱신한다
(이제 이것도 독립 산출이므로 "독립 검증력 없음" 문구는 삭제한다).

- [ ] **Step 4: 검증 통과 확인**

Run: `.\.venv\Scripts\python.exe scripts/verify_flows.py`
Expected: `전부 통과`. 세 지도 전부 OK, 위험성격 부호 조건 통과, 순위표·요약 정상.

- [ ] **Step 5: 재현성 확인 — 이 결함이 실제로 고쳐졌는지**

같은 명령을 한 번 더 돌린다. 픽스쳐를 읽으므로 DB 가 갱신돼도 결과가 동일해야 한다.

Run: `.\.venv\Scripts\python.exe scripts/verify_flows.py`
Expected: 1회차와 **완전히 동일한 출력**. 다르면 아직 어딘가 라이브 DB 를 읽고 있는 것이다.

- [ ] **Step 6: 문법 점검 후 커밋**

Run: `.\.venv\Scripts\python.exe -m py_compile app/analysis/flows.py scripts/verify_flows.py scripts/gen_flows_golden.py`

```bash
git add app/analysis/flows.py scripts/verify_flows.py
git commit -m "feat(flows): 위험성격 연속값 + 순위 변동표 + 규칙 기반 요약

색은 VIX 급등일 민감도 연속 스케일 — 3단계 분류는 경계 자산이 룩백에 따라
깜빡여 폐기(스펙 4.4). 요약은 사분면 전이 규칙 기반이라 LLM 지연 없음.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"

git add scripts/gen_flows_golden.py scripts/fixtures/flows_history.json
git commit -m "test(flows): 검증을 동결 픽스쳐로 고정

기준 좌표를 장중 진행형 마지막 봉에서 뽑아 확정 종가로 바뀌면 매일 깨지던
문제 해결(금 08-05 4306.60 -> 4245.80). 골든은 flows.py 를 import 하지 않는
독립 구현이 산출한다 — 두 구현의 일치가 검증의 핵심.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## 완료 기준

- [ ] `.\.venv\Scripts\python.exe scripts/verify_flows.py` → `전부 통과`
- [ ] `http://127.0.0.1:8003/flows` 에서 지도 2개 + 순위표 + 한계 안내가 렌더된다
- [ ] `http://127.0.0.1:8000/` 메인 대시보드가 `tlt` 추가 후에도 정상 렌더된다
- [ ] 브라우저 콘솔 에러 없음

## 2단계 (이 계획 범위 밖)

스펙 7절 참조. 1단계가 검증된 뒤 별도 사이클로:
- 오버레이 섹션 아래 full-width 로 대시보드 이관, `presenter.build_dashboard` 에 편입(라이브 전용 — `snapshot_id is None`)
- `/flows` 라우트·`flows.html`·`run_flows_test.py` 정리
- **`run.py` 재시작** — 재시작 전까지 스케줄 수집이 `tlt` 를 건너뛴다(CLAUDE.md)
