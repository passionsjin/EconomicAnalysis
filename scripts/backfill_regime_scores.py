r"""저장된 스냅샷의 위험선호 점수를 현행 보정 기준으로 재계산해 백필한다.

왜 필요한가
-----------
`regime.py` 의 수준형 신호(HY·NFCI·장단기차) 중립점을 실제 분포에 맞게 재보정하면서
점수 스케일이 바뀌었다(5년 소급 평균 58.8 -> 49.7). 그런데 `snapshots.regime_score` 에
이미 기록된 과거 점수는 옛 스케일 그대로다. `/api/regime` 은 재계산 시계열(`history`)과
DB 기록(`stored`)을 함께 내려주므로, 백필하지 않으면 두 계열 사이에 단차가 생긴다.

어떻게 매핑하나
---------------
재계산은 sp500 거래일 축 위에서 하루 단위로 이뤄진다(`regime.regime_history`). 스냅샷은
장중 임의 시각이라, **그 스냅샷이 실제로 보고 있던 sp500 봉의 날짜**로 축에 붙인다 —
`observations.as_of`(해당 수집 시점 sp500 시세의 기준시각)의 날짜를 쓴다. 시각 휴리스틱
보다 정확하다. 축에 그 날짜가 없으면 그 이전 마지막 거래일로 내려붙는다.

한계(알고 쓸 것)
----------------
- 재계산은 **일 단위 종가 기준**이라, 같은 날 여러 번 수집된 스냅샷은 모두 같은 점수가
  된다. 원래는 장중 진행형 값이라 조금씩 달랐다.
- `history` 는 개정된 최신 데이터를 담는다(FRED/CFNAI 개정). 즉 '그때 보였던 값'이 아니라
  '지금 아는 그날의 값'으로 계산된다.
- 미래참조는 없다 — 날짜 D 의 점수는 D 이하 데이터만 쓴다.

실행:
  .\.venv\Scripts\python.exe scripts/backfill_regime_scores.py            # 미리보기(쓰기 없음)
  .\.venv\Scripts\python.exe scripts/backfill_regime_scores.py --apply    # 실제 반영
옵션: --span N(재계산 거래일 수, 기본 400) · --no-backup(백업 생략, 권장하지 않음)

주의: run.py 가 돌고 있으면 DB 락 경합으로 느려질 수 있다. 또 재보정 전 코드로 기동된
프로세스는 계속 옛 스케일로 기록하므로, **run.py 를 먼저 재시작한 뒤** 백필할 것.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                                # noqa: E402
from app.analysis import regime as regime_mod     # noqa: E402
from app.config import settings                   # noqa: E402

WINDOW = 20   # pipeline 이 기록할 때 쓰는 값(regime_mod.snapshot(30) -> detect_regime(20))과 일치


def _axis_date(as_of: str | None, finished_utc: str | None) -> str | None:
    """스냅샷이 보고 있던 sp500 봉의 날짜(YYYY-MM-DD)."""
    for ts in (as_of, finished_utc):
        if ts:
            return ts[:10]
    return None


def _resolve(day: str, days_sorted: list[str]) -> str | None:
    """축에 day 가 없으면 그 이전 마지막 거래일로."""
    import bisect
    j = bisect.bisect_right(days_sorted, day) - 1
    return days_sorted[j] if j >= 0 else None


def _dist(scores: list[int]) -> str:
    b = Counter("①>=70" if v >= 70 else "②58-69" if v >= 58 else "③43-57" if v >= 43
                else "④31-42" if v >= 31 else "⑤<31" for v in scores)
    return "  ".join(f"{k} {100 * b[k] / len(scores):4.1f}%" for k in sorted(b))


def _backup(src: Path) -> Path:
    """WAL 환경에서도 일관된 백업(파일 복사 대신 sqlite backup API)."""
    dst = src.with_name(f"{src.name}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="스냅샷 위험선호 점수 재계산 백필")
    ap.add_argument("--apply", action="store_true", help="실제 DB 에 반영(기본은 미리보기)")
    ap.add_argument("--span", type=int, default=400, help="재계산할 거래일 수(기본 400)")
    ap.add_argument("--no-backup", action="store_true", help="백업 생략(권장하지 않음)")
    args = ap.parse_args()

    print(f"재계산 중 (window={WINDOW}, span={args.span})...")
    recomputed = {p["date"]: p["score"] for p in regime_mod.regime_history(WINDOW, args.span)}
    if not recomputed:
        print("[중단] 재계산 결과가 비었다 - history 에 sp500 이 있는지 확인할 것.")
        return 1
    days = sorted(recomputed)
    print(f"  재계산 {len(days)}일: {days[0]} ~ {days[-1]}")

    con = db.connect()
    rows = con.execute(
        "SELECT s.id, s.finished_utc, s.regime_score, s.regime_short, o.as_of "
        "FROM snapshots s LEFT JOIN observations o "
        "  ON o.snapshot_id = s.id AND o.key = 'sp500' "
        "WHERE s.regime_score IS NOT NULL ORDER BY s.finished_utc"
    ).fetchall()
    print(f"  대상 스냅샷 {len(rows)}건")

    updates: list[tuple[int, int, str, str]] = []   # (score, id, tone, short) 순은 아래에서 맞춤
    plan: list[tuple[int, str, int, int]] = []      # (id, 축날짜, 이전, 이후)
    unmatched = 0
    for r in rows:
        day = _axis_date(r["as_of"], r["finished_utc"])
        hit = _resolve(day, days) if day else None
        if hit is None:
            unmatched += 1
            continue
        new = recomputed[hit]
        plan.append((r["id"], hit, r["regime_score"], new))
        _label, short, tone = regime_mod._classify(new)
        updates.append((new, tone, short, r["id"]))

    if not plan:
        print("[중단] 축에 붙은 스냅샷이 하나도 없다.")
        return 1

    old = [p[2] for p in plan]
    new = [p[3] for p in plan]
    print()
    print(f"{'':6s} {'평균':>6s} {'중앙값':>7s} {'최소':>5s} {'최대':>5s}   분포")
    print(f"{'이전':6s} {statistics.mean(old):6.1f} {statistics.median(old):7.0f} "
          f"{min(old):5d} {max(old):5d}   {_dist(old)}")
    print(f"{'이후':6s} {statistics.mean(new):6.1f} {statistics.median(new):7.0f} "
          f"{min(new):5d} {max(new):5d}   {_dist(new)}")
    if unmatched:
        print(f"\n[주의] 축에 붙지 못한 스냅샷 {unmatched}건은 건너뛴다(원값 유지).")

    changed = sum(1 for _i, _d, o, n in plan if o != n)
    print(f"\n변경 대상 {changed}/{len(plan)}건. 날짜별 표본(스냅샷 여러 건은 같은 날 = 같은 점수):")
    seen: set[str] = set()
    for sid, day, o, n in plan:
        if day in seen:
            continue
        seen.add(day)
        if len(seen) > 12 and day != plan[-1][1]:
            continue
        print(f"   {day}  {o:3d} -> {n:3d}  ({n - o:+d})  [id {sid}]")

    if not args.apply:
        print("\n미리보기만 수행했다(쓰기 없음). 반영하려면 --apply 를 붙일 것.")
        return 0

    if not args.no_backup:
        dst = _backup(Path(settings.db_path))
        print(f"\n백업 생성: {dst}  (되돌리려면 이 파일을 economic.db 로 복사)")

    # db.py 가 autocommit(isolation_level=None) 이라 명시 트랜잭션으로 묶는다.
    con.execute("BEGIN IMMEDIATE")
    try:
        con.executemany(
            "UPDATE snapshots SET regime_score=?, regime_tone=?, regime_short=? WHERE id=?",
            updates)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print(f"반영 완료: {len(updates)}건 갱신.")

    verify = con.execute(
        "SELECT regime_score FROM snapshots WHERE regime_score IS NOT NULL").fetchall()
    v = [r[0] for r in verify]
    print(f"검증 - DB 현재 {len(v)}건: 평균 {statistics.mean(v):.1f} / 중앙값 "
          f"{statistics.median(v):.0f} / 범위 {min(v)}~{max(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
