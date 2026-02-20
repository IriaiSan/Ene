/* Ene Observatory — Dashboard Frontend
 * Vanilla JS + Chart.js, no framework, no build step.
 * SSE for real-time updates, smooth animations.
 */

const API = '';  // Same origin
const COLORS = {
    accent: '#58a6ff',
    accentDim: '#1f6feb',
    green: '#3fb950',
    yellow: '#d29922',
    red: '#f85149',
    purple: '#bc8cff',
    textSecondary: '#8b949e',
    border: '#30363d',
    surface: '#161b22',
};

const CHART_COLORS = [
    '#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff',
    '#f0883e', '#56d4dd', '#db61a2', '#7ee787', '#79c0ff',
];

// Chart.js global defaults
Chart.defaults.color = COLORS.textSecondary;
Chart.defaults.borderColor = COLORS.border;
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
Chart.defaults.animation.duration = 500;

// ── Chart Instances ──────────────────────────────────

let chartCostDaily = null;
let chartHourly = null;
let chartModel = null;
let chartType = null;

function initCharts() {
    // Cost over time (line chart)
    chartCostDaily = new Chart(document.getElementById('chart-cost-daily'), {
        type: 'bar',
        data: { labels: [], datasets: [{ label: 'Cost ($)', data: [], backgroundColor: COLORS.accent + '60', borderColor: COLORS.accent, borderWidth: 1 }] },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: COLORS.border + '40' }, ticks: { callback: v => '$' + v.toFixed(4) } },
                x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } }
            }
        }
    });

    // Hourly activity (bar chart)
    chartHourly = new Chart(document.getElementById('chart-hourly'), {
        type: 'bar',
        data: { labels: [], datasets: [{ label: 'Calls', data: [], backgroundColor: COLORS.green + '60', borderColor: COLORS.green, borderWidth: 1 }] },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: COLORS.border + '40' } },
                x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } }
            }
        }
    });

    // Cost by model (doughnut)
    chartModel = new Chart(document.getElementById('chart-model'), {
        type: 'doughnut',
        data: { labels: [], datasets: [{ data: [], backgroundColor: CHART_COLORS }] },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'right', labels: { boxWidth: 12, padding: 8 } },
                tooltip: { callbacks: { label: ctx => `${ctx.label}: $${ctx.raw.toFixed(4)}` } }
            }
        }
    });

    // Cost by type (doughnut)
    chartType = new Chart(document.getElementById('chart-type'), {
        type: 'doughnut',
        data: { labels: [], datasets: [{ data: [], backgroundColor: CHART_COLORS.slice(3) }] },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'right', labels: { boxWidth: 12, padding: 8 } },
                tooltip: { callbacks: { label: ctx => `${ctx.label}: $${ctx.raw.toFixed(4)}` } }
            }
        }
    });
}

// ── Data Fetching ────────────────────────────────────

async function fetchJSON(url) {
    try {
        const res = await fetch(API + url);
        if (!res.ok) return null;
        return await res.json();
    } catch (e) {
        console.warn('Fetch failed:', url, e);
        return null;
    }
}

async function updateSummary() {
    const data = await fetchJSON('/api/summary/today');
    if (!data) return;

    document.getElementById('today-cost').textContent = '$' + data.total_cost_usd.toFixed(4);
    document.getElementById('today-calls').textContent = data.total_calls.toLocaleString();
    document.getElementById('today-tokens').textContent = data.total_tokens.toLocaleString();
    document.getElementById('today-latency').textContent = Math.round(data.avg_latency_ms) + 'ms';
    document.getElementById('today-errors').textContent = data.error_count;
    document.getElementById('today-callers').textContent = data.unique_callers;
}

async function updateCostChart() {
    const data = await fetchJSON('/api/cost/daily?days=30');
    if (!data || !chartCostDaily) return;

    chartCostDaily.data.labels = data.map(d => d.date.slice(5));  // MM-DD
    chartCostDaily.data.datasets[0].data = data.map(d => d.cost);
    chartCostDaily.update('none');  // No animation on update (prevent flashing)
}

async function updateHourlyChart() {
    const data = await fetchJSON('/api/activity/hourly?days=1');
    if (!data || !chartHourly) return;

    chartHourly.data.labels = data.map(d => d.hour.slice(11, 16));  // HH:MM
    chartHourly.data.datasets[0].data = data.map(d => d.calls);
    chartHourly.update('none');
}

async function updateModelChart() {
    const data = await fetchJSON('/api/cost/by-model?days=7');
    if (!data || !chartModel) return;

    chartModel.data.labels = data.map(d => d.model.split('/').pop());
    chartModel.data.datasets[0].data = data.map(d => d.cost);
    chartModel.update('none');
}

async function updateTypeChart() {
    const data = await fetchJSON('/api/cost/by-type?days=7');
    if (!data || !chartType) return;

    chartType.data.labels = data.map(d => d.call_type);
    chartType.data.datasets[0].data = data.map(d => d.cost);
    chartType.update('none');
}

async function updateHealth() {
    const data = await fetchJSON('/api/health');
    if (!data) return;

    // Update badge
    const badge = document.getElementById('health-badge');
    badge.textContent = data.status;
    badge.className = 'badge ' + data.status;

    // Update health grid
    const grid = document.getElementById('health-checks');
    if (!data.checks || data.checks.length === 0) {
        grid.innerHTML = '<p class="muted">No health data</p>';
        return;
    }

    grid.innerHTML = data.checks.map(c => `
        <div class="health-check">
            <span class="health-dot ${c.status}"></span>
            <span class="health-name">${formatCheckName(c.name)}</span>
            <span class="health-value">${c.value}</span>
        </div>
    `).join('');
}

async function updateRecentCalls() {
    const data = await fetchJSON('/api/calls/recent?limit=30');
    if (!data) return;

    const tbody = document.getElementById('calls-body');
    if (data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="muted">No calls recorded</td></tr>';
        return;
    }

    tbody.innerHTML = data.map(c => {
        const isError = c.error != null;
        const time = c.timestamp ? c.timestamp.slice(11, 19) : '--';
        const model = c.model ? c.model.split('/').pop() : '--';
        const caller = c.caller_id ? c.caller_id.split(':').pop().slice(0, 10) : '--';

        return `<tr class="${isError ? 'error-row' : ''}">
            <td>${time}</td>
            <td>${c.call_type}</td>
            <td>${model}</td>
            <td>${(c.total_tokens || 0).toLocaleString()}</td>
            <td>$${(c.cost_usd || 0).toFixed(4)}</td>
            <td>${c.latency_ms}ms</td>
            <td>${caller}</td>
            <td>${isError ? '❌' : '✓'}</td>
        </tr>`;
    }).join('');
}

async function updateExperiments() {
    const data = await fetchJSON('/api/experiments');
    if (!data) return;

    const container = document.getElementById('experiments-list');
    if (data.length === 0) {
        container.innerHTML = '<p class="muted">No experiments</p>';
        return;
    }

    container.innerHTML = data.map(exp => {
        const variants = Array.isArray(exp.variants) ? exp.variants : [];
        return `
            <div class="experiment">
                <div class="experiment-header">
                    <span class="experiment-name">${exp.name}</span>
                    <span class="experiment-status ${exp.status}">${exp.status}</span>
                </div>
                ${exp.description ? `<p class="muted">${exp.description}</p>` : ''}
                ${variants.map(v => `
                    <div class="variant-row">
                        <span>${v.name || v.id}</span>
                        <span>weight: ${v.weight || 1.0}</span>
                    </div>
                `).join('')}
            </div>
        `;
    }).join('');
}

// ── Helpers ──────────────────────────────────────────

function formatCheckName(name) {
    return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function updateTimestamp() {
    document.getElementById('last-update').textContent =
        'Updated: ' + new Date().toLocaleTimeString();
}

// ── SSE Real-time ────────────────────────────────────

function connectSSE() {
    const evtSource = new EventSource(API + '/api/events');

    evtSource.addEventListener('summary', (event) => {
        try {
            const data = JSON.parse(event.data);
            document.getElementById('today-cost').textContent = '$' + data.total_cost_usd.toFixed(4);
            document.getElementById('today-calls').textContent = data.total_calls.toLocaleString();
            document.getElementById('today-tokens').textContent = data.total_tokens.toLocaleString();
            document.getElementById('today-latency').textContent = Math.round(data.avg_latency_ms) + 'ms';
            document.getElementById('today-errors').textContent = data.error_count;
            document.getElementById('today-callers').textContent = data.unique_callers;
            updateTimestamp();
        } catch (e) {
            console.warn('SSE parse error:', e);
        }
    });

    evtSource.onerror = () => {
        console.warn('SSE connection lost, reconnecting in 10s...');
        evtSource.close();
        setTimeout(connectSSE, 10000);
    };
}

// ── Init ─────────────────────────────────────────────

async function init() {
    initCharts();

    // Initial data load
    await Promise.all([
        updateSummary(),
        updateCostChart(),
        updateHourlyChart(),
        updateModelChart(),
        updateTypeChart(),
        updateHealth(),
        updateRecentCalls(),
        updateExperiments(),
    ]);
    updateTimestamp();

    // SSE for real-time summary updates
    connectSSE();

    // Periodic full refresh (charts, health, calls) every 30s
    setInterval(async () => {
        await Promise.all([
            updateCostChart(),
            updateHourlyChart(),
            updateModelChart(),
            updateTypeChart(),
            updateHealth(),
            updateRecentCalls(),
            updateExperiments(),
        ]);
        updateTimestamp();
    }, 30000);
}

document.addEventListener('DOMContentLoaded', init);

// ── Brain Indicator ──────────────────────────────────

async function pollBrainStatus() {
    const brainEl = document.getElementById('brain-indicator');
    if (!brainEl) return;
    try {
        const res = await fetch(API + '/api/brain');
        if (res.ok) {
            const data = await res.json();
            if (data.enabled) {
                brainEl.textContent = '🧠 ON';
                brainEl.className = 'badge healthy';
            } else {
                brainEl.textContent = '🧠 OFF';
                brainEl.className = 'badge critical';
            }
        }
    } catch (e) { /* silent */ }
}

pollBrainStatus();
setInterval(pollBrainStatus, 5000);
