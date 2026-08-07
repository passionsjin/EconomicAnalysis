/* 자금 회전지도 — RRG 산점도.

   인코딩(스펙 4.4절):
     색   = 위험 성격 연속 발산 스케일(공격 <- 중립 -> 방어). 임계선 3분류를 쓰지 않는다 —
            경계 자산이 룩백에 따라 색이 깜빡이는 것을 막으려는 것이 연속 스케일의 존재 이유다.
     모양 = 자산군 5종(주식 원 / 채권 사각 / 실물 삼각 / 코인 마름모 / 통화 별)
     라벨 = 개별 정체성(점 옆에 직접 부착 — 범례를 눈으로 왕복할 필요가 없다)
   지도 2(지역)는 위험성격을 쓰지 않으므로 색·모양을 정체성 보조에 돌려 쓴다(아래 참조).

   색상값은 dataviz 검증기 통과분: 두 극 #e66767 <-> #3987e5 가
   표면 #151b26 기준 CVD dE 19.2(protan)/31.4(tritan), 정상시야 29.0, 대비 >=3:1. */

const FLOW_SHAPE = {
  equity: 'circle', bond: 'rect', real: 'triangle',
  crypto: 'rectRot', fx: 'star', region: 'circle',
};

const FLOW_ATTACK = [230, 103, 103];   // #e66767 공격
const FLOW_MID    = [138, 148, 166];   // #8a94a6 중립(앱 --muted)
const FLOW_DEFEND = [57, 135, 229];    // #3987e5 방어

/* 지역 지도는 위험성격을 쓰지 않으므로 색 채널이 비어 있다 — 정체성에 쓴다.
   6개 궤적이 전부 같은 색이면 교차하는 순간 어느 꼬리가 누구 것인지 알 수 없고,
   궤적을 따라가는 것이 RRG 의 존재 이유다. dataviz 카테고리 팔레트 다크 스텝.
   색은 순위가 아니라 개체를 따라야 하므로(rows 는 상대강도 순 정렬이라 인덱스를 쓰면
   순위가 바뀔 때 생존 자산의 색이 재배정된다) 지표키에 고정 배정한다.
   정체성은 점 옆 라벨이 담당하고 색은 궤적 추적 보조다 — 색만으로 구분하지 않는다.

   6색을 쓰면 검증기가 하드 FAIL 한다(magenta<->aqua deutan dE 1.6, violet<->blue 정상시야 9.8).
   all-pairs 를 통과하는 3색만 쓰고, 모자란 구분은 모양으로 채운다 —
   모양은 선진(원)/신흥(삼각)이라 임의 채널이 아니라 뜻이 있다.
   3색 all-pairs 검증: 최악쌍 CVD dE 9.4(deutan), 정상시야 20.9, 대비 >=3:1 전부 PASS. */
const FLOW_REGION_HUE = {
  sp500:    [57, 135, 229],    // #3987e5 blue    선진
  eustoxx:  [217, 89, 38],     // #d95926 orange  선진
  nikkei:   [25, 158, 112],    // #199e70 aqua    선진
  kospi:    [57, 135, 229],    // blue            신흥
  shanghai: [217, 89, 38],     // orange          신흥
  hangseng: [25, 158, 112],    // aqua            신흥
};
/* 선진 = 원, 신흥 = 삼각. 색 3종 x 모양 2종 = 6개 조합이 전부 고유하다. */
const FLOW_REGION_SHAPE = {
  sp500: 'circle', eustoxx: 'circle', nikkei: 'circle',
  kospi: 'triangle', shanghai: 'triangle', hangseng: 'triangle',
};

/* risk(VIX 급등일 평균수익률 %)를 발산 스케일에 태운다.
   domain 은 데이터의 최대 절대값으로 잡아 0 기준 대칭을 유지한다 —
   양팔을 각자 최대값으로 늘이면 약한 방어 신호가 강해 보여 오해를 부른다. */
function flowRiskRgb(risk, domain) {
  if (risk === null || risk === undefined) return FLOW_MID.slice();
  const t = Math.max(-1, Math.min(1, risk / (domain || 1)));
  const pole = t < 0 ? FLOW_ATTACK : FLOW_DEFEND;
  const w = Math.abs(t);
  return FLOW_MID.map((v, i) => Math.round(v + (pole[i] - v) * w));
}

function flowRgba(c, a) {
  return a === undefined ? `rgb(${c[0]},${c[1]},${c[2]})` : `rgba(${c[0]},${c[1]},${c[2]},${a})`;
}

function flowCssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/* 축은 데이터에 맞춰 자동 스케일한다. 점구름 지름 중앙값이 4~5 로 좁아서
   94~106 고정축으로 그리면 점이 전부 가운데 뭉쳐 보인다. */
function flowRange(vals, pad) {
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = Math.max(hi - lo, 0.6);
  return { min: lo - span * pad, max: hi + span * pad };
}

function flowDrawRRG(canvasId, map, opts) {
  const el = document.getElementById(canvasId);
  if (!el || !map || !map.rows || !map.rows.length) return;
  const useRisk = !!(opts && opts.useRisk);
  const ink = flowCssVar('--txt', '#e6ebf2');
  const muted = flowCssVar('--muted', '#8a94a6');

  const risks = map.rows.map((r) => r.risk).filter((v) => v !== null && v !== undefined);
  const domain = risks.length ? Math.max(...risks.map(Math.abs)) : 1;

  const surface = flowCssVar('--card', '#151b26');
  const isLast = (c) => c.dataIndex === c.dataset.data.length - 1;

  const datasets = map.rows.map((r) => {
    const rgb = useRisk ? flowRiskRgb(r.risk, domain)
                        : (FLOW_REGION_HUE[r.key] || [138, 148, 166]);
    const shape = useRisk ? (FLOW_SHAPE[r.group] || 'circle')
                          : (FLOW_REGION_SHAPE[r.key] || 'circle');
    /* star 는 Chart.js 에서 채우기가 아니라 선으로만 그려진다 —
       다른 모양처럼 표면색 링을 두르면 별 자체가 표면색이 되어 사라진다.
       이 스타일만 테두리를 계열색으로 칠한다. */
    const strokeOnly = shape === 'star';
    return {
      label: r.label,
      data: r.tail.map((p) => ({ x: p.x, y: p.y, date: p.date })),
      showLine: true,
      /* 꼬리 선은 반투명으로 물러나게 한다 — 10개 궤적이 불투명하면 서로 뒤엉켜
         어느 꼬리가 어느 점 것인지 추적할 수 없다. 꼬리는 맥락, 현재 위치가 메시지다. */
      borderColor: flowRgba(rgb, 0.3),
      borderWidth: 1.2,
      tension: 0,                      // 곡선은 교차를 더 헷갈리게 만든다
      backgroundColor: flowRgba(rgb),
      pointStyle: shape,
      /* 꼬리 점은 작고 희미하게, 현재 위치만 크고 선명하게 */
      pointRadius: (c) => (isLast(c) ? 7 : 2.2),
      pointBackgroundColor: (c) => flowRgba(rgb, isLast(c) ? 1 : 0.45),
      /* 겹치는 마크는 표면색 링으로 분리한다(선으로만 그려지는 star 는 예외) */
      pointBorderColor: (c) => (strokeOnly
        ? flowRgba(rgb, isLast(c) ? 1 : 0.45)
        : (isLast(c) ? surface : 'rgba(0,0,0,0)')),
      pointBorderWidth: (c) => (strokeOnly ? (isLast(c) ? 2.5 : 1) : (isLast(c) ? 2 : 0)),
      pointHoverRadius: 9,
    };
  });

  const xs = map.rows.flatMap((r) => r.tail.map((p) => p.x));
  const ys = map.rows.flatMap((r) => r.tail.map((p) => p.y));
  const xr = flowRange(xs.concat([100]), 0.12);
  const yr = flowRange(ys.concat([100]), 0.12);

  /* 100 기준 십자선 + 사분면 이름 */
  const crosshair = {
    id: 'flowCrosshair',
    beforeDatasetsDraw(chart) {
      const { ctx, chartArea: a, scales } = chart;
      const cx = scales.x.getPixelForValue(100), cy = scales.y.getPixelForValue(100);
      ctx.save();
      ctx.strokeStyle = 'rgba(138,148,166,.42)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(a.left, cy); ctx.lineTo(a.right, cy);
      ctx.moveTo(cx, a.top); ctx.lineTo(cx, a.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = muted;
      ctx.font = '11px Inter, system-ui, sans-serif';
      ctx.fillText('주도', a.right - 32, a.top + 14);
      ctx.fillText('개선', a.left + 8, a.top + 14);
      ctx.fillText('약화', a.right - 32, a.bottom - 8);
      ctx.fillText('지체', a.left + 8, a.bottom - 8);
      ctx.restore();
    },
  };

  /* 현재 위치 옆 라벨 직접 부착 */
  const tags = {
    id: 'flowTags',
    afterDatasetsDraw(chart) {
      const { ctx } = chart;
      ctx.save();
      ctx.font = '600 11px Inter, system-ui, sans-serif';
      ctx.fillStyle = ink;
      ctx.textBaseline = 'middle';
      const placed = [];
      chart.data.datasets.forEach((ds, i) => {
        const meta = chart.getDatasetMeta(i);
        const last = meta.data[meta.data.length - 1];
        if (!last) return;
        /* 라벨끼리 세로로 겹치면 아래로 밀어 충돌을 피한다 */
        let y = last.y;
        while (placed.some((p) => Math.abs(p.y - y) < 12 && Math.abs(p.x - last.x) < 68)) y += 12;
        placed.push({ x: last.x, y });
        ctx.fillText(ds.label, last.x + 11, y);
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
      layout: { padding: { right: 64, top: 6 } },   // 오른쪽 라벨 잘림 방지
      scales: {
        x: {
          min: xr.min, max: xr.max,
          title: { display: true, text: '상대강도 (3개월 위치) →', color: muted, font: { size: 11 } },
          ticks: { color: muted, font: { size: 10 } },
          grid: { color: 'rgba(138,148,166,.10)' },
        },
        y: {
          min: yr.min, max: yr.max,
          title: { display: true, text: '상대모멘텀 (1개월 방향) →', color: muted, font: { size: 11 } },
          ticks: { color: muted, font: { size: 10 } },
          grid: { color: 'rgba(138,148,166,.10)' },
        },
      },
      plugins: {
        legend: { display: false },     // 정체성은 점 옆 라벨이 담당
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

  /* 색 범례의 양끝 수치를 실제 domain 으로 채운다 */
  if (useRisk) {
    const lo = document.getElementById('flow-risk-lo');
    const hi = document.getElementById('flow-risk-hi');
    if (lo) lo.textContent = `공격 −${domain.toFixed(2)}%`;
    if (hi) hi.textContent = `방어 +${domain.toFixed(2)}%`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  flowDrawRRG('flow-asset', window.__FLOW_ASSET__, { useRisk: true });
  flowDrawRRG('flow-region', window.__FLOW_REGION__, { useRisk: false });
});
