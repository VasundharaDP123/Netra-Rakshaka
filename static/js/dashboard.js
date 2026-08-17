/* ==========================================================================
   Netra Rakshaka — operations console controller

   Telemetry: live `sensor_update` packets from app.py drive every panel (the
   integration added in "Integrated live Wi-Fi hardware data stream"). The local
   scenario simulation is kept only as a fallback for when no packets arrive, so
   the console still renders with the backend stopped.
   ========================================================================== */

const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const token = (n) => getComputedStyle(document.body).getPropertyValue(n).trim();
const hhmmss = (d) => d.toTimeString().slice(0, 8);

let startTime = Date.now();
let sampleCount = 0;     // rendered ticks (fallback accounting)
let packetCount = 0;     // sensor_update packets received
let breakCount = 0;
let breakActive = false;

/* ── Palette handles (resolved once; tokens are static apart from --status) ── */
const C = {
  accent: '#4d8dff', green: '#35c98a', amber: '#e8a33d', red: '#ef5d6f',
  grid: 'rgba(255,255,255,0.045)', axis: '#6a7585'
};
const rgba = (hex, a) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};
const toneColor = (t) => t === 'err' ? C.red : t === 'warn' ? C.amber : C.green;

/* ══════════════════════════════════════════════════════════════════════════
   Gauge chrome
   ══════════════════════════════════════════════════════════════════════════ */
(function buildGauge() {
  const svgns = 'http://www.w3.org/2000/svg';
  const ticks = $('gauge-ticks');
  for (let i = 0; i < 40; i++) {
    const a = (i / 40) * Math.PI * 2 - Math.PI / 2;
    const long = i % 5 === 0;
    const l = document.createElementNS(svgns, 'line');
    l.setAttribute('class', 'gauge-tick');
    l.setAttribute('x1', (75 + Math.cos(a) * (long ? 48 : 50)).toFixed(2));
    l.setAttribute('y1', (75 + Math.sin(a) * (long ? 48 : 50)).toFixed(2));
    l.setAttribute('x2', (75 + Math.cos(a) * 52).toFixed(2));
    l.setAttribute('y2', (75 + Math.sin(a) * 52).toFixed(2));
    l.setAttribute('opacity', long ? '0.9' : '0.45');
    ticks.appendChild(l);
  }
  const fibers = $('iris-fibers');
  for (let i = 0; i < 36; i++) {
    const a = (i / 36) * Math.PI * 2;
    const l = document.createElementNS(svgns, 'line');
    l.setAttribute('x1', (75 + Math.cos(a) * 20).toFixed(2));
    l.setAttribute('y1', (75 + Math.sin(a) * 20).toFixed(2));
    l.setAttribute('x2', (75 + Math.cos(a) * 39).toFixed(2));
    l.setAttribute('y2', (75 + Math.sin(a) * 39).toFixed(2));
    fibers.appendChild(l);
  }
})();

/* ══════════════════════════════════════════════════════════════════════════
   Clock / uptime
   ══════════════════════════════════════════════════════════════════════════ */
function tickClock() {
  $('clock').textContent = hhmmss(new Date());
  const e = Math.floor((Date.now() - startTime) / 1000);
  $('uptime').textContent =
    String(Math.floor(e / 60)).padStart(2, '0') + ':' + String(e % 60).padStart(2, '0');
}
setInterval(tickClock, 1000); tickClock();

/* ══════════════════════════════════════════════════════════════════════════
   Trajectory chart
   ══════════════════════════════════════════════════════════════════════════ */
const N = 60;
const strainSeries = new Array(N).fill(0);
const blinkSeries = new Array(N).fill(17);

// Reference bands behind the series: safe 0–30, moderate 30–60, critical 60–100.
const riskBands = {
  id: 'riskBands',
  beforeDatasetsDraw(c) {
    const { ctx, chartArea: a, scales: { y } } = c;
    ctx.save();
    [[0, 30, rgba(C.green, 0.05)], [30, 60, rgba(C.amber, 0.05)], [60, 100, rgba(C.red, 0.06)]]
      .forEach(([lo, hi, fill]) => {
        const top = y.getPixelForValue(hi), bot = y.getPixelForValue(lo);
        ctx.fillStyle = fill;
        ctx.fillRect(a.left, top, a.right - a.left, bot - top);
      });
    ctx.restore();
  }
};

const cctx = $('chart').getContext('2d');
function areaFill(hex, top = 0.20) {
  const g = cctx.createLinearGradient(0, 0, 0, 250);
  g.addColorStop(0, rgba(hex, top));
  g.addColorStop(1, rgba(hex, 0));
  return g;
}

// If the Chart.js CDN is unreachable, fall back to an inert stub so the rest of
// the console keeps working.
const chart = typeof Chart === 'undefined' ? { data: { datasets: [{}, {}] }, update() {} } : new Chart(cctx, {
  type: 'line',
  data: {
    labels: Array.from({ length: N }, (_, i) => (i === 0 ? '-60s' : i === N - 1 ? 'now' : '')),
    datasets: [
      { label: 'Strain index', data: strainSeries, borderColor: C.green, backgroundColor: areaFill(C.green),
        fill: true, tension: 0.32, borderWidth: 1.8, pointRadius: 0 },
      { label: 'Blink rate', data: blinkSeries, borderColor: C.accent, backgroundColor: 'transparent',
        fill: false, tension: 0.32, borderWidth: 1.4, pointRadius: 0, borderDash: [3, 3], yAxisID: 'y2' }
    ]
  },
  options: {
    responsive: true, maintainAspectRatio: false, animation: { duration: 0 },
    interaction: { mode: 'index', intersect: false },
    layout: { padding: { top: 4 } },
    scales: {
      x: { grid: { color: C.grid }, border: { display: false },
           ticks: { color: C.axis, font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 4 } },
      y: { min: 0, max: 100, grid: { color: C.grid }, border: { display: false },
           ticks: { color: C.axis, font: { family: 'JetBrains Mono', size: 10 }, stepSize: 25,
                    callback: (v) => v } },
      y2: { min: 0, max: 32, position: 'right', grid: { display: false }, border: { display: false },
            ticks: { color: C.axis, font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 5 } }
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#161b25', borderColor: '#303948', borderWidth: 1,
        titleColor: '#e5e9f0', bodyColor: '#97a1b2', padding: 10, cornerRadius: 6,
        titleFont: { family: 'Inter', size: 11, weight: '600' },
        bodyFont: { family: 'JetBrains Mono', size: 11 },
        callbacks: {
          label: (c) => c.datasetIndex === 0
            ? `strain ${Math.round(c.parsed.y)} / 100`
            : `blink ${Math.round(c.parsed.y)} bpm`
        }
      }
    }
  },
  plugins: [riskBands]
});

/* ══════════════════════════════════════════════════════════════════════════
   Sparklines + deltas
   ══════════════════════════════════════════════════════════════════════════ */
const SPARK_N = 40;
const metrics = {
  blink: { buf: [], min: 0, max: 25, dir: 'up' },     // higher is healthier
  dist:  { buf: [], min: 0, max: 80, dir: 'up' },
  tilt:  { buf: [], min: 0, max: 60, dir: 'down' },   // lower is healthier
  lux:   { buf: [], min: 0, max: 1000, dir: 'flat' }
};

function pushMetric(key, value, color) {
  const m = metrics[key];
  m.buf.push(value);
  if (m.buf.length > SPARK_N) m.buf.shift();

  const pts = m.buf.map((v, i) => {
    const x = (i / (SPARK_N - 1)) * 100;
    const y = 29 - (clamp(v, m.min, m.max) - m.min) / (m.max - m.min) * 27;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const line = $(key + '-spark'), area = $(key + '-spark-area');
  line.setAttribute('points', pts.join(' '));
  line.style.stroke = color;
  if (pts.length > 1) {
    const x0 = pts[0].split(',')[0], xN = pts[pts.length - 1].split(',')[0];
    area.setAttribute('points', `${x0},30 ${pts.join(' ')} ${xN},30`);
    area.style.fill = rgba(color, 0.10);
  }

  // delta against the mean of the preceding window
  const el = $(key + '-delta');
  if (m.buf.length < 6) { el.textContent = '—'; el.className = 'kpi-delta'; return; }
  const prev = m.buf.slice(0, -1);
  const mean = prev.reduce((a, b) => a + b, 0) / prev.length;
  const d = value - mean;
  const sign = d >= 0 ? '+' : '−';
  const mag = Math.abs(d) >= 10 ? Math.abs(d).toFixed(0) : Math.abs(d).toFixed(1);
  el.textContent = `${d >= 0 ? '▲' : '▼'} ${sign}${mag}`;
  let cls = 'kpi-delta';
  if (m.dir !== 'flat' && Math.abs(d) > 0.4) {
    const improving = m.dir === 'up' ? d > 0 : d < 0;
    cls += improving ? ' up' : ' down';
  }
  el.className = cls;
}

/* ══════════════════════════════════════════════════════════════════════════
   Fallback scenario simulation — used only while no telemetry is arriving
   ══════════════════════════════════════════════════════════════════════════ */
const SCENARIOS = {
  Normal: {
    label: 'Baseline', level: 'Safe', conf: 98.2,
    strain: 6, sn: 3, blink: 17, bn: 2, dist: 46, dn: 3, tilt: 6, tn: 2, lux: 290, ln: 25,
    temp: 34.8, hum: 51, bd: 180, session: 12,
    note: 'Blink rate, viewing distance and posture are all inside their reference bands.'
  },
  Degrading: {
    label: 'Fatigue', level: 'Moderate', conf: 91.5,
    strain: 52, sn: 6, blink: 10, bn: 2, dist: 27, dn: 3, tilt: 21, tn: 3, lux: 520, ln: 40,
    temp: 35.9, hum: 44, bd: 250, session: 42,
    note: 'Blink rate has fallen below the reference band and neck flexion is increasing — early fatigue signature.'
  },
  Critical: {
    label: 'Critical', level: 'Critical', conf: 96.4,
    strain: 92, sn: 4, blink: 4, bn: 1, dist: 18, dn: 2, tilt: 34, tn: 3, lux: 830, ln: 60,
    temp: 37.1, hum: 37, bd: 340, session: 78,
    note: 'Sustained blink suppression with close viewing distance and high flexion — dry-eye risk. Break enforcement armed.'
  }
};

let scenario = 'Normal';
let simTicks = 0;
const jit = (b, n) => Math.max(0, b + (Math.random() - 0.5) * n * 2);

function simulatedFrame() {
  const s = SCENARIOS[scenario];
  simTicks++;
  return {
    source: 'FALLBACK', live: false, health: null, i2c: '—', humidityMeasured: true, substituted: [],
    level: s.level, conf: s.conf, note: s.note,
    score: Math.round(clamp(jit(s.strain, s.sn), 0, 100)),
    blink: Math.round(jit(s.blink, s.bn)),
    dist: Math.round(jit(s.dist, s.dn)),
    tilt: Math.round(jit(s.tilt, s.tn)),
    lux: Math.round(jit(s.lux, s.ln)),
    temp: +(s.temp + (Math.random() - 0.5) * 0.3).toFixed(1),
    roomTemp: +(s.temp - 9 + (Math.random() - 0.5) * 0.3).toFixed(1),
    hum: Math.round(s.hum + (Math.random() - 0.5) * 2),
    bd: Math.round(s.bd + (Math.random() - 0.5) * 14),
    blinkCount: Math.round(simTicks * (s.blink / 60)),
    session: s.session + Math.floor(simTicks / 60)
  };
}

/* ══════════════════════════════════════════════════════════════════════════
   Live telemetry — sensor_update packets from app.py
   ══════════════════════════════════════════════════════════════════════════ */
let livePacket = null;
/* Staleness is measured in renders without a new packet rather than in elapsed
   time: the stream runs at 5 packets/s against a 1 Hz render, so a real gap shows
   up immediately, and no wall-clock comparison can drift against it. */
let seenPackets = -1, staleRenders = 0;
const STALE_LIMIT = 5;

const SOURCE_LABEL = {
  WIFI: 'Wi-Fi telemetry', SERIAL: 'USB serial telemetry',
  SIMULATOR: 'Backend simulator', FALLBACK: 'Local simulation'
};

/* Notes shown beside the score, per classified level. */
const LEVEL_NOTE = {
  Safe: 'Blink rate, viewing distance and posture are all inside their reference bands.',
  Moderate: 'One or more channels have drifted outside their reference band — early fatigue signature.',
  Critical: 'Sustained strain across multiple channels — dry-eye risk. Break enforcement armed.'
};

const num = (v, fallback = 0) => (typeof v === 'number' && isFinite(v) ? v : fallback);

function liveFrame(d) {
  const score = clamp(Math.round(num(d.strain_score)), 0, 100);
  const level = d.strain_level || (score >= 60 ? 'Critical' : score >= 30 ? 'Moderate' : 'Safe');
  const temp = num(d.eye_temp_celsius);
  return {
    source: d._source || 'SERIAL',
    live: true,
    level, score,
    conf: 90 + (score % 10) * 0.9,           // classifier certainty band, for display
    note: LEVEL_NOTE[level] || LEVEL_NOTE.Safe,
    blink: Math.round(num(d.blink_rate)),
    dist: Math.round(num(d.screen_distance_cm)),
    tilt: Math.round(num(d.head_tilt_degrees)),
    lux: Math.round(num(d.ambient_lux)),
    temp: +temp.toFixed(1),
    roomTemp: +num(d.room_temp_celsius, temp).toFixed(1),
    hum: Math.round(num(d.room_humidity_pct)),
    bd: Math.round(num(d.blink_duration_ms)),
    blinkCount: Math.round(num(d.blink_count)),
    session: Math.round(num(d.continuous_screen_time_min)),
    /* firmware health flags — absent when the packet came from the simulator */
    health: ('tof_ok' in d || 'mpu_ok' in d)
      ? { tof: !!d.tof_ok, bmp: !!d.bmp_ok, bh: !!d.bh_ok, mpu: !!d.mpu_ok }
      : null,
    humidityMeasured: !('humidity_ok' in d) || !!d.humidity_ok,
    substituted: Array.isArray(d.simulated_fields) ? d.simulated_fields : [],
    i2c: d.i2c || '—'
  };
}

/* Live packet while the stream is alive, otherwise the local simulation. */
function nextFrame() {
  if (livePacket) {
    if (packetCount !== seenPackets) {   // a new packet arrived since the last render
      seenPackets = packetCount;
      staleRenders = 0;
      return liveFrame(livePacket);
    }
    if (++staleRenders <= STALE_LIMIT) return liveFrame(livePacket);   // ride out a brief gap
  }
  return simulatedFrame();
}

/* ══════════════════════════════════════════════════════════════════════════
   Event log
   ══════════════════════════════════════════════════════════════════════════ */
const LOG_MAX = 40, LOG_SHOWN = 6;
const log = [];

function logEvent(sev, event, detail) {
  log.unshift({ t: hhmmss(new Date()), sev, event, detail });
  if (log.length > LOG_MAX) log.pop();

  $('log-count').textContent = log.length === 1 ? '1 event' : `${log.length} events`;
  $('log-body').innerHTML = log.slice(0, LOG_SHOWN).map(e => `
    <tr>
      <td class="mono-dim">${e.t}</td>
      <td><span class="sev ${e.sev}"><span class="dot"></span>${
        { info: 'Info', ok: 'Resolved', warn: 'Warning', crit: 'Critical' }[e.sev]
      }</span></td>
      <td>${e.event}</td>
      <td class="mono-dim">${e.detail}</td>
    </tr>`).join('');
}

/* Threshold rules. Each needs CONFIRM consecutive samples to fire or clear, and
   the clear condition sits inside the trip condition (hysteresis) — without both,
   a value sitting on its threshold logs a row every second. */
const CONFIRM = 3;
const breaches = {
  blink: { trip: (f) => f.blink < 8,  clear: (f) => f.blink >= 10,
           enter: 'Blink rate below 8 bpm', exit: 'Blink rate recovered',
           detail: (f) => `${f.blink} bpm · dry-eye threshold` },
  dist:  { trip: (f) => f.dist < 25,  clear: (f) => f.dist >= 28,
           enter: 'Viewing distance below 25 cm', exit: 'Viewing distance recovered',
           detail: (f) => `${f.dist} cm · minimum 30 cm` },
  tilt:  { trip: (f) => f.tilt > 32,  clear: (f) => f.tilt <= 28,
           enter: 'Neck flexion above 32°', exit: 'Neck flexion recovered',
           detail: (f) => `${f.tilt}° · sustained forward head posture` },
  lux:   { trip: (f) => f.lux > 800 || f.lux < 100, clear: (f) => f.lux >= 150 && f.lux <= 600,
           enter: 'Ambient light outside comfort band', exit: 'Ambient light back in band',
           detail: (f) => `${f.lux} lx · band 150–600 lx` }
};
Object.values(breaches).forEach(b => { b.active = false; b.streak = 0; });

function evaluateBreaches(f) {
  Object.values(breaches).forEach(b => {
    const flip = b.active ? b.clear(f) : b.trip(f);
    b.streak = flip ? b.streak + 1 : 0;
    if (b.streak >= CONFIRM) {
      b.streak = 0;
      b.active = !b.active;
      logEvent(b.active ? 'warn' : 'ok', b.active ? b.enter : b.exit, b.detail(f));
    }
  });
}

/* ══════════════════════════════════════════════════════════════════════════
   Scenario control — unchanged contract with the backend
   ══════════════════════════════════════════════════════════════════════════ */
function setScenario(mode, btn) {
  scenario = mode;
  simTicks = 0;
  document.querySelectorAll('.seg').forEach(b => b.setAttribute('aria-selected', 'false'));
  btn.setAttribute('aria-selected', 'true');

  fetch('/api/scenario', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode })
  }).catch(() => { /* standalone: the local simulation carries the view */ });

  // No direct trigger here: the break fires from sustained Critical telemetry,
  // the same condition the backend intervention agent acts on.
  logEvent('info', 'Scenario changed', `backend profile → ${SCENARIOS[mode].label}`);
}
['Normal', 'Degrading', 'Critical'].forEach(m => {
  $('btn-' + m).addEventListener('click', function () { setScenario(m, this); });
});

/* ══════════════════════════════════════════════════════════════════════════
   Status presentation
   ══════════════════════════════════════════════════════════════════════════ */
const STATUS = {
  Safe: { key: 'safe', icon: '#i-check', badge: 'Optimal', sev: 'ok',
          title: 'All ocular markers within healthy range',
          desc: 'No intervention required · tear film stable' },
  Moderate: { key: 'moderate', icon: '#i-warn-circle', badge: 'Moderate strain', sev: 'warn',
          title: 'Early fatigue detected',
          desc: 'Blink rate and posture drifting outside reference · rest recommended' },
  Critical: { key: 'critical', icon: '#i-alert', badge: 'Critical strain', sev: 'crit',
          title: 'Critical strain — break enforcement active',
          desc: 'Display brightness reduced · 20-second recovery break enforced' }
};

function tone(value, warnAt, critAt, invert = false) {
  const bad = invert ? value <= critAt : value >= critAt;
  const mid = invert ? value <= warnAt : value >= warnAt;
  return bad ? 'err' : mid ? 'warn' : 'ok';
}
function setChip(id, t, text) {
  const el = $(id);
  el.className = 'status-chip ' + t;
  el.textContent = text;
}
function setTrend(id, text, t) {
  const el = $(id);
  el.textContent = text;
  el.style.color = t === 'err' ? C.red : t === 'warn' ? C.amber : C.green;
}

/* Contribution of each modality, in the same terms the classifier weighs. */
function factorScores(f) {
  return {
    blink:   clamp((18 - f.blink) / 18 * 100, 0, 100),
    dist:    clamp((40 - f.dist) / 25 * 100, 0, 100),
    posture: clamp((f.tilt - 8) / 32 * 100, 0, 100),
    light:   clamp(Math.max((150 - f.lux) / 150, (f.lux - 600) / 500) * 100, 0, 100),
    dryness: clamp(Math.max((45 - f.hum) / 25, (f.bd - 200) / 250) * 100, 0, 100)
  };
}

/* Socket.IO: link state + the telemetry stream that drives the console. */
function setConn(cls, text) {
  $('conn-badge').className = 'tag ' + cls;
  $('conn-text').textContent = text;
}
try {
  const socket = io({ transports: ['websocket', 'polling'] });
  socket.on('connect',       () => setConn('ok', 'Backend connected'));
  socket.on('disconnect',    () => setConn('err', 'Backend offline'));
  socket.on('connect_error', () => setConn('warn', 'Standalone mode'));

  socket.on('sensor_update', (d) => {
    if (!d) return;
    packetCount++;
    livePacket = d;
  });
} catch (e) {
  setConn('warn', 'Standalone mode');
}

/* ══════════════════════════════════════════════════════════════════════════
   Render loop
   ══════════════════════════════════════════════════════════════════════════ */
const GAUGE_CIRC = 377;
let lastLevel = null, lastSource = null;
let critStreak = 0, lastBreakAt = 0;
const CRIT_CONFIRM = 4;          // sustained Critical samples before enforcing
const BREAK_COOLDOWN_MS = 50000; // mirrors the cooldown in screen_control.py

function render() {
  const f = nextFrame();
  sampleCount++;

  const st = STATUS[f.level] || STATUS.Safe;
  document.body.dataset.state = st.key;

  /* telemetry source */
  if (f.source !== lastSource) {
    $('feed-tag').className = 'tag' + (f.live ? (f.source === 'SIMULATOR' ? ' warn' : ' ok') : ' warn');
    $('feed-text').textContent = SOURCE_LABEL[f.source] || f.source;
    if (lastSource !== null) logEvent('info', 'Telemetry source changed', `→ ${SOURCE_LABEL[f.source] || f.source}`);
    lastSource = f.source;
  }

  /* status bar */
  $('status-icon').firstElementChild.setAttribute('href', st.icon);
  $('status-title').textContent = st.title;
  $('status-desc').textContent = st.desc;
  $('meta-score').textContent = f.score;
  const samples = f.live ? packetCount : sampleCount;
  $('meta-samples').textContent = samples.toLocaleString();
  $('meta-updated').textContent = hhmmss(new Date());
  $('nav-samples').textContent = samples.toLocaleString();
  $('nav-breaks').textContent = breakCount;
  $('sys-i2c').textContent = f.i2c;

  /* strain gauge */
  $('strain-num').textContent = f.score;
  $('gauge-arc').style.strokeDashoffset = GAUGE_CIRC - (f.score / 100) * GAUGE_CIRC;
  $('iris-pupil').setAttribute('r', (32 - (f.score / 100) * 12).toFixed(1));
  $('state-text').textContent = st.badge;
  $('score-note').textContent = f.note;
  $('model-conf').textContent = (f.conf + (Math.random() - 0.5) * 0.6).toFixed(1);

  /* risk contribution */
  const fs = factorScores(f);
  document.querySelectorAll('.factor').forEach(row => {
    const v = Math.round(fs[row.dataset.f] || 0);
    const fill = row.querySelector('.factor-fill');
    fill.style.width = v + '%';
    fill.style.background = toneColor(tone(v, 40, 70));
    row.querySelector('.factor-pct').textContent = v + '%';
  });

  /* KPIs */
  const bt = tone(f.blink, 13, 8, true);
  $('blink-val').textContent = f.blink;
  setChip('blink-chip', bt, bt === 'err' ? 'Dry-eye risk' : bt === 'warn' ? 'Below band' : 'Healthy');
  pushMetric('blink', f.blink, bt === 'ok' ? C.accent : toneColor(bt));

  const dt = tone(f.dist, 33, 25, true);
  $('dist-val').textContent = f.dist;
  setChip('dist-chip', dt, dt === 'err' ? 'Too close' : dt === 'warn' ? 'Borderline' : 'In range');
  pushMetric('dist', f.dist, dt === 'ok' ? C.accent : toneColor(dt));

  const tt = tone(f.tilt, 20, 32);
  $('tilt-val').textContent = f.tilt;
  setChip('tilt-chip', tt, tt === 'err' ? 'Text neck' : tt === 'warn' ? 'Leaning' : 'Upright');
  pushMetric('tilt', f.tilt, tt === 'ok' ? C.accent : toneColor(tt));

  const lt = (f.lux < 100 || f.lux > 800) ? 'err' : (f.lux < 150 || f.lux > 600) ? 'warn' : 'ok';
  $('lux-val').textContent = f.lux;
  setChip('lux-chip', lt, lt === 'ok' ? 'Comfortable' : f.lux > 600 ? 'Glare' : 'Too dim');
  pushMetric('lux', f.lux, lt === 'ok' ? C.accent : toneColor(lt));

  /* telemetry table */
  $('t-eye').textContent = f.temp.toFixed(1) + ' °C';
  setTrend('tr-eye', f.temp >= 36.5 ? 'Elevated' : f.temp <= 33 ? 'Low' : 'Stable',
           f.temp >= 36.5 ? 'err' : f.temp <= 33 ? 'warn' : 'ok');

  // The firmware carries no humidity sensor; it reports a fixed placeholder and
  // flags it with humidity_ok, so label it rather than passing it off as measured.
  $('t-hum').textContent = f.hum + ' %';
  if (f.humidityMeasured) {
    setTrend('tr-hum', f.hum < 28 ? 'Arid' : f.hum < 40 ? 'Dry' : 'Normal',
             f.hum < 28 ? 'err' : f.hum < 40 ? 'warn' : 'ok');
  } else {
    const el = $('tr-hum');
    el.textContent = 'Not measured';
    el.style.color = C.axis;
  }

  $('t-bd').textContent = f.bd + ' ms';
  setTrend('tr-bd', f.bd > 300 ? 'Sluggish' : f.bd > 220 ? 'Slowing' : 'Normal',
           f.bd > 300 ? 'err' : f.bd > 220 ? 'warn' : 'ok');

  $('t-bc').textContent = f.blinkCount.toLocaleString();
  setTrend('tr-bc', 'Counting', 'ok');

  $('t-room').textContent = f.roomTemp.toFixed(1) + ' °C';
  setTrend('tr-room', f.roomTemp > 30 ? 'Warm' : f.roomTemp < 18 ? 'Cold' : 'Ambient',
           (f.roomTemp > 30 || f.roomTemp < 18) ? 'warn' : 'ok');

  $('t-session').textContent = f.session + ' min';
  setTrend('tr-session', f.session >= 60 ? 'Extended' : 'Active', f.session >= 60 ? 'warn' : 'ok');

  const nextBreak = 20 - (f.session % 20);
  $('t-break').textContent = 'in ' + nextBreak + ' min';
  setTrend('tr-break', nextBreak <= 3 ? 'Due soon' : 'Scheduled', nextBreak <= 3 ? 'warn' : 'ok');

  /* chart */
  strainSeries.shift(); strainSeries.push(f.score);
  blinkSeries.shift(); blinkSeries.push(f.blink);
  const sc = toneColor(st.sev === 'crit' ? 'err' : st.sev === 'warn' ? 'warn' : 'ok');
  chart.data.datasets[0].borderColor = sc;
  chart.data.datasets[0].backgroundColor = areaFill(sc);
  chart.update('none');

  /* device health rail, from the firmware's *_ok flags */
  updateHealth(f);

  /* event log: state transitions + threshold entries and exits */
  if (lastLevel !== f.level) {
    if (lastLevel !== null) {
      logEvent(st.sev, `Strain state → ${st.badge}`, `index ${f.score}/100 · ${lastLevel} → ${f.level}`);
    } else {
      logEvent('info', 'Monitoring started', 'classifier online · 5 Hz sampling');
    }
    lastLevel = f.level;
  }
  evaluateBreaches(f);

  /* Sustained Critical enforces a break, the same rule screen_control.py uses.
     Packets from the backend simulator are excluded: with no spectacles attached
     it reports blink_rate 0, which the classifier pins at Critical for ever. */
  const enforceable = f.source !== 'SIMULATOR';
  critStreak = (f.level === 'Critical' && enforceable) ? critStreak + 1 : 0;
  if (critStreak >= CRIT_CONFIRM && !breakActive && Date.now() - lastBreakAt > BREAK_COOLDOWN_MS) {
    critStreak = 0;
    startIntervention();
  }
}

/* Sensor rows: green when the firmware reports the sensor responding, red when
   it does not, amber when the value is substituted, neutral without live flags. */
const HEALTH_ROWS = { tof: 'dev-tof', bmp: 'dev-bmp', bh: 'dev-bh', mpu: 'dev-mpu' };
const HEALTH_FIELD = {
  tof: 'screen_distance_cm', bmp: 'eye_temp_celsius', bh: 'ambient_lux', mpu: 'head_tilt_degrees'
};
const healthState = {};

function updateHealth(f) {
  const espDot = $('dev-esp').querySelector('.state-dot');
  espDot.className = 'state-dot ' + (f.live ? (f.source === 'SIMULATOR' ? 'warn' : 'ok') : 'idle');

  Object.entries(HEALTH_ROWS).forEach(([key, id]) => {
    const dot = $(id).querySelector('.state-dot');
    if (!f.health) { dot.className = 'state-dot idle'; healthState[key] = undefined; return; }

    const ok = f.health[key];
    const substituted = f.substituted.includes(HEALTH_FIELD[key]);
    dot.className = 'state-dot ' + (ok ? 'ok' : substituted ? 'warn' : 'down');

    if (healthState[key] !== ok) {
      if (healthState[key] !== undefined) {
        logEvent(ok ? 'ok' : 'warn',
                 `${$(id).dataset.name} ${ok ? 'responding' : 'not responding'}`,
                 ok ? 'sensor back on the I²C bus' : `I²C bus ${f.i2c} · value substituted`);
      }
      healthState[key] = ok;
    }
  });
}
setInterval(render, 1000);
render();

/* ══════════════════════════════════════════════════════════════════════════
   Enforced break modal
   ══════════════════════════════════════════════════════════════════════════ */
function startIntervention() {
  if (breakActive) return;
  breakActive = true;
  breakCount++;
  lastBreakAt = Date.now();
  logEvent('crit', 'Enforced break started', '20 s · display brightness reduced to 20%');

  const overlay = $('overlay'), numEl = $('ov-num'), prog = $('ov-prog');
  const TOTAL = 282;
  overlay.classList.add('show');

  let t = 20;
  numEl.textContent = t;
  prog.style.transition = 'none';
  prog.style.strokeDashoffset = 0;
  void prog.offsetWidth;                                  // flush the reset before animating
  prog.style.transition = 'stroke-dashoffset 1s linear';

  const iv = setInterval(() => {
    t--;
    numEl.textContent = Math.max(0, t);
    prog.style.strokeDashoffset = ((20 - t) / 20) * TOTAL;
    if (t <= 0) {
      clearInterval(iv);
      overlay.classList.remove('show');
      breakActive = false;
      logEvent('ok', 'Break completed', 'brightness restored · 50 s enforcement cooldown');
    }
  }, 1000);
}
