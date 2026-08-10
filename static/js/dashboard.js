// ── Clock ──
let startTime = Date.now(), packetCount = 0;
function tick() {
  const now = new Date();
  document.getElementById('clock').textContent = now.toTimeString().slice(0,8);
  const elapsed = Math.floor((Date.now()-startTime)/1000);
  const m = String(Math.floor(elapsed/60)).padStart(2,'0');
  const s = String(elapsed%60).padStart(2,'0');
  document.getElementById('uptime').textContent = m+':'+s;
}
setInterval(tick, 1000); tick();

// ── Chart ──
const N = 60;
const strainData = new Array(N).fill(0);
const blinkData  = new Array(N).fill(17);
const labels     = Array.from({length:N},(_,i)=>i===0?'60s ago':i===N-1?'now':'');

function makeGrad(ctx, color) {
  const g = ctx.createLinearGradient(0,0,0,180);
  g.addColorStop(0, color.replace(')',',0.25)').replace('rgb','rgba'));
  g.addColorStop(1, color.replace(')',',0)').replace('rgb','rgba'));
  return g;
}

const ctx = document.getElementById('chart').getContext('2d');
const blueRaw  = 'rgb(59,130,246)';
const tealRaw  = 'rgb(20,184,166)';
const blueGrad = makeGrad(ctx, blueRaw);
const tealGrad = makeGrad(ctx, tealRaw);

const chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels,
    datasets: [
      { label:'Strain', data:strainData, borderColor:blueRaw, backgroundColor:blueGrad,
        fill:true, tension:0.4, borderWidth:2, pointRadius:0 },
      { label:'Blink',  data:blinkData,  borderColor:tealRaw, backgroundColor:tealGrad,
        fill:true, tension:0.4, borderWidth:2, pointRadius:0, yAxisID:'y2' }
    ]
  },
  options: {
    responsive:true, maintainAspectRatio:false, animation:{duration:0},
    interaction:{ mode:'index', intersect:false },
    scales:{
      x:{ grid:{color:'rgba(255,255,255,0.04)',drawBorder:false},
          ticks:{color:'rgba(125,133,144,0.6)',font:{family:'DM Mono',size:10},maxTicksLimit:4} },
      y:{ min:0, max:100, position:'left',
          grid:{color:'rgba(255,255,255,0.04)',drawBorder:false},
          ticks:{color:'rgba(125,133,144,0.6)',font:{family:'DM Mono',size:10},maxTicksLimit:5} },
      y2:{ min:0, max:30, position:'right', display:false }
    },
    plugins:{
      legend:{display:false},
      tooltip:{
        backgroundColor:'rgba(22,27,34,0.95)',
        borderColor:'rgba(45,55,72,1)',borderWidth:1,
        titleColor:'#e6edf3', bodyColor:'#7d8590',
        titleFont:{family:'DM Mono',size:11},
        bodyFont:{family:'DM Mono',size:10},
        padding:10
      }
    }
  }
});

// ── Scenario state ──
const SCENARIOS = {
  baseline: {
    strainBase:2,   strainNoise:2,
    blinkBase:17,   blinkNoise:2,
    distBase:45,    distNoise:2,
    focusBase:14,
    eye:'34.8',  hum:'51',  lux:'290', tilt:'5',  bd:'180',
    eyeTr:['↔ stable','green'], humTr:['↔ ok','green'], luxTr:['↔ ideal','green'], tiltTr:['↔ upright','green'], bdTr:['↔ normal','green'],
    alert:{cls:'ok',  ico:'✅', msg:'All biometric markers within healthy range',    sub:'No intervention required · AI confidence 98.2%'},
    strainColor:'var(--green)', badgeCls:'green', badgeText:'OPTIMAL',
    intervention: false,
    backendMode: 'Normal'
  },
  fatigue: {
    strainBase:55,  strainNoise:6,
    blinkBase:9,    blinkNoise:2,
    distBase:27,    distNoise:3,
    focusBase:47,
    eye:'35.9',  hum:'44',  lux:'510', tilt:'18', bd:'240',
    eyeTr:['↑ elevated','amber'], humTr:['↓ low','amber'], luxTr:['↑ bright','amber'], tiltTr:['↗ leaning','amber'], bdTr:['↑ slowing','amber'],
    alert:{cls:'warn',ico:'⚠️', msg:'Eye fatigue detected — blink rate low, posture degrading', sub:'Recommendation: take a short break · AI confidence 91.5%'},
    strainColor:'var(--amber)', badgeCls:'amber', badgeText:'MODERATE',
    intervention: false,
    backendMode: 'Degrading'
  },
  critical: {
    strainBase:92,  strainNoise:4,
    blinkBase:3,    blinkNoise:1,
    distBase:18,    distNoise:2,
    focusBase:82,
    eye:'37.2',  hum:'38',  lux:'820', tilt:'31', bd:'340',
    eyeTr:['↑↑ high','red'], humTr:['↓↓ critical','red'], luxTr:['↑↑ harsh','red'], tiltTr:['↑↑ hunched','red'], bdTr:['↑↑ dry eye','red'],
    alert:{cls:'crit',ico:'🚨', msg:'CRITICAL — mandatory 20-second break enforced now', sub:'Screen dimmed · Physical intervention active · ESP32 triggered'},
    strainColor:'var(--red)', badgeCls:'red', badgeText:'CRITICAL',
    intervention: true,
    backendMode: 'Critical'
  }
};

let current = 'baseline';
let sessionTick = 0;
let interventionRunning = false;

// ── Initialize UI ──
document.addEventListener("DOMContentLoaded", () => {
    // Override the HTML onclicks just to be safe
    document.getElementById('btn-Normal').onclick = function() { activate('baseline', this); };
    document.getElementById('btn-Degrading').onclick = function() { activate('fatigue', this); };
    document.getElementById('btn-Critical').onclick = function() { activate('critical', this); };
    
    // Set initial connection status
    const connBadge = document.getElementById('conn-badge');
    if (connBadge) {
        connBadge.className = 'conn-badge connected';
        document.getElementById('conn-text').textContent = 'ESP32-S3 Connected';
    }
});

function activate(key, el) {
  current = key;
  document.querySelectorAll('.stab').forEach(b => b.classList.remove('active'));
  el.classList.add('active');

  const s = SCENARIOS[key];

  // Send API call to backend to trigger physical screen dimming if it's running
  fetch('/api/scenario', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: s.backendMode })
  }).catch(e => console.log("Backend offline, continuing in standalone mode."));

  // Alert
  const al = document.getElementById('alert');
  al.className = 'alert-strip ' + s.alert.cls;
  al.querySelector('.alert-icon').textContent = s.alert.ico;
  document.getElementById('alert-msg').textContent = s.alert.msg;
  document.getElementById('alert-sub').textContent = s.alert.sub;

  // Strain
  document.getElementById('gauge-arc').style.stroke = s.strainColor;
  document.getElementById('strain-num').style.color  = s.strainColor;
  const badge = document.getElementById('strain-badge');
  badge.className = 'badge ' + s.badgeCls;
  badge.textContent = s.badgeText;

  // Telemetry trends
  const tItems = [{id:'eye',tr:'eyeTr'},{id:'hum',tr:'humTr'},{id:'lux',tr:'luxTr'},{id:'tilt',tr:'tiltTr'},{id:'bd',tr:'bdTr'}];
  tItems.forEach(t => {
    const trEl = document.getElementById('tr-'+t.id);
    const [txt, col] = s[t.tr];
    trEl.textContent = txt;
    trEl.style.color = col === 'green' ? 'var(--green)' : col === 'amber' ? 'var(--amber)' : 'var(--red)';
  });

  // Intervention
  if (s.intervention && !interventionRunning) {
    setTimeout(startIntervention, 800);
  }
}

// --- REAL WEBSOCKET DATA INTEGRATION ---
const socket = io();

socket.on('sensor_update', function(data) {
  packetCount++;
  document.getElementById('packets').textContent = packetCount;

  // Strain gauge
  const strain = data.strain_score;
  document.getElementById('strain-num').textContent = strain;
  const arc = document.getElementById('gauge-arc');
  arc.style.strokeDashoffset = 163.4 - (strain/100)*163.4;

  // Blink
  const blink = data.blink_rate;
  document.getElementById('blink-val').textContent = blink;
  document.getElementById('blink-val').style.color = blink < 8 ? 'var(--red)' : blink < 13 ? 'var(--amber)' : 'var(--blue)';
  document.getElementById('blink-bar').style.width = Math.min(100,(blink/20)*100)+'%';
  document.getElementById('blink-bar').style.background = blink < 8 ? 'var(--red)' : blink < 13 ? 'var(--amber)' : 'var(--blue)';

  // Distance
  const dist = data.screen_distance_cm;
  document.getElementById('dist-val').textContent = dist;
  document.getElementById('dist-val').style.color = dist < 25 ? 'var(--red)' : dist < 33 ? 'var(--amber)' : 'var(--green)';
  document.getElementById('dist-bar').style.width = Math.min(100,(dist/70)*100)+'%';
  document.getElementById('dist-bar').style.background = dist < 25 ? 'var(--red)' : dist < 33 ? 'var(--amber)' : 'var(--green)';

  // Focus/session
  const focusMin = data.continuous_screen_time_min || 0;
  document.getElementById('focus-val').textContent = focusMin;
  const breakLeft = Math.max(0, 20 - (focusMin % 20));
  document.getElementById('focus-sub').textContent = breakLeft > 0
    ? `20-20-20 break due in ${breakLeft} min` : 'Break overdue - rest now!';
  document.getElementById('focus-bar').style.width = Math.min(100,(focusMin/120)*100)+'%';

  // Telemetry (No fake jitter, just real numbers)
  document.getElementById('t-eye').textContent  = parseFloat(data.eye_temp_celsius).toFixed(1) + '°C';
  document.getElementById('t-hum').textContent  = Math.round(data.room_humidity_pct) + '%';
  document.getElementById('t-lux').textContent  = Math.round(data.ambient_lux) + ' lx';
  document.getElementById('t-tilt').textContent = parseFloat(data.head_tilt_degrees).toFixed(1) + '°';
  document.getElementById('t-bd').textContent   = Math.round(data.blink_duration_ms) + ' ms';

  // Chart
  strainData.shift(); strainData.push(strain);
  blinkData.shift();  blinkData.push(blink);
  chart.update('none');
  
  // Dynamic Badge Color Update based on AI Score
  const sCls = strain >= 60 ? 'red' : strain >= 30 ? 'amber' : 'green';
  const sTxt = strain >= 60 ? 'CRITICAL' : strain >= 30 ? 'MODERATE' : 'OPTIMAL';
  const sCol = strain >= 60 ? 'var(--red)' : strain >= 30 ? 'var(--amber)' : 'var(--green)';
  
  document.getElementById('gauge-arc').style.stroke = sCol;
  document.getElementById('strain-num').style.color  = sCol;
  const badge = document.getElementById('strain-badge');
  badge.className = 'badge ' + sCls;
  badge.textContent = sTxt;
});

// ── Intervention ──
function startIntervention() {
  if (interventionRunning) return;
  interventionRunning = true;
  const overlay = document.getElementById('overlay');
  const numEl   = document.getElementById('ov-num');
  const progEl  = document.getElementById('ov-prog');
  overlay.classList.add('show');

  let t = 20;
  const total = 390; // stroke-dasharray
  progEl.style.transition = 'none';
  progEl.style.strokeDashoffset = 0;
  
  // Force reflow
  void progEl.offsetWidth;
  
  progEl.style.transition = 'stroke-dashoffset 1s linear';

  const iv = setInterval(() => {
    t--;
    numEl.textContent = t;
    progEl.style.strokeDashoffset = ((20-t)/20) * total;
    if (t <= 0) {
      clearInterval(iv);
      overlay.classList.remove('show');
      interventionRunning = false;
      // Auto-reset to baseline
      activate('baseline', document.getElementById('btn-Normal'));
    }
  }, 1000);
}
