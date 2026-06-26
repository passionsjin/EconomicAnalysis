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

/* ── 즐겨찾기(localStorage) ── */
function getFavs() { try { return JSON.parse(localStorage.getItem("favs") || "[]"); } catch (e) { return []; } }
function setFavs(a) { try { localStorage.setItem("favs", JSON.stringify(a)); } catch (e) {} }
function toggleFav(ev, key) {
  ev.stopPropagation();
  const f = getFavs(); const i = f.indexOf(key);
  if (i >= 0) f.splice(i, 1); else f.push(key);
  setFavs(f); applyFavs();
}
function applyFavs() {
  const f = getFavs();
  document.querySelectorAll(".ind").forEach((c) => {
    const on = f.includes(c.dataset.key);
    c.classList.toggle("isfav", on);
    const btn = c.querySelector(".fav");
    if (btn) btn.classList.toggle("on", on);
  });
  filterIndicators();
}

/* ── 검색·필터 ── */
function filterIndicators() {
  const q = (document.getElementById("ind-search")?.value || "").trim().toLowerCase();
  const favOnly = document.getElementById("fav-only")?.checked;
  const prioOnly = document.getElementById("prio-only")?.checked;
  const favs = getFavs();
  document.querySelectorAll(".ind").forEach((c) => {
    const label = (c.dataset.label || "").toLowerCase();
    const key = (c.dataset.key || "").toLowerCase();
    let show = !q || label.includes(q) || key.includes(q);
    if (favOnly && !favs.includes(c.dataset.key)) show = false;
    if (prioOnly && c.dataset.prio !== "1") show = false;
    c.style.display = show ? "" : "none";
  });
  document.querySelectorAll(".group").forEach((g) => {
    const any = [...g.querySelectorAll(".ind")].some((c) => c.style.display !== "none");
    g.style.display = any ? "" : "none";
  });
}

/* ── CSV 내보내기(현재 표시 중인 지표) ── */
function exportCsv() {
  const rows = [["지표", "값", "변화", "백분위", "카테고리"]];
  document.querySelectorAll(".ind").forEach((c) => {
    if (c.style.display === "none") return;
    rows.push([
      c.dataset.label || "",
      (c.querySelector(".v-native")?.textContent || c.querySelector(".ind-value")?.textContent || "").trim(),
      (c.querySelector(".chg")?.textContent || "").trim(),
      (c.querySelector(".pctbar em")?.textContent || "").trim(),
      c.dataset.cat || "",
    ]);
  });
  const csv = rows.map((r) => r.map((x) => `"${String(x).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "indicators.csv"; a.click();
  URL.revokeObjectURL(a.href);
}

/* ── 원화 환산 토글(body.krw-on 클래스 → CSS가 native↔krw 전환) ── */
function toggleKrw() {
  const on = !!document.getElementById("krw-toggle")?.checked;
  document.body.classList.toggle("krw-on", on);
  try { localStorage.setItem("krwView", on ? "1" : "0"); } catch (e) {}
}
function restoreKrw() {
  let on = false;
  try { on = localStorage.getItem("krwView") === "1"; } catch (e) {}
  const cb = document.getElementById("krw-toggle");
  if (cb) cb.checked = on;
  document.body.classList.toggle("krw-on", on);
}

/* ── 쉬운 설명 토글(body.easy-on → CSS가 카드별 설명줄/범례 표시) ── */
function toggleEasy() {
  const on = !!document.getElementById("easy-toggle")?.checked;
  document.body.classList.toggle("easy-on", on);
  try { localStorage.setItem("easyView", on ? "1" : "0"); } catch (e) {}
}
function restoreEasy() {
  // 첫 방문(미설정)=ON(초보 접근성) · 한 번이라도 토글하면 그 선택을 유지
  let v = null;
  try { v = localStorage.getItem("easyView"); } catch (e) {}
  const on = (v === null) ? true : (v === "1");
  const cb = document.getElementById("easy-toggle");
  if (cb) cb.checked = on;
  document.body.classList.toggle("easy-on", on);
}

/* ── 자산간 상관 히트맵 ── */
function corrColor(c) {
  if (c === null || c === undefined) return "transparent";
  const a = Math.min(Math.abs(c), 1);
  const alpha = (0.10 + 0.62 * a).toFixed(2);
  return c >= 0 ? `rgba(22,199,132,${alpha})` : `rgba(234,57,67,${alpha})`;
}
async function renderCorrelations() {
  const el = document.getElementById("corr-heatmap");
  if (!el) return;
  let j;
  try {
    j = await fetch("/api/correlations?window=30").then((r) => r.json());
  } catch (e) { el.textContent = "상관 데이터를 불러오지 못했습니다."; return; }
  const labels = j.labels || [];
  const m = j.matrix || [];
  if (!labels.length) { el.textContent = "상관 계산에 필요한 이력이 부족합니다."; return; }
  let html = '<table class="corr"><thead><tr><th></th>';
  for (const l of labels) html += `<th>${l}</th>`;
  html += "</tr></thead><tbody>";
  for (let i = 0; i < labels.length; i++) {
    html += `<tr><th>${labels[i]}</th>`;
    for (let k = 0; k < labels.length; k++) {
      const c = (m[i] || [])[k];
      const txt = (c === null || c === undefined) ? "–" : c.toFixed(2);
      const bg = corrColor(c);
      html += `<td style="background:${bg}" title="${labels[i]} ↔ ${labels[k]}: ${txt}">${txt}</td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  el.classList.remove("muted");
  el.innerHTML = html;
}

/* ── 시장 분위기(위험선호 점수) 패널 ── */
async function renderRegime() {
  const el = document.getElementById("regime-panel");
  if (!el) return;
  let j;
  try { j = await fetch("/api/regime?window=20&span=90").then((r) => r.json()); }
  catch (e) { el.textContent = "시장 분위기 데이터를 불러오지 못했습니다."; return; }
  const cur = j.current || {}, hist = j.history || [];
  if (cur.score === null || cur.score === undefined) { el.textContent = "데이터 부족으로 계산 불가."; return; }
  const color = TONE[cur.tone] || TONE.neutral;
  el.classList.remove("muted");
  el.innerHTML = `
    <div class="regime-top">
      <div class="regime-score" style="color:${color}">${cur.score}<small>/100</small></div>
      <div class="regime-meta">
        <div class="regime-label" style="color:${color}">${cur.short || ""}</div>
        <div class="regime-gauge"><span style="width:${cur.score}%;background:${color}"></span><i></i></div>
        <div class="regime-drivers muted">${(cur.drivers || []).join(" · ")}</div>
      </div>
      <canvas id="regime-spark" height="46"></canvas>
    </div>
    <div class="regime-comp"></div>`;
  const comp = el.querySelector(".regime-comp");
  (cur.components || []).forEach((c) => {
    const pos = c.contrib >= 0, w = Math.min(Math.abs(c.contrib), 1) * 50;
    comp.insertAdjacentHTML("beforeend",
      `<div class="rc" title="${c.label}: 기여 ${pos ? "+" : ""}${c.contrib} · 가중 ${c.weight}">
         <span class="rc-l">${c.label}</span>
         <span class="rc-bar"><b class="${pos ? "pos" : "neg"}" style="width:${w}%;${pos ? "left:50%" : "right:50%"}"></b></span>
       </div>`);
  });
  const cv = document.getElementById("regime-spark");
  if (cv && hist.length > 1 && window.Chart) {
    new Chart(cv, {
      type: "line",
      data: { labels: hist.map((p) => p.date),
        datasets: [{ data: hist.map((p) => p.score), borderColor: color, borderWidth: 1.5,
          pointRadius: 0, tension: 0.25, fill: false }] },
      options: { responsive: true, maintainAspectRatio: true, aspectRatio: 5.5, animation: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false, min: 0, max: 100 } } },
    });
  }
}

/* ── 수익률곡선 차트(만기축; 현재 vs 1개월 전) ── */
function renderYieldCurve() {
  const yc = window.__YIELD_CURVE__;
  const cv = document.getElementById("yieldcurve");
  if (!cv || !yc || !yc.points || !yc.points.length || !window.Chart) return;
  const pts = yc.points;
  new Chart(cv, {
    type: "line",
    data: {
      labels: pts.map((p) => p.label),
      datasets: [
        { label: "현재", data: pts.map((p) => p.cur),
          borderColor: "#3b82f6", backgroundColor: "#3b82f622",
          borderWidth: 2.2, pointRadius: 3, pointBackgroundColor: "#3b82f6", tension: 0.3, fill: true },
        { label: "1개월 전", data: pts.map((p) => p.prev),
          borderColor: "#7d8696", borderWidth: 1.3, borderDash: [4, 3],
          pointRadius: 0, tension: 0.3, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: true, position: "bottom",
          labels: { color: "#8a94a6", boxWidth: 12, font: { size: 11 }, padding: 8 } },
        tooltip: { mode: "index", intersect: false,
          callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y == null ? "—" : c.parsed.y.toFixed(2)}%` } },
      },
      scales: {
        x: { ticks: { color: "#8a94a6" }, grid: { color: "#1b2230" } },
        y: { ticks: { color: "#8a94a6", callback: (v) => v + "%" }, grid: { color: "#1b2230" } },
      },
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

/* ── 내 포트폴리오 리스크(localStorage, 서버 미저장) ── */
function pfGet() { try { return JSON.parse(localStorage.getItem("pfHoldings") || "{}"); } catch (e) { return {}; } }
function pfSet(o) { try { localStorage.setItem("pfHoldings", JSON.stringify(o)); } catch (e) {} }
const PF_CAT = { equity: "주가지수", sector: "미국 섹터", commodity: "원자재", crypto: "암호화폐" };

function pfKRW(n) {
  if (n == null || isNaN(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e8) return (n / 1e8).toFixed(2) + "억";
  if (a >= 1e4) return Math.round(n / 1e4).toLocaleString() + "만";
  return Math.round(n).toLocaleString();
}

/* ⑨ 권고 자산비중 ↔ 내 보유 비중 갭(평가액에 권고 적용 + 입력자산 기준 비교) */
const PF_BUCKET = { equity: "equity", sector: "equity", commodity: "alt", crypto: "alt" };
function renderAllocMine(total, breakdown) {
  const box = document.getElementById("alMine");
  const al = window.__ALLOCATION__;
  if (!box || !al || !al.buckets || !total) { if (box) box.hidden = true; return; }
  const catOf = {};
  (window.__HOLDABLE__ || []).forEach((a) => { catOf[a.key] = a.category; });
  const mine = { equity: 0, bond: 0, alt: 0, cash: 0 };
  (breakdown || []).forEach((b) => { mine[PF_BUCKET[catOf[b.key]] || "alt"] += b.weight; });
  const rows = al.buckets.map((b) => {
    const amt = Math.round(total * b.weight / 100);
    const my = Math.round(mine[b.key] || 0);
    const gap = my - b.weight;
    const cls = gap >= 1 ? "over" : gap <= -1 ? "under" : "";
    const gapTxt = Math.abs(gap) < 1 ? "≈" : (gap > 0 ? "+" : "") + gap + "%p";
    return `<div class="alm-row"><span class="alm-k"><i class="al-dot ${b.key}"></i>${b.label}</span>`
      + `<span class="alm-rec">권고 ${b.weight}% <small>₩${pfKRW(amt)}</small></span>`
      + `<span class="alm-my">내 ${my}%</span>`
      + `<span class="alm-gap ${cls}">${gapTxt}</span></div>`;
  }).join("");
  box.innerHTML =
    `<div class="alm-head">내 평가액 <b>₩${pfKRW(total)}</b> 에 권고 비중 적용 <small>(입력 자산 기준)</small></div>`
    + rows
    + `<div class="alm-note">입력 자산은 모두 위험자산입니다 — 권고의 <b>현금·채권</b> 비중은 이 도구에 없는 예금·채권으로 채우세요. <span class="alm-over">초과(+)</span>는 위험 과다, <span class="alm-under">미달(−)</span>은 매수 여력. 참고용·투자권유 아님.</div>`;
  box.hidden = false;
}

function pfBuildEditor() {
  const grid = document.getElementById("pfGrid");
  if (!grid || !window.__HOLDABLE__) return;
  const held = pfGet();
  const byCat = {};
  window.__HOLDABLE__.forEach((a) => { (byCat[a.category] = byCat[a.category] || []).push(a); });
  let html = "";
  Object.keys(byCat).forEach((c) => {
    html += `<div class="pf-cat">${PF_CAT[c] || c}</div>`;
    byCat[c].forEach((a) => {
      const v = held[a.key] != null ? held[a.key] : "";
      html += `<label class="pf-row"><span class="pf-name">${a.label}</span>`
        + `<input class="pf-input" type="number" min="0" step="any" inputmode="numeric" data-key="${a.key}" value="${v}" placeholder="₩"></label>`;
    });
  });
  grid.innerHTML = html;
}

function pfRead() {
  const out = {};
  document.querySelectorAll("#pfGrid .pf-input").forEach((i) => {
    const v = parseFloat(i.value);
    if (!isNaN(v) && v > 0) out[i.dataset.key] = v;
  });
  return out;
}

async function pfCompute() {
  const holdings = pfRead();
  pfSet(holdings);
  const box = document.getElementById("pfResult");
  if (!box) return;
  if (!Object.keys(holdings).length) {
    box.innerHTML = `<div class="pf-empty">보유 자산을 입력하면 포트폴리오 위험을 계산합니다. <b>보유 편집</b>으로 시작하세요.</div>`;
    renderAllocMine(0, []);
    return;
  }
  box.innerHTML = `<div class="pf-empty">계산 중…</div>`;
  let r;
  try {
    r = await fetch("/api/portfolio", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ holdings }),
    }).then((x) => x.json());
  } catch (e) { box.innerHTML = `<div class="pf-empty">계산 실패(네트워크).</div>`; return; }
  if (!r || !r.ok) { box.innerHTML = `<div class="pf-empty">${(r && r.reason) || "계산 불가"}</div>`; return; }
  const sgn = (p) => (p > 0 ? "pos" : p < 0 ? "neg" : "zero");
  const pnl = (p, k) => `<b class="pf-pnl ${sgn(p)}">${p >= 0 ? "+" : ""}${p.toFixed(2)}%</b> <small>₩${pfKRW(k)}</small>`;
  const bars = r.breakdown.map((b) => `<span class="pf-wseg" style="width:${b.weight}%" title="${b.label} ${b.weight}%"></span>`).join("");
  const legend = r.breakdown.slice(0, 6).map((b) => `<span class="pf-leg">${b.label} <b>${b.weight}%</b></span>`).join("");
  box.innerHTML =
    `<div class="pf-top"><span>평가액 <b>₩${pfKRW(r.total_krw)}</b> · ${r.n_holdings}종</span>`
    + `<span>연 변동성 <b>${r.vol_annual_pct}%</b></span></div>`
    + `<div class="pf-vars">`
    + `<div class="pf-varcard"><span class="pf-vlbl">하루 손실 가능액 · VaR 95%</span><span class="pf-vval">-${r.var95_pct}%</span><span class="pf-vkrw">₩${pfKRW(r.var95_krw)}</span><span class="pf-vfreq">약 20일 중 1일은 이보다 더 빠질 수 있어요 — 흔한 범위</span></div>`
    + `<div class="pf-varcard hi"><span class="pf-vlbl">나쁜 날 · VaR 99%</span><span class="pf-vval">-${r.var99_pct}%</span><span class="pf-vkrw">₩${pfKRW(r.var99_krw)}</span><span class="pf-vfreq">약 100일 중 1일 꼴의 드문 손실 추정</span></div>`
    + `</div>`
    + `<div class="pf-pnlrow"><span class="pf-pnllbl">손익</span> 1일 ${pnl(r.pnl_1d_pct, r.pnl_1d_krw)} · 1주 ${pnl(r.pnl_1w_pct, r.pnl_1w_krw)} · 1개월 ${pnl(r.pnl_1m_pct, r.pnl_1m_krw)}</div>`
    + `<div class="pf-bar">${bars}</div><div class="pf-legend">${legend}</div>`
    + `<div class="pf-foot">역사적 VaR · 최근 ${r.window_days}거래일 분포 · 환율 포함(원화 기준) · 참고용, 투자권유 아님</div>`;
  renderAllocMine(r.total_krw, r.breakdown);
}

function initPortfolio() {
  const sec = document.getElementById("portfolio");
  if (!sec || !window.__HOLDABLE__ || !window.__HOLDABLE__.length) return;
  pfBuildEditor();
  const ed = document.getElementById("pfEditor");
  document.getElementById("pfEditBtn").addEventListener("click", () => { ed.hidden = !ed.hidden; });
  document.getElementById("pfClose").addEventListener("click", () => { ed.hidden = true; });
  document.getElementById("pfApply").addEventListener("click", () => { pfCompute(); ed.hidden = true; });
  document.getElementById("pfClear").addEventListener("click", () => { pfSet({}); pfBuildEditor(); pfCompute(); });
  if (Object.keys(pfGet()).length) pfCompute();
}

/* ── 초기화 ── */
document.addEventListener("DOMContentLoaded", () => {
  applyTimes();
  renderBriefing();
  setInterval(applyTimes, 60000); // 상대시간 갱신
  const sparks = document.querySelectorAll("canvas.spark");
  if (sparks.length) pool([...sparks], drawSpark, 6);
  applyFavs();
  restoreKrw();
  restoreEasy();
  renderYieldCurve();
  renderCorrelations();
  renderRegime();
  initPortfolio();
  autoRefreshLoop();
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
});

window.openChart = openChart;
window.closeModal = closeModal;
window.collectNow = collectNow;
window.toggleKrw = toggleKrw;
window.toggleEasy = toggleEasy;
