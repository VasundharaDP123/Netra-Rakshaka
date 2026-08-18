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

/* ══════════════════════════════════════════════════════════════════════════
   Feature state

   Everything the render loop or a click handler can reach is declared here,
   ahead of the loop itself. `let`/`const` are hoisted but not initialised, so a
   binding declared further down the file throws "cannot access before
   initialisation" the moment the loop touches it — and because the loop is
   started at the top level, that error aborts the rest of the script, leaving
   every listener below it unregistered.
   ══════════════════════════════════════════════════════════════════════════ */

let lastFrame = null;             // most recent telemetry frame, shared by all engines

/* — 20-20-20 baseline timer — */
let t20Interval = 20 * 60;        // seconds per cycle (Configuration can change this)
let t20Left = t20Interval;
let t20Paused = false;            // true while no active screen use is detected
let t20ModalOpen = false;
let t20ModalIv = null;
let t20Interacted = false;        // did the user act on the prompt, or ignore it?
const t20Outcomes = { complied: 0, skipped: 0, ignored: 0 };

/* — Deep Work — */
let deepWorkActive = false;
let deepWorkLeft = 0;
let deepWorkTotal = 0;            // for the progress ring
let deepWorkTimerIv = null;
let deepWorkEpisodes = 0;         // Critical episodes during the session (not seconds)
let inCriticalEpisode = false;
let criticalSecInDeepWork = 0;
let deepWorkSessions = 0;
let chainMinutes = 0;             // focus minutes since the last complied rest
let lastDeepWorkEndAt = 0;
let suppressedBreaks = 0;

/* — Deep Work auto-suggestion — */
let steadyFocusSamples = 0;
let deepWorkAutoSuggested = false;

/* — Health analytics — */
let currentAnalyticsWindow = 'daily';
const liveSessionBuffer = [];
const MAX_LIVE_BUFFER = 1500;     // ~5 min of 5 Hz telemetry

/* — Configuration (persisted locally; the backend routes are optional) — */
const SETTINGS_KEY = 'nr.settings.v1';
const DEFAULT_SETTINGS = {
  sensitivity_mode: 'Normal',
  cooldown_sec: 50,
  min_distance_threshold: 20,
  min_bpm_threshold: 8,
  sound_alerts: 1
};
let settings = { ...DEFAULT_SETTINGS };

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
    recordLiveTelemetry(d);
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
  lastFrame = f;

  const st = STATUS[f.level] || STATUS.Safe;
  document.body.dataset.state = st.key;

  /* Ambient Corner Strain Ring/Dot Indicator update */
  const cornerDot = $('corner-strain-dot');
  if (cornerDot) {
    const dotColor = f.level === 'Critical' ? '#ef5d6f' : (f.level === 'Moderate' ? '#e8a33d' : '#35c98a');
    const dotGlow = f.level === 'Critical' ? '0 0 16px rgba(239,93,111,0.9)' : (f.level === 'Moderate' ? '0 0 12px rgba(232,163,61,0.8)' : '0 0 12px rgba(53,201,138,0.8)');
    cornerDot.style.background = dotColor;
    cornerDot.style.boxShadow = dotGlow;
    cornerDot.title = `Strain State: ${f.level} (${f.score}/100)`;
  }

  /* Update scenario buttons to reflect active telemetry strain state */
  const activeBtnId = f.level === 'Critical' ? 'btn-Critical' : (f.level === 'Moderate' ? 'btn-Degrading' : 'btn-Normal');
  document.querySelectorAll('.seg').forEach(b => b.setAttribute('aria-selected', b.id === activeBtnId ? 'true' : 'false'));

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
      if (f.level === 'Critical') {
        showDashboardNotification("🚨 CRITICAL EYE STRAIN DETECTED", `Ocular strain score ${f.score}/100! Blink rate or viewing distance critically degraded. Recovery break required.`, "crit");
      } else if (f.level === 'Moderate') {
        showDashboardNotification("⚠️ Moderate Eye Strain Warning", `Ocular strain score ${f.score}/100. Early fatigue detected — rest recommended.`, "warn");
      }
    } else {
      logEvent('info', 'Monitoring started', 'classifier online · 5 Hz sampling');
    }
    lastLevel = f.level;
  }
  evaluateBreaches(f);
  checkDeepWorkAutoSuggest(f);
  tick202020(f);
  tickDeepWork(f);

  /* Sustained Critical enforces a break, the same rule screen_control.py uses.
     Packets from the backend simulator are excluded: with no spectacles attached
     it reports blink_rate 0, which the classifier pins at Critical for ever. */
  const enforceable = f.source !== 'SIMULATOR';
  critStreak = (f.level === 'Critical' && enforceable) ? critStreak + 1 : 0;
  if (critStreak >= CRIT_CONFIRM && !breakActive && Date.now() - lastBreakAt > settings.cooldown_sec * 1000) {
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
/* ══════════════════════════════════════════════════════════════════════════
   Enforced break modal — the "intrusive" intervention Deep Work silences
   ══════════════════════════════════════════════════════════════════════════ */
function startIntervention() {
  if (breakActive) return;

  /* Deep Work's promise: intrusive alerts stay quiet. The full-screen takeover is
     suppressed and replaced by the breakthrough banner, which is visual only.
     (Physical screen dimming is screen_control.py's job and is untouched.) */
  if (deepWorkActive) {
    suppressedBreaks++;
    lastBreakAt = Date.now();
    showBreakthrough(true);
    logEvent('warn', 'Enforced break suppressed', 'Deep Work active · shown as a visual breakthrough instead');
    return;
  }

  breakActive = true;
  breakCount++;
  lastBreakAt = Date.now();
  reset202020Timer('enforced break');    // a stronger alert already rested the eyes
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

/* ══════════════════════════════════════════════════════════════════════════
   Live telemetry buffer — feeds Health Analytics
   ══════════════════════════════════════════════════════════════════════════ */
function recordLiveTelemetry(d) {
  if (!d) return;
  liveSessionBuffer.push({
    t: Date.now(),
    bpm: typeof d.blink_rate === 'number' ? d.blink_rate : 0,
    dist: typeof d.screen_distance_cm === 'number' ? d.screen_distance_cm : 0,
    tilt: typeof d.head_tilt_degrees === 'number' ? d.head_tilt_degrees : 0,
    lux: typeof d.ambient_lux === 'number' ? d.ambient_lux : 0,
    level: d.strain_level || 'Safe',
    score: typeof d.strain_score === 'number' ? d.strain_score : 0
  });
  if (liveSessionBuffer.length > MAX_LIVE_BUFFER) liveSessionBuffer.shift();

  // keep an open analytics page live, but repaint at most once a second
  if (analyticsOpen && Date.now() - lastAnalyticsPaint > 1000) renderAnalytics(currentAnalyticsWindow);
}

/* ══════════════════════════════════════════════════════════════════════════
   Configuration — persisted locally, applied to the running console
   ══════════════════════════════════════════════════════════════════════════ */
const SENSITIVITY_INTERVAL_MIN = { Normal: 20, Sensitive: 15, Strict: 12 };

function applySettings(s, announce = false) {
  settings = { ...DEFAULT_SETTINGS, ...s };

  // sensitivity decides how often the baseline rest cycle comes round
  const mins = SENSITIVITY_INTERVAL_MIN[settings.sensitivity_mode] || 20;
  const changed = t20Interval !== mins * 60;
  t20Interval = mins * 60;
  if (changed) { t20Left = Math.min(t20Left, t20Interval); update202020UI(); }

  // thresholds feed the alert rules and the KPI chips
  breaches.blink.trip = (f) => f.blink < settings.min_bpm_threshold;
  breaches.blink.clear = (f) => f.blink >= settings.min_bpm_threshold + 2;
  breaches.blink.enter = `Blink rate below ${settings.min_bpm_threshold} bpm`;
  breaches.dist.trip = (f) => f.dist < settings.min_distance_threshold;
  breaches.dist.clear = (f) => f.dist >= settings.min_distance_threshold + 3;
  breaches.dist.enter = `Viewing distance below ${settings.min_distance_threshold} cm`;

  if (announce) {
    logEvent('ok', 'Configuration applied',
      `${settings.sensitivity_mode} · rest every ${mins} min · dist ${settings.min_distance_threshold} cm · ` +
      `blink ${settings.min_bpm_threshold} bpm · cooldown ${settings.cooldown_sec}s · ` +
      `sound ${settings.sound_alerts ? 'on' : 'off'}`);
  }
}

function fillSettingsForm() {
  if ($('cfg-sensitivity')) $('cfg-sensitivity').value = settings.sensitivity_mode;
  if ($('cfg-cooldown')) $('cfg-cooldown').value = settings.cooldown_sec;
  if ($('cfg-min-dist')) $('cfg-min-dist').value = settings.min_distance_threshold;
  if ($('cfg-min-bpm')) $('cfg-min-bpm').value = settings.min_bpm_threshold;
  if ($('cfg-sound')) $('cfg-sound').value = String(settings.sound_alerts);
}

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) applySettings(JSON.parse(raw));
  } catch (e) { /* corrupt or unavailable storage: defaults stand */ }
  fillSettingsForm();

  // If the backend ever exposes /api/settings it wins; until then this 404s quietly.
  fetch('/api/settings')
    .then(r => r.ok ? r.json() : null)
    .then(d => { if (d && typeof d === 'object') { applySettings(d); fillSettingsForm(); } })
    .catch(() => {});
}

function saveSettings(e) {
  if (e) e.preventDefault();
  const payload = {
    sensitivity_mode: $('cfg-sensitivity').value,
    cooldown_sec: clamp(parseInt($('cfg-cooldown').value, 10) || 50, 20, 180),
    min_distance_threshold: clamp(parseInt($('cfg-min-dist').value, 10) || 20, 10, 40),
    min_bpm_threshold: clamp(parseInt($('cfg-min-bpm').value, 10) || 8, 4, 15),
    sound_alerts: parseInt($('cfg-sound').value, 10) ? 1 : 0
  };
  applySettings(payload, true);
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(payload)); } catch (e2) {}

  fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
  }).catch(() => {});

  closeSettingsModal();                       // closes whether or not a backend answered
  showDashboardNotification('Configuration saved', 'Thresholds applied to the running session.', 'ok');
}

function openSettingsModal() {
  const m = $('settings-modal');
  if (!m) return;
  fillSettingsForm();
  m.style.display = 'flex';
}
function closeSettingsModal() {
  const m = $('settings-modal');
  if (m) m.style.display = 'none';
}

/* ══════════════════════════════════════════════════════════════════════════
   Health Analytics — live session buffer widened by the CSV session log
   ══════════════════════════════════════════════════════════════════════════ */
let analyticsOpen = false;
let lastAnalyticsPaint = 0;

const setText = (id, v) => { if ($(id)) $(id).textContent = v; };

function paintDistribution(safePct, modPct, critPct) {
  if ($('bar-safe')) $('bar-safe').style.width = safePct + '%';
  if ($('bar-mod')) $('bar-mod').style.width = modPct + '%';
  if ($('bar-crit')) $('bar-crit').style.width = critPct + '%';
  setText('pct-safe', safePct.toFixed(1));
  setText('pct-mod', modPct.toFixed(1));
  setText('pct-crit', critPct.toFixed(1));
}

function summarise(rows, get) {
  const n = rows.length;
  if (!n) return null;
  const bpm = rows.reduce((a, r) => a + get.bpm(r), 0) / n;
  const compliant = rows.filter(r => get.dist(r) >= settings.min_distance_threshold).length;
  const active = rows.filter(r => { const d = get.dist(r); return d > 0 && d < ACTIVE_SCREEN_CM; }).length;
  const share = (name) => rows.filter(r => get.level(r) === name).length / n * 100;
  return {
    samples: n,
    avgBpm: bpm,
    distCompliance: compliant / n * 100,
    activeMin: active * 0.2 / 60,             // telemetry streams at 5 Hz → 0.2 s per row
    safe: share('Safe'), mod: share('Moderate'), crit: share('Critical')
  };
}

function paintBehaviour() {
  const total = t20Outcomes.complied + t20Outcomes.skipped + t20Outcomes.ignored;
  const rate = total ? (t20Outcomes.complied / total * 100) : 0;
  setText('an-compliance-rate', total ? rate.toFixed(0) + '%' : '—');
  setText('an-complied', t20Outcomes.complied);
  setText('an-skipped', t20Outcomes.skipped);
  setText('an-ignored', t20Outcomes.ignored);
  setText('an-dw-sessions', deepWorkSessions);
  setText('an-dw-chain', chainMinutes + ' min');
  setText('an-dw-suppressed', suppressedBreaks);
}

function renderAnalytics(windowType = 'daily') {
  currentAnalyticsWindow = windowType;
  lastAnalyticsPaint = Date.now();

  ['daily', 'weekly'].forEach(w => {
    const tab = $('an-tab-' + w);
    if (!tab) return;
    const on_ = w === windowType;
    tab.style.background = on_ ? 'var(--accent)' : 'transparent';
    tab.style.color = on_ ? '#fff' : '#97a1b2';
  });

  paintBehaviour();

  /* 1. Paint the live session immediately so the page is never blank. */
  const live = summarise(liveSessionBuffer, { bpm: r => r.bpm, dist: r => r.dist, level: r => r.level });
  if (live) {
    setText('an-screen-time', live.activeMin.toFixed(1) + ' min');
    setText('an-avg-bpm', live.avgBpm.toFixed(1) + ' bpm');
    setText('an-dist-comp', live.distCompliance.toFixed(1) + '%');
    setText('an-breaks', breakCount);
    paintDistribution(live.safe, live.mod, live.crit);
    setText('an-window-note', `live session · ${live.samples.toLocaleString()} telemetry samples`);
  } else {
    setText('an-window-note', 'no live telemetry yet · reading the session log');
  }

  /* 2. Then widen to the persisted session log for the requested window. */
  const limit = windowType === 'weekly' ? 20000 : 8000;
  const cutoff = Date.now() - (windowType === 'weekly' ? 7 : 1) * 24 * 3600 * 1000;

  fetch(`/api/history?limit=${limit}`)
    .then(r => r.ok ? r.json() : [])
    .then(rows => {
      if (!Array.isArray(rows) || !rows.length) return;
      const windowRows = rows.filter(r => {
        const t = Date.parse(String(r.timestamp).replace(' ', 'T'));
        return isFinite(t) ? t >= cutoff : true;
      });
      const hist = summarise(windowRows, {
        bpm: r => Number(r.blink_rate) || 0,
        dist: r => Number(r.screen_distance_cm) || 0,
        level: r => r.strain_level
      });
      if (!hist) return;
      setText('an-screen-time', hist.activeMin.toFixed(1) + ' min');
      setText('an-avg-bpm', hist.avgBpm.toFixed(1) + ' bpm');
      setText('an-dist-comp', hist.distCompliance.toFixed(1) + '%');
      setText('an-breaks', breakCount);
      paintDistribution(hist.safe, hist.mod, hist.crit);
      setText('an-window-note',
        `${windowType === 'weekly' ? 'last 7 days' : 'last 24 hours'} · ` +
        `${hist.samples.toLocaleString()} logged samples · breaks counted for this session`);
    })
    .catch(() => {});

  /* 3. A richer backend endpoint is used only if it exists. */
  fetch(`/api/analytics?window=${windowType}`)
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;
      if (d.total_screen_time_min != null) setText('an-screen-time', Number(d.total_screen_time_min).toFixed(1) + ' min');
      if (d.average_bpm != null) setText('an-avg-bpm', Number(d.average_bpm).toFixed(1) + ' bpm');
      if (d.distance_compliance_pct != null) setText('an-dist-comp', Number(d.distance_compliance_pct).toFixed(1) + '%');
      if (d.total_breaks_taken != null) setText('an-breaks', d.total_breaks_taken);
      if (d.strain_breakdown) {
        paintDistribution(Number(d.strain_breakdown.safe_pct) || 0,
                          Number(d.strain_breakdown.moderate_pct) || 0,
                          Number(d.strain_breakdown.critical_pct) || 0);
      }
    })
    .catch(() => {});
}

function openAnalyticsModal() {
  const m = $('analytics-modal');
  if (!m) return;
  analyticsOpen = true;
  m.style.display = 'block';
  renderAnalytics(currentAnalyticsWindow);
}
function closeAnalyticsModal() {
  const m = $('analytics-modal');
  if (m) m.style.display = 'none';
  analyticsOpen = false;
}

/* ══════════════════════════════════════════════════════════════════════════
   20-20-20 baseline timer

   Counts down only while the wearer is actually at a screen, is reset by any
   stronger intervention, and is deliberately exempt from Deep Work suppression:
   it is a visual reminder, not an interruption.
   ══════════════════════════════════════════════════════════════════════════ */
const T20_RING = 132;                     // circumference of the r=21 progress ring
const ACTIVE_SCREEN_CM = 60;              // closer than this counts as active screen use
let t20SeenLevel = null;

const mmss = (sec) => {
  const s = Math.max(0, Math.round(sec));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};

function update202020UI() {
  setText('t20-timer', mmss(t20Left));
  const ring = $('t20-ring');
  if (ring) ring.style.strokeDashoffset = T20_RING * (1 - (t20Interval - t20Left) / t20Interval);

  const chip = $('t20-status');
  if (chip) {
    const [label, cls] = t20ModalOpen ? ['Resting', 'ok']
      : t20Paused ? ['Paused', 'warn']
      : t20Left <= 60 ? ['Due soon', 'warn']
      : ['Active', 'ok'];
    chip.className = 'status-chip ' + cls;
    chip.textContent = label;
  }
  setText('t20-sub', t20ModalOpen ? 'Visual rest in progress'
    : t20Paused ? 'Paused · no active screen use detected'
    : `Next rest in ${mmss(t20Left)} · gentle visual reminder`);
}

function reset202020Timer(reason) {
  t20Left = t20Interval;
  update202020UI();
  if (reason) logEvent('info', '20-20-20 cycle reset', `${reason} · next rest in ${mmss(t20Left)}`);
}

function tick202020(f) {
  // A stronger alert has already rested the eyes: restart the clock rather than
  // stacking a second prompt on top of it.
  if (f.level === 'Critical' && t20SeenLevel !== 'Critical') reset202020Timer('critical strain alert');
  t20SeenLevel = f.level;

  if (t20ModalOpen) { update202020UI(); return; }

  const activeScreenUse = f.dist > 0 && f.dist < ACTIVE_SCREEN_CM;
  t20Paused = !activeScreenUse;
  if (activeScreenUse) {
    t20Left--;
    if (t20Left <= 0) { trigger202020(); return; }
  }
  update202020UI();
}

function trigger202020() {
  const modal = $('modal-202020');
  if (!modal || t20ModalOpen) return;

  t20ModalOpen = true;
  t20Interacted = false;
  t20Left = t20Interval;

  // Deep Work keeps its promise: the reminder appears, silently, and says so.
  const note = $('m20-dw-note');
  if (note) note.style.display = deepWorkActive ? 'block' : 'none';
  if (!deepWorkActive) playAudioBeep();

  modal.style.display = 'flex';
  update202020UI();
  logEvent('info', '20-20-20 rest prompted',
    deepWorkActive ? 'visual-only reminder · Deep Work alerts stay silenced'
                   : 'look 20 feet away for 20 seconds');

  let sec = 20;
  setText('m20-timer', sec);
  if (t20ModalIv) clearInterval(t20ModalIv);
  t20ModalIv = setInterval(() => {
    sec--;
    setText('m20-timer', Math.max(0, sec));
    if (sec <= 0) close202020(t20Interacted ? 'complied' : 'ignored');
  }, 1000);
}

function close202020(outcome) {
  if (t20ModalIv) { clearInterval(t20ModalIv); t20ModalIv = null; }
  const modal = $('modal-202020');
  if (modal) modal.style.display = 'none';
  t20ModalOpen = false;
  recordCompliance(outcome);
  reset202020Timer();
}

/* Compliance is the behavioural signal the forecasting work wants: complied =
   the user confirmed the rest, skipped = dismissed on purpose, ignored = the
   prompt ran its full 20 s untouched. */
function recordCompliance(outcome) {
  if (!(outcome in t20Outcomes)) return;
  t20Outcomes[outcome]++;
  if (outcome === 'complied') chainMinutes = 0;      // a real rest breaks the focus chain

  try { localStorage.setItem('nr.compliance.v1', JSON.stringify(t20Outcomes)); } catch (e) {}

  logEvent(outcome === 'complied' ? 'ok' : 'warn',
    `20-20-20 ${outcome}`,
    outcome === 'complied' ? 'user confirmed a 20 s distance rest'
      : outcome === 'skipped' ? 'prompt dismissed without resting'
      : 'prompt ran its full 20 s with no interaction');

  fetch('/api/compliance', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: outcome, event_type: '20-20-20', ts: Date.now() })
  }).catch(() => {});

  if (analyticsOpen) renderAnalytics(currentAnalyticsWindow);
}

/* ══════════════════════════════════════════════════════════════════════════
   Deep Work Mode
   ══════════════════════════════════════════════════════════════════════════ */
const DW_RING = 132;
const DW_CRITICAL_AUTOEND_SEC = 120;   // sustained Critical that outlasts the override
const DW_CHAIN_WARN_MIN = 50;          // focus minutes without a rest before we flag it

function updateDeepWorkUI() {
  const ring = $('dw-ring');
  if (deepWorkActive) {
    setText('dw-timer-display', mmss(deepWorkLeft));
    if ($('dw-timer-display')) $('dw-timer-display').style.display = 'inline-block';
    if ($('dw-ring-wrap')) $('dw-ring-wrap').style.display = 'block';
    if (ring) ring.style.strokeDashoffset = DW_RING * (1 - deepWorkLeft / Math.max(1, deepWorkTotal));
    if ($('btn-dw-start')) $('btn-dw-start').style.display = 'none';
    if ($('btn-dw-extend')) $('btn-dw-extend').style.display = 'inline-block';
    if ($('btn-dw-stop')) $('btn-dw-stop').style.display = 'inline-block';
    setText('dw-title', 'Deep Work Mode: Active');
    if ($('dw-title')) $('dw-title').style.color = '#35c98a';
    setText('dw-desc', `${mmss(deepWorkLeft)} remaining · intrusive alerts silenced`);
  } else {
    if ($('dw-timer-display')) $('dw-timer-display').style.display = 'none';
    if ($('dw-ring-wrap')) $('dw-ring-wrap').style.display = 'none';
    if ($('btn-dw-start')) $('btn-dw-start').style.display = 'inline-block';
    if ($('btn-dw-extend')) $('btn-dw-extend').style.display = 'none';
    if ($('btn-dw-stop')) $('btn-dw-stop').style.display = 'none';
    setText('dw-title', 'Deep Work Mode');
    if ($('dw-title')) $('dw-title').style.color = '#e5e9f0';
    setText('dw-desc', '25-min focus session · silences intrusive alerts');
    showBreakthrough(false);
  }
}

function showBreakthrough(on_) {
  const banner = $('dw-breakthrough-banner');
  if (banner) banner.style.display = on_ ? 'flex' : 'none';
  if ($('dw-card')) $('dw-card').style.borderColor = on_ ? '#ef5d6f' : 'rgba(255,255,255,0.08)';
}

function startDeepWork(durationMin = 25, force = false) {
  if (deepWorkActive) { extendDeepWork(durationMin); return; }

  /* Starting a focus session while the eyes are already in Critical strain would
     silence the very alerts that state needs, so it takes an explicit override. */
  const level = lastFrame ? lastFrame.level : 'Safe';
  if (level === 'Critical' && !force) {
    logEvent('warn', 'Deep Work blocked', 'strain is Critical · rest first, or start with the override');
    showDashboardNotification(
      'Deep Work blocked — strain is Critical',
      'Focus mode would silence the alerts you currently need. Rest your eyes first, or override deliberately.',
      'crit',
      [{ label: 'Start anyway', action: () => startDeepWork(durationMin, true) },
       { label: 'Rest now', action: () => trigger202020() }]);
    return;
  }

  // Chained sessions with no rest in between are themselves a strain risk.
  const gapMin = lastDeepWorkEndAt ? (Date.now() - lastDeepWorkEndAt) / 60000 : Infinity;
  if (gapMin > 10) chainMinutes = 0;

  deepWorkActive = true;
  deepWorkTotal = durationMin * 60;
  deepWorkLeft = deepWorkTotal;
  deepWorkEpisodes = 0;
  inCriticalEpisode = false;
  criticalSecInDeepWork = 0;
  deepWorkSessions++;
  chainMinutes += durationMin;

  updateDeepWorkUI();
  logEvent('info', 'Deep Work started',
    `${durationMin} min · enforced-break takeover silenced · chain ${chainMinutes} min`);
  showDashboardNotification('Deep Work started',
    `${durationMin} minutes of focus. Intrusive alerts are silenced; 20-20-20 reminders still appear.`, 'ok');

  fetch('/api/deep_work_start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ duration_min: durationMin })
  }).catch(() => {});

  checkFocusChain();
}

function extendDeepWork(extraMin = 25) {
  if (!deepWorkActive) return;
  deepWorkLeft += extraMin * 60;
  deepWorkTotal += extraMin * 60;
  chainMinutes += extraMin;
  updateDeepWorkUI();
  logEvent('ok', 'Deep Work extended',
    `+${extraMin} min · ${mmss(deepWorkLeft)} remaining · chain ${chainMinutes} min`);
  checkFocusChain();
}

/* Back-to-back focus with no rest in between defeats the point of the mode. */
function checkFocusChain() {
  if (chainMinutes < DW_CHAIN_WARN_MIN) return;
  logEvent('warn', 'Extended focus without rest',
    `${chainMinutes} min of chained Deep Work · a 20-20-20 rest is overdue`);
  showDashboardNotification('Extended focus without a rest',
    `${chainMinutes} minutes of chained focus. A 20-second distance rest resets the chain.`,
    'warn', [{ label: 'Rest now', action: () => trigger202020() }]);
}

function stopDeepWork(userCancelled = true) {
  if (!deepWorkActive) return;
  deepWorkActive = false;
  lastDeepWorkEndAt = Date.now();
  criticalSecInDeepWork = 0;
  inCriticalEpisode = false;
  updateDeepWorkUI();
  if (userCancelled) logEvent('warn', 'Deep Work ended early', `${mmss(deepWorkLeft)} left on the clock`);
}

function completeDeepWork() {
  const minutes = Math.round(deepWorkTotal / 60);
  const episodes = deepWorkEpisodes;
  const held = suppressedBreaks;
  stopDeepWork(false);

  logEvent('ok', 'Deep Work complete',
    `${minutes} min · ${episodes} critical episode${episodes === 1 ? '' : 's'} · ${held} break${held === 1 ? '' : 's'} held back`);
  showDashboardNotification('Deep Work session complete',
    episodes === 0
      ? `${minutes} minutes with zero critical alerts. Great focus.`
      : `${minutes} minutes finished with ${episodes} critical episode${episodes === 1 ? '' : 's'}. Consider a rest before the next session.`,
    episodes === 0 ? 'ok' : 'warn',
    [{ label: 'Rest now', action: () => trigger202020() }]);

  fetch('/api/deep_work_complete', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ duration_min: minutes, critical_episodes: episodes, status: 'completed' })
  }).catch(() => {});
}

/* Driven by the 1 Hz render loop, so the session clock and the telemetry it
   reacts to always come from the same tick. */
function tickDeepWork(f) {
  if (!deepWorkActive) return;

  deepWorkLeft--;

  if (f.level === 'Critical') {
    criticalSecInDeepWork++;
    if (!inCriticalEpisode) {
      inCriticalEpisode = true;
      deepWorkEpisodes++;
      showBreakthrough(true);
      logEvent('crit', 'Critical strain during Deep Work', `breaking through the silence · episode ${deepWorkEpisodes}`);
      showDashboardNotification('Critical strain — breaking through Deep Work',
        'Not a routine alert: strain is Critical while focus mode is silencing the rest.', 'crit');
    }
    if (criticalSecInDeepWork >= DW_CRITICAL_AUTOEND_SEC) {
      stopDeepWork(false);
      logEvent('crit', 'Deep Work auto-ended',
        `strain stayed Critical for ${DW_CRITICAL_AUTOEND_SEC} s despite the override`);
      showDashboardNotification('Deep Work auto-ended',
        'Strain stayed Critical for two minutes. Focus mode released and a rest is starting.', 'crit');
      trigger202020();
      return;
    }
  } else {
    criticalSecInDeepWork = 0;
    if (inCriticalEpisode) { inCriticalEpisode = false; showBreakthrough(false); }
  }

  if (deepWorkLeft <= 0) { completeDeepWork(); return; }
  updateDeepWorkUI();
}

/* ══════════════════════════════════════════════════════════════════════════
   On-dashboard notifications
   ══════════════════════════════════════════════════════════════════════════ */
function playAudioBeep() {
  if (!settings.sound_alerts) return;
  if (deepWorkActive) return;                     // silence is the point of the mode
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    gain.gain.setValueAtTime(0.12, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
    osc.connect(gain); gain.connect(ctx.destination);
    osc.start(); osc.stop(ctx.currentTime + 0.3);
  } catch (e) { /* autoplay policy or no audio device */ }
}

const TOAST_TONE = {
  crit: ['#1c1216', '#ef5d6f', '#i-alert'],
  warn: ['#1d1a14', '#e8a33d', '#i-warn-circle'],
  ok:   ['#101c17', '#35c98a', '#i-check'],
  info: ['#101a2c', '#4d8dff', '#i-target']
};

function showDashboardNotification(title, message, type = 'crit', actions = []) {
  const container = $('notification-container');
  if (!container) return;
  if (type === 'crit' || type === 'warn') playAudioBeep();

  const [bg, accent, icon] = TOAST_TONE[type] || TOAST_TONE.crit;
  const toast = document.createElement('div');
  toast.style.cssText = `pointer-events:auto; background:${bg}; border:1px solid ${accent};
    border-radius:10px; padding:14px 16px; color:#e5e9f0; box-shadow:0 10px 30px rgba(0,0,0,0.6);
    display:flex; flex-direction:column; gap:10px; transition:opacity .3s, transform .3s;`;

  const head = document.createElement('div');
  head.style.cssText = 'display:flex; align-items:flex-start; gap:12px;';
  head.innerHTML = `
    <div style="width:28px; height:28px; border-radius:50%; background:${accent}22; display:flex;
                align-items:center; justify-content:center; color:${accent}; flex-shrink:0;">
      <svg class="ic ic-sm"><use href="${icon}"/></svg>
    </div>
    <div style="flex:1; min-width:0;">
      <div style="font-size:13px; font-weight:700; color:${accent};">${title}</div>
      <div style="font-size:12px; color:#c5cbd6; line-height:1.45; margin-top:2px;">${message}</div>
    </div>`;
  const close = document.createElement('button');
  close.textContent = '×';
  close.style.cssText = 'background:transparent; border:none; color:#97a1b2; font-size:18px; cursor:pointer; line-height:1;';
  close.addEventListener('click', () => toast.remove());
  head.appendChild(close);
  toast.appendChild(head);

  if (actions.length) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex; gap:8px; justify-content:flex-end;';
    actions.forEach((a, i) => {
      const b = document.createElement('button');
      b.textContent = a.label;
      b.style.cssText = i === 0
        ? `background:${accent}; color:#0b0e13; border:none; border-radius:6px; padding:7px 14px; font-size:12px; font-weight:600; cursor:pointer;`
        : 'background:#1c2536; color:#97a1b2; border:1px solid rgba(255,255,255,0.1); border-radius:6px; padding:7px 12px; font-size:12px; cursor:pointer;';
      b.addEventListener('click', () => { toast.remove(); a.action(); });
      row.appendChild(b);
    });
    toast.appendChild(row);
  }

  container.appendChild(toast);
  setTimeout(() => {
    if (!toast.parentElement) return;
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(40px)';
    setTimeout(() => toast.remove(), 300);
  }, actions.length ? 14000 : 6000);
}

/* ══════════════════════════════════════════════════════════════════════════
   Deep Work auto-suggestion — steady posture and distance imply focused work
   ══════════════════════════════════════════════════════════════════════════ */
function checkDeepWorkAutoSuggest(f) {
  if (deepWorkActive || deepWorkAutoSuggested || f.level === 'Critical') return;

  const steady = f.dist >= 30 && f.dist <= 65 && f.tilt <= 20;
  steadyFocusSamples = steady ? steadyFocusSamples + 1 : Math.max(0, steadyFocusSamples - 1);

  if (steadyFocusSamples >= 50) {
    deepWorkAutoSuggested = true;
    logEvent('info', 'Steady focus detected', 'posture and viewing distance held for ~50 s');
    showDashboardNotification('Steady focus detected',
      'Posture and viewing distance have been stable. Start a 25-minute Deep Work session to silence routine alerts?',
      'info',
      [{ label: 'Start Deep Work', action: () => startDeepWork(25) },
       { label: 'Not now', action: () => {} }]);
  }
}

/* ══════════════════════════════════════════════════════════════════════════
   Wiring
   ══════════════════════════════════════════════════════════════════════════ */
const bind = (id, ev, fn) => { const el = $(id); if (el) el.addEventListener(ev, fn); };

bind('btn-dw-start', 'click', () => startDeepWork(25));
bind('btn-dw-extend', 'click', () => extendDeepWork(25));
bind('btn-dw-stop', 'click', () => stopDeepWork(true));
bind('nav-item-deepwork', 'click', () => {
  if (!deepWorkActive) startDeepWork(25);
  const card = $('dw-card');
  if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
});

bind('nav-item-analytics', 'click', openAnalyticsModal);
bind('an-close', 'click', closeAnalyticsModal);
bind('an-tab-daily', 'click', () => renderAnalytics('daily'));
bind('an-tab-weekly', 'click', () => renderAnalytics('weekly'));

bind('nav-item-settings', 'click', openSettingsModal);
bind('set-close', 'click', closeSettingsModal);
bind('cfg-form', 'submit', saveSettings);

bind('btn-202020-comply', 'click', () => { t20Interacted = true; close202020('complied'); });
bind('btn-202020-skip', 'click', () => { t20Interacted = true; close202020('skipped'); });
bind('btn-202020-dw', 'click', () => { t20Interacted = true; close202020('skipped'); startDeepWork(25, true); });

bind('corner-strain-dot', 'click', () => {
  const bar = $('statusbar');
  if (bar) bar.scrollIntoView({ behavior: 'smooth', block: 'center' });
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if ($('analytics-modal') && $('analytics-modal').style.display !== 'none') closeAnalyticsModal();
  if ($('settings-modal') && $('settings-modal').style.display !== 'none') closeSettingsModal();
});

/* The physical Deep Work button on the spectacles: when app.py broadcasts a
   `deep_work_start` event over Socket.IO, the console picks it up here. */
if (typeof io !== 'undefined') {
  try {
    const evtSocket = io({ transports: ['websocket', 'polling'] });
    evtSocket.on('deep_work_start', (d) => {
      const mins = (d && d.duration_min) || 25;
      logEvent('info', 'Deep Work requested by device', `${mins} min · deep_work_start over Wi-Fi`);
      startDeepWork(mins, true);
    });
  } catch (e) { /* standalone */ }
}

/* Inline onclick attributes in the markup call these by name. */
Object.assign(window, {
  startDeepWork, extendDeepWork, stopDeepWork,
  openAnalyticsModal, closeAnalyticsModal,
  openSettingsModal, closeSettingsModal,
  trigger202020, close202020, renderAnalytics
});

/* ══════════════════════════════════════════════════════════════════════════
   Boot — every binding above is initialised before the loop starts
   ══════════════════════════════════════════════════════════════════════════ */
try {
  const saved = JSON.parse(localStorage.getItem('nr.compliance.v1') || 'null');
  if (saved) Object.assign(t20Outcomes, saved);
} catch (e) {}

loadSettings();
update202020UI();
updateDeepWorkUI();

setInterval(render, 1000);
render();
