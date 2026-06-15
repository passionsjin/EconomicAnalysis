/* 거시경제 대시보드 — 프론트 로직: 시각 변환·스파크라인·모달차트·자동수집·자동새로고침 */
"use strict";

const TONE = {
  good: "#16c784", bad: "#ea3943", neutral: "#7d8696", warn: "#f0a020",
};

/* ── 시각(KST) 포맷 ── */
const kstFmt = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});
const kstDateFmt = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

function relTime(d) {
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "방금";
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
}

function applyTimes() {
  document.querySelectorAll("time.kst").forEach((el) => {
    const raw = el.dataset.utc;
    if (!raw) { el.textContent = "—"; return; }
    const d = new Date(raw);
    if (isNaN(d)) { el.textContent = "—"; return; }
    if (el.classList.contains("rel")) el.textContent = relTime(d);
    else if (el.classList.contains("dt")) el.textContent = kstDateFmt.format(d);
    else el.textContent = kstFmt.format(d);
    el.title = kstDateFmt.format(d) + " KST";
  });
}

/* ── 브리핑 마크다운 (LLM 출력 = 신뢰 불가 → 반드시 sanitize) ── */
function renderBriefing() {
  const el = document.getElementById("brief-body");
  if (!el || !window.__BRIEFING_MD__ || !window.marked) return;
  const rawHtml = marked.parse(window.__BRIEFING_MD__);
  // DOMPurify 로 위험한 HTML/스크립트 제거 후 삽입(없으면 텍스트로 안전 폴백)
  if (window.DOMPurify) {
    el.innerHTML = DOMPurify.sanitize(rawHtml, { USE_PROFILES: { html: true } });
  } else {
    el.textContent = window.__BRIEFING_MD__;
  }
}

/* ── 시계열 fetch (캐시) ── */
const _seriesCache = {};
async function fetchSeries(key, points) {
  const ck = `${key}:${points}`;
  if (_seriesCache[ck]) return _seriesCache[ck];
  try {
    // points = '개수' 기준(일/월 빈도 무관) — 월별 거시도 다년치가 나온다
    const r = await fetch(`/api/series/${encodeURIComponent(key)}?points=${points}`);
    const j = await r.json();
    _seriesCache[ck] = j.series || [];
    return _seriesCache[ck];
  } catch (e) { return []; }
}

/* ── 동시성 제한 풀 ── */
async function pool(items, worker, size = 6) {
  const q = [...items];
  const runners = Array.from({ length: Math.min(size, q.length) }, async () => {
    while (q.length) { const it = q.shift(); await worker(it); }
  });
  await Promise.all(runners);
}

/* ── 스파크라인 ── */
async function drawSpark(canvas) {
  const series = await fetchSeries(canvas.dataset.key, 60);  // 최근 60개 점
  if (!series.length) { canvas.style.display = "none"; return; }
  const color = TONE[canvas.dataset.tone] || TONE.neutral;
  new Chart(canvas, {
    type: "line",
    data: {
      labels: series.map((p) => p.date),
      datasets: [{
        data: series.map((p) => p.value), borderColor: color, borderWidth: 1.4,
        pointRadius: 0, tension: 0.25, fill: true,
        backgroundColor: (ctx) => {
          const { ctx: c, chartArea } = ctx.chart;
          if (!chartArea) return "transparent";
          const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          g.addColorStop(0, color + "33"); g.addColorStop(1, color + "00");
          return g;
        },
      }],
    },
    options: {
      // 종횡비 고정 → 부모 카드 높이에 의존하지 않아 렌더가 안정적(늘어남/잘림 방지)
      responsive: true, maintainAspectRatio: true, aspectRatio: 7, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false, grace: "8%" } },
      elements: { line: { borderJoinStyle: "round" } },
    },
  });
}

/* ── 모달 큰 차트 ── */
let _modalChart = null;
async function openChart(key, label, unit) {
  const modal = document.getElementById("modal");
  document.getElementById("modal-title").textContent = label + (unit ? ` (${unit})` : "");
  modal.classList.remove("hidden");
  const series = await fetchSeries(key, 240);  // 최근 240개 점(일별 ≈1년, 월별 ≈20년)
  const ctx = document.getElementById("modal-chart");
  if (_modalChart) { _modalChart.destroy(); _modalChart = null; }
  if (!series.length) {
    document.getElementById("modal-title").textContent =
      label + " — 아직 이력 데이터가 없습니다 (수집이 쌓이면 표시됩니다)";
    return;
  }
  _modalChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: series.map((p) => p.date),
      datasets: [{
        label, data: series.map((p) => p.value),
        borderColor: "#3b82f6", borderWidth: 1.8, pointRadius: 0, tension: 0.2, fill: false,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false } },
      scales: {
        x: { ticks: { color: "#8a94a6", maxTicksLimit: 8 }, grid: { color: "#1b2230" } },
        y: { ticks: { color: "#8a94a6" }, grid: { color: "#1b2230" } },
      },
    },
  });
}
function closeModal() {
  document.getElementById("modal").classList.add("hidden");
}

/* ── 수집 트리거 ── */
async function collectNow() {
  const btn = document.getElementById("collect-btn");
  const note = document.getElementById("status-note");
  btn.disabled = true; btn.textContent = "수집 중…";
  let baseline = await currentFinished();
  try { await fetch("/api/collect", { method: "POST" }); } catch (e) {}
  const t0 = Date.now();
  const timer = setInterval(async () => {
    const st = await fetch("/api/status").then((r) => r.json()).catch(() => null);
    if (st) {
      note.textContent = st.pipeline.running ? "● 수집 진행 중…" : "";
      const fin = st.latest_finished;
      if (!st.pipeline.running && fin && fin !== baseline) {
        clearInterval(timer); location.reload();
      }
    }
    if (Date.now() - t0 > 180000) { clearInterval(timer); btn.disabled = false; btn.textContent = "⟳ 지금 수집"; }
  }, 2500);
}

async function currentFinished() {
  const st = await fetch("/api/status").then((r) => r.json()).catch(() => null);
  return st ? st.latest_finished : null;
}

/* ── 자동 새로고침 ── */
async function autoRefreshLoop() {
  if (window.__HISTORICAL__) return;
  const baseline = await currentFinished();
  setInterval(async () => {
    const cb = document.getElementById("autorefresh");
    if (!cb || !cb.checked) return;
    const st = await fetch("/api/status").then((r) => r.json()).catch(() => null);
    if (!st) return;
    const note = document.getElementById("status-note");
    if (note) note.textContent = st.pipeline.running ? "● 수집 진행 중…" : "";
    if (!st.pipeline.running && st.latest_finished && st.latest_finished !== baseline) {
      location.reload();
    }
  }, 30000);
}

/* ── 초기화 ── */
document.addEventListener("DOMContentLoaded", () => {
  applyTimes();
  renderBriefing();
  setInterval(applyTimes, 60000); // 상대시간 갱신
  const sparks = document.querySelectorAll("canvas.spark");
  if (sparks.length) pool([...sparks], drawSpark, 6);
  autoRefreshLoop();
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
});

window.openChart = openChart;
window.closeModal = closeModal;
window.collectNow = collectNow;
