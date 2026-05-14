const socket = io();

// UI Elements
const connectionPill = document.getElementById('connection-pill');
const connectionText = connectionPill.querySelector('span');

socket.on('connect', () => {
    connectionText.textContent = 'System Online';
    connectionPill.className = 'status-indicator connected';
});

socket.on('disconnect', () => {
    connectionText.textContent = 'System Offline';
    connectionPill.className = 'status-indicator disconnected';
});

// Chart setup with premium styling
const ctx = document.getElementById('strainChart').getContext('2d');
Chart.defaults.color = '#94a3b8';
Chart.defaults.font.family = 'Inter';

// Create gradient for chart fill
const gradient = ctx.createLinearGradient(0, 0, 0, 400);
gradient.addColorStop(0, 'rgba(56, 189, 248, 0.4)');
gradient.addColorStop(1, 'rgba(56, 189, 248, 0.0)');

const chart = new Chart(ctx, {
    type: 'line',
    data: {
        labels: Array(60).fill(''),
        datasets: [{
            label: 'Strain Index',
            data: Array(60).fill(0),
            borderColor: '#38bdf8',
            backgroundColor: gradient,
            borderWidth: 2,
            tension: 0.4,
            fill: true,
            pointRadius: 0
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 0 },
        scales: {
            x: { display: false },
            y: { 
                min: 0, 
                max: 100,
                grid: { color: 'rgba(255, 255, 255, 0.05)' },
                border: { display: false }
            }
        },
        plugins: { legend: { display: false } },
        layout: { padding: { left: -10, bottom: -10 } }
    }
});

let countdownInterval;

// Helper function to handle classes
function updateValueClass(elementId, isCritical, isWarning) {
    const el = document.getElementById(elementId);
    if(isCritical) el.className = 'value critical';
    else if(isWarning) el.className = 'value warning';
    else el.className = 'value safe';
}

function updateEnvClass(elementId, isCritical) {
    const el = document.getElementById(elementId);
    if(isCritical) el.style.color = 'var(--critical)';
    else el.style.color = 'var(--text-primary)';
}

// Listen for updates
socket.on('sensor_update', (data) => {
    // Score & Circle Update
    const scoreEl = document.getElementById('strain-score-val');
    scoreEl.textContent = data.strain_score;
    
    const labelEl = document.getElementById('strain-label');

    const circle = document.getElementById('score-circle');
    const circumference = 2 * Math.PI * 45; 
    const offset = circumference - (data.strain_score / 100) * circumference;
    circle.style.strokeDashoffset = offset;

    if (data.strain_level === 'Safe') {
        scoreEl.className = 'score-val safe';
        labelEl.className = 'badge safe';
        labelEl.textContent = 'Optimal';
        circle.style.stroke = 'var(--safe)';
    } else if (data.strain_level === 'Moderate') {
        scoreEl.className = 'score-val warning';
        labelEl.className = 'badge warning';
        labelEl.textContent = 'Fatigued';
        circle.style.stroke = 'var(--warning)';
    } else {
        scoreEl.className = 'score-val critical';
        labelEl.className = 'badge critical';
        labelEl.textContent = 'Critical';
        circle.style.stroke = 'var(--critical)';
    }

    // Blink Rate
    document.getElementById('blink-rate-val').textContent = data.blink_rate;
    updateValueClass('blink-rate-val', data.blink_rate < 6, data.blink_rate < 10);

    // Distance
    document.getElementById('distance-val').textContent = data.screen_distance_cm;
    updateValueClass('distance-val', data.screen_distance_cm < 20, data.screen_distance_cm < 30);

    // Session time
    document.getElementById('session-time-val').textContent = data.continuous_screen_time_min;

    // Environmental metrics
    document.getElementById('temp-val').textContent = `${data.eye_temp_celsius.toFixed(1)}°C`;
    updateEnvClass('temp-val', data.eye_temp_celsius < 34.0);

    document.getElementById('humidity-val').textContent = `${data.room_humidity_pct}%`;
    document.getElementById('lux-val').textContent = `${data.ambient_lux}`;
    
    document.getElementById('tilt-val').textContent = `${data.head_tilt_degrees}°`;
    updateEnvClass('tilt-val', data.head_tilt_degrees > 35);
    
    document.getElementById('blink-dur-val').textContent = `${data.blink_duration_ms}ms`;

    // Chart update
    chart.data.datasets[0].data.shift();
    chart.data.datasets[0].data.push(data.strain_score);
    chart.update();

    // Alert Modal logic
    const modal = document.getElementById('alert-modal');
    if (data.strain_level === 'Critical' && modal.classList.contains('hidden')) {
        modal.classList.remove('hidden');
        let count = 5;
        document.getElementById('countdown').textContent = count;
        
        const progBar = document.getElementById('alert-progress');
        progBar.style.transition = 'none';
        progBar.style.width = '100%';
        
        // Force reflow
        void progBar.offsetWidth;
        
        progBar.style.transition = 'width 5s linear';
        progBar.style.width = '0%';
        
        clearInterval(countdownInterval);
        countdownInterval = setInterval(() => {
            count--;
            if(count >= 0) {
                document.getElementById('countdown').textContent = count;
            }
        }, 1000);
    } else if (data.strain_level !== 'Critical' && !modal.classList.contains('hidden')) {
        modal.classList.add('hidden');
        clearInterval(countdownInterval);
    }
});

function setScenario(mode) {
    // Update active button state
    document.querySelectorAll('.scenario-btn').forEach(btn => btn.classList.remove('active'));
    
    if(mode === 'Normal') document.getElementById('btn-normal').classList.add('active');
    else if(mode === 'Degrading') document.getElementById('btn-degrading').classList.add('active');
    else if(mode === 'Critical') document.getElementById('btn-critical').classList.add('active');

    fetch('/api/scenario', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode })
    });
}
