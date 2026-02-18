/* Ene Live Trace — Real-time processing dashboard
 * Vanilla JS, SSE for live events, no build step.
 */

const API = '';  // Same origin
let paused = false;
let eventCount = 0;
let lastEventId = 0;
let evtSource = null;
let lastBatchChannel = null;

// ── DOM refs ──────────────────────────────────────
const timeline = document.getElementById('timeline');
const container = document.getElementById('events-container');
const countEl = document.getElementById('event-count');
const connBadge = document.getElementById('connection-badge');
const btnPause = document.getElementById('btn-pause');
const btnClear = document.getElementById('btn-clear');
const btnHardReset = document.getElementById('btn-hard-reset');

// ── Event type → CSS class mapping ────────────────
const TYPE_CLASS = {
    hard_reset:     'evt-error',
    msg_arrived:    'evt-arrival',
    rate_limited:   'evt-error',
    debounce_add:   'evt-system',
    debounce_flush: 'evt-arrival',
    daemon_result:  'evt-classify',
    classification: 'evt-classify',
    dad_promotion:  'evt-classify',
    merge_complete: 'evt-classify',
    should_respond: 'evt-classify',
    llm_call:       'evt-llm',
    llm_response:   'evt-llm',
    tool_exec:      'evt-tool',
    loop_break:     'evt-system',
    response_clean: 'evt-output',
    response_sent:  'evt-output',
    mute_event:     'evt-error',
    error:          'evt-error',
};

const TYPE_LABEL = {
    hard_reset:     'RESET',
    msg_arrived:    'ARRIVED',
    rate_limited:   'RATE LIM',
    debounce_add:   'BUFFER',
    debounce_flush: 'FLUSH',
    daemon_result:  'DAEMON',
    classification: 'CLASSIFY',
    dad_promotion:  'PROMOTE',
    merge_complete: 'MERGED',
    should_respond: 'RESPOND?',
    llm_call:       'LLM CALL',
    llm_response:   'LLM RESP',
    tool_exec:      'TOOL',
    loop_break:     'LOOP END',
    response_clean: 'CLEAN',
    response_sent:  'SENT',
    mute_event:     'MUTED',
    error:          'ERROR',
};

// ── Render helpers ────────────────────────────────

function esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = String(str);
    return d.innerHTML;
}

function renderBody(evt) {
    const t = evt.type;
    switch (t) {
        case 'msg_arrived':
            return `<b>${esc(evt.sender)}</b>: "${esc(evt.content_preview)}"` +
                (evt.metadata_flags ? `<div class="evt-detail">${esc(evt.metadata_flags)}</div>` : '');

        case 'rate_limited':
            return `<b>${esc(evt.sender)}</b> — ${evt.count} msgs in window`;

        case 'debounce_add':
            return `${esc(evt.sender)} → buffer (${evt.buffer_size} msgs)`;

        case 'debounce_flush':
            return `${evt.batch_size} message${evt.batch_size > 1 ? 's' : ''} flushed (${esc(evt.trigger)})`;

        case 'daemon_result': {
            const cls = evt.classification || '?';
            const clsUpper = cls.toUpperCase();
            let html = `<b>${esc(evt.sender)}</b> → <b>${clsUpper}</b>`;
            if (evt.model) html += ` <code>${esc(evt.model)}</code>`;
            if (evt.latency_ms) html += ` ${evt.latency_ms}ms`;
            if (evt.fallback) html += ' (fallback)';
            if (evt.reason) html += `<div class="evt-detail">${esc(evt.reason)}</div>`;
            if (evt.security_flags) html += `<div class="evt-detail" style="color:var(--red)">⚠ ${esc(evt.security_flags)}</div>`;
            return html;
        }

        case 'classification':
            return `<b>${esc(evt.sender)}</b> → <b>${(evt.result || '?').toUpperCase()}</b> (${esc(evt.source)})` +
                (evt.override ? ` <span style="color:var(--yellow)">⚡ override: ${esc(evt.override)}</span>` : '');

        case 'dad_promotion':
            return `Dad-alone: ${evt.count} msg${evt.count > 1 ? 's' : ''} CONTEXT → RESPOND`;

        case 'merge_complete':
            return `<b>${evt.respond_count}R</b> / ${evt.context_count}C / ${evt.dropped_count} dropped` +
                (evt.thread_count ? ` (${evt.thread_count} threads)` : '');

        case 'should_respond': {
            const icon = evt.decision ? '✅' : '💤';
            return `${icon} ${evt.decision ? 'YES' : 'NO — lurk'}` +
                (evt.reason ? ` — ${esc(evt.reason)}` : '');
        }

        case 'llm_call':
            return `Iteration <b>${evt.iteration}</b> → <code>${esc(evt.model)}</code>` +
                `<div class="evt-detail">${evt.message_count} messages, ${evt.tool_count} tools available</div>`;

        case 'llm_response': {
            let html = `Iteration <b>${evt.iteration}</b>`;
            if (evt.latency_ms) html += ` — ${evt.latency_ms}ms`;
            if (evt.tool_calls && evt.tool_calls.length > 0) {
                html += `<div class="evt-detail">Tools: <code>${esc(evt.tool_calls.join(', '))}</code></div>`;
            }
            if (evt.content_preview) {
                html += `<div class="evt-detail">"${esc(evt.content_preview)}"</div>`;
            }
            return html;
        }

        case 'tool_exec': {
            let html = `<code>${esc(evt.tool_name)}</code>`;
            if (evt.latency_ms) html += ` — ${evt.latency_ms}ms`;
            if (evt.args_preview) html += `<div class="evt-detail">args: ${esc(evt.args_preview)}</div>`;
            if (evt.result_preview) html += `<div class="evt-detail">result: ${esc(evt.result_preview)}</div>`;
            return html;
        }

        case 'loop_break':
            return `<b>${esc(evt.reason)}</b> after ${evt.iterations} iteration${evt.iterations > 1 ? 's' : ''}` +
                (evt.tools_used && evt.tools_used.length ? `<div class="evt-detail">Tools: ${esc(evt.tools_used.join(' → '))}</div>` : '');

        case 'response_clean': {
            let html = `${evt.raw_length} → ${evt.clean_length} chars`;
            if (evt.was_blocked) html += ' <b style="color:var(--red)">BLOCKED</b>';
            else if (evt.was_truncated) html += ' (truncated)';
            return html;
        }

        case 'response_sent': {
            let html = `"${esc(evt.content_preview)}"`;
            if (evt.reply_to) html += `<div class="evt-detail">reply_to: ${esc(evt.reply_to)}</div>`;
            return html;
        }

        case 'mute_event':
            return `<b>${esc(evt.sender)}</b> muted ${evt.duration_min}min (${esc(evt.reason)})`;

        case 'hard_reset':
            return `<b>Hard reset</b> — queues cleared, session dropped, fresh start`;

        case 'error':
            return `<b>${esc(evt.stage)}</b>: ${esc(evt.error_message)}`;

        default:
            return JSON.stringify(evt);
    }
}

function clearTimeline(message) {
    container.innerHTML = `<div class="empty-state"><p>${esc(message || 'Timeline cleared')}</p></div>`;
    eventCount = 0;
    countEl.textContent = '0 events';
    lastBatchChannel = null;
}

function addEvent(evt) {
    // Hard reset from server — wipe timeline before rendering the reset marker
    if (evt.type === 'hard_reset') {
        clearTimeline('⚡ Hard reset — fresh start');
        lastEventId = evt.id;
        eventCount = 0;
        return;
    }

    // Remove empty state if present
    const empty = container.querySelector('.empty-state');
    if (empty) empty.remove();

    // Add separator between different batch channels
    if (evt.type === 'debounce_flush' && lastBatchChannel && lastBatchChannel !== evt.channel_key) {
        const sep = document.createElement('div');
        sep.className = 'evt-separator';
        sep.setAttribute('data-label', 'new batch');
        container.appendChild(sep);
    }
    if (evt.type === 'debounce_flush') {
        lastBatchChannel = evt.channel_key;
    }

    const el = document.createElement('div');
    const cssClass = TYPE_CLASS[evt.type] || 'evt-system';
    const label = TYPE_LABEL[evt.type] || evt.type.toUpperCase();
    el.className = `evt ${cssClass}`;
    el.innerHTML = `
        <span class="evt-ts">${esc(evt.ts)}</span>
        <span class="evt-badge">${label}</span>
        <span class="evt-body">${renderBody(evt)}</span>
    `;
    container.appendChild(el);

    eventCount++;
    countEl.textContent = `${eventCount} events`;
    lastEventId = evt.id;

    // Auto-scroll if not paused
    if (!paused) {
        timeline.scrollTop = timeline.scrollHeight;
    }

    // Cap DOM elements (keep last 300)
    while (container.children.length > 300) {
        container.removeChild(container.firstChild);
    }
}

// ── SSE Connection ────────────────────────────────

function connectSSE() {
    const url = `${API}/api/live?last_id=${lastEventId}`;
    evtSource = new EventSource(url);

    evtSource.addEventListener('event', (e) => {
        try {
            const evt = JSON.parse(e.data);
            addEvent(evt);
        } catch (err) {
            console.warn('SSE parse error:', err);
        }
    });

    evtSource.addEventListener('state', (e) => {
        try {
            const state = JSON.parse(e.data);
            updateStatePanel(state);
        } catch (err) {
            console.warn('State parse error:', err);
        }
    });

    evtSource.onopen = () => {
        connBadge.textContent = 'connected';
        connBadge.className = 'badge connected';
    };

    evtSource.onerror = () => {
        connBadge.textContent = 'disconnected';
        connBadge.className = 'badge disconnected';
        evtSource.close();
        setTimeout(connectSSE, 3000);
    };
}

// ── State Panel ───────────────────────────────────

function updateStatePanel(state) {
    // Buffer count
    const buffers = state.buffers || {};
    const totalBuf = Object.values(buffers).reduce((a, b) => a + b, 0);
    document.getElementById('st-buffers').textContent = totalBuf;

    // Queue depth
    const queues = state.queues || {};
    const totalQueue = Object.values(queues).reduce((a, b) => a + b, 0);
    document.getElementById('st-queues').textContent = totalQueue;

    // Processing
    const proc = state.processing;
    const procEl = document.getElementById('st-processing');
    procEl.textContent = proc ? '✓' : '—';
    procEl.style.color = proc ? 'var(--green)' : '';

    // Muted
    document.getElementById('st-muted').textContent = state.muted_count || 0;

    // Active batch
    const batchEl = document.getElementById('active-batch');
    const batch = state.active_batch;
    if (batch) {
        const ch = batch.channel_key || '?';
        const short = ch.length > 25 ? '...' + ch.slice(-22) : ch;
        batchEl.innerHTML = `
            <div>Channel: <b>${esc(short)}</b></div>
            <div>Messages: ${batch.msg_count || 0}</div>
            <div>Respond: ${batch.respond || 0}</div>
            <div>Context: ${batch.context || 0}</div>
            <div>Dropped: ${batch.dropped || 0}</div>
        `;
    } else {
        batchEl.innerHTML = '<span class="muted">No active batch</span>';
    }
}

// Fallback: poll state if SSE doesn't include it
async function pollState() {
    try {
        const res = await fetch(API + '/api/live/state');
        if (res.ok) {
            const state = await res.json();
            updateStatePanel(state);
        }
    } catch (e) { /* silent */ }
}

// ── Controls ──────────────────────────────────────

btnPause.addEventListener('click', () => {
    paused = !paused;
    btnPause.textContent = paused ? '▶ Resume' : '⏸ Pause';
    btnPause.classList.toggle('active', paused);
    timeline.classList.toggle('paused', paused);
    if (!paused) {
        timeline.scrollTop = timeline.scrollHeight;
    }
});

btnClear.addEventListener('click', () => {
    clearTimeline('Timeline cleared');
});

btnHardReset.addEventListener('click', async () => {
    if (!confirm('Hard reset: drop all queued messages, clear Ene\'s active session, and start fresh.\n\nThis cannot be undone. Continue?')) return;

    btnHardReset.disabled = true;
    btnHardReset.textContent = '⏳ Resetting...';
    try {
        const res = await fetch(API + '/api/live/reset', { method: 'POST' });
        const data = await res.json();
        if (!res.ok) {
            alert('Reset failed: ' + (data.error || res.statusText));
        }
        // Timeline wipe is handled by the hard_reset SSE event from the server
    } catch (e) {
        alert('Reset request failed: ' + e.message);
    } finally {
        btnHardReset.disabled = false;
        btnHardReset.textContent = '⚡ Hard Reset';
    }
});

// Auto-pause on scroll up, auto-resume on scroll to bottom
timeline.addEventListener('scroll', () => {
    const atBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 40;
    if (!atBottom && !paused) {
        paused = true;
        btnPause.textContent = '▶ Resume';
        btnPause.classList.add('active');
        timeline.classList.add('paused');
    } else if (atBottom && paused) {
        paused = false;
        btnPause.textContent = '⏸ Pause';
        btnPause.classList.remove('active');
        timeline.classList.remove('paused');
    }
});

// ── Prompt Log ────────────────────────────────────

const promptLogBody = document.getElementById('prompt-log-body');
const promptEntries = document.getElementById('prompt-entries');
const promptCountEl = document.getElementById('prompt-count');
const promptConnBadge = document.getElementById('prompt-conn-badge');
const btnPromptClear = document.getElementById('btn-prompt-clear');
const btnPromptPause = document.getElementById('btn-prompt-pause');

let promptPaused = false;
let promptCount = 0;
let lastPromptId = 0;
let promptEvtSource = null;

function clearPromptLog(msg) {
    promptEntries.innerHTML = `<div class="empty-state"><p>${esc(msg || 'Prompt log cleared')}</p></div>`;
    promptCount = 0;
    promptCountEl.textContent = '0 entries';
}

function renderMessages(msgs) {
    if (!msgs || !msgs.length) return '<span class="muted">(no messages)</span>';
    return msgs.map(m => {
        const role = m.role || '?';
        const content = m.content || '';
        // Handle array content (multi-part messages with text + images)
        let text = '';
        if (Array.isArray(content)) {
            text = content.map(part => {
                if (part.type === 'text') return part.text || '';
                if (part.type === 'image_url') return '[image]';
                return JSON.stringify(part);
            }).join('\n');
        } else {
            text = String(content);
        }
        const roleClass = role === 'system' ? 'msg-system' : role === 'assistant' ? 'msg-assistant' : 'msg-user';
        return `<div class="msg-block ${roleClass}"><span class="msg-role">${esc(role.toUpperCase())}</span><pre class="msg-text">${esc(text)}</pre></div>`;
    }).join('');
}

function addPromptEntry(entry) {
    const empty = promptEntries.querySelector('.empty-state');
    if (empty) empty.remove();

    const el = document.createElement('div');
    el.className = 'prompt-entry';

    const t = entry.type;

    if (t === 'prompt_daemon') {
        el.classList.add('pe-daemon');
        el.innerHTML = `
            <div class="pe-header">
                <span class="pe-badge pe-badge-daemon">DAEMON PROMPT</span>
                <span class="pe-ts">${esc(entry.ts)}</span>
                <span class="pe-sender">→ ${esc(entry.sender)}</span>
            </div>
            <div class="pe-section">
                <div class="pe-section-label">SYSTEM</div>
                <pre class="pe-text">${esc(entry.system || '')}</pre>
            </div>
            <div class="pe-section">
                <div class="pe-section-label">USER</div>
                <pre class="pe-text">${esc(entry.user || '')}</pre>
            </div>
        `;
    } else if (t === 'prompt_daemon_response') {
        el.classList.add('pe-daemon-resp');
        const cls = (entry.classification || '?').toUpperCase();
        const fallbackNote = entry.fallback ? ' <span class="pe-fallback">(fallback)</span>' : '';
        el.innerHTML = `
            <div class="pe-header">
                <span class="pe-badge pe-badge-daemon-resp">DAEMON RESPONSE</span>
                <span class="pe-ts">${esc(entry.ts)}</span>
                <span class="pe-sender">← ${esc(entry.sender)}</span>
                <span class="pe-model">${esc(entry.model || '')}${fallbackNote}</span>
            </div>
            <div class="pe-kv">
                <span class="pe-kv-row"><b>classification:</b> ${esc(cls)}</span>
                <span class="pe-kv-row"><b>confidence:</b> ${entry.confidence != null ? entry.confidence.toFixed(2) : '?'}</span>
                <span class="pe-kv-row"><b>reason:</b> ${esc(entry.reason || '—')}</span>
                <span class="pe-kv-row"><b>topic:</b> ${esc(entry.topic || '—')}</span>
                <span class="pe-kv-row"><b>tone:</b> ${esc(entry.tone || '—')}</span>
                ${entry.security_flags ? `<span class="pe-kv-row" style="color:var(--red)"><b>security:</b> ${esc(entry.security_flags)}</span>` : ''}
            </div>
        `;
    } else if (t === 'prompt_ene') {
        el.classList.add('pe-ene');
        const msgs = entry.messages || [];
        el.innerHTML = `
            <div class="pe-header">
                <span class="pe-badge pe-badge-ene">ENE PROMPT</span>
                <span class="pe-ts">${esc(entry.ts)}</span>
                <span class="pe-model">${esc(entry.model || '')}</span>
                <span class="pe-meta">${msgs.length} messages</span>
            </div>
            <div class="pe-messages">${renderMessages(msgs)}</div>
        `;
    } else if (t === 'prompt_ene_response') {
        el.classList.add('pe-ene-resp');
        const toolHtml = (entry.tool_calls && entry.tool_calls.length)
            ? `<div class="pe-section"><div class="pe-section-label">TOOL CALLS</div><pre class="pe-text">${esc(JSON.stringify(entry.tool_calls, null, 2))}</pre></div>`
            : '';
        el.innerHTML = `
            <div class="pe-header">
                <span class="pe-badge pe-badge-ene-resp">ENE RESPONSE</span>
                <span class="pe-ts">${esc(entry.ts)}</span>
                <span class="pe-meta">iter ${entry.iteration || '?'} — ${entry.latency_ms || '?'}ms</span>
            </div>
            ${entry.content ? `<div class="pe-section"><div class="pe-section-label">CONTENT</div><pre class="pe-text">${esc(entry.content)}</pre></div>` : ''}
            ${toolHtml}
        `;
    } else {
        el.innerHTML = `<pre class="pe-text">${esc(JSON.stringify(entry, null, 2))}</pre>`;
    }

    promptEntries.appendChild(el);
    promptCount++;
    promptCountEl.textContent = `${promptCount} entries`;
    lastPromptId = entry.id;

    if (!promptPaused) {
        promptLogBody.scrollTop = promptLogBody.scrollHeight;
    }

    // Cap DOM (keep last 100 entries)
    while (promptEntries.children.length > 100) {
        promptEntries.removeChild(promptEntries.firstChild);
    }
}

function connectPromptSSE() {
    const url = `${API}/api/live/prompts?last_id=${lastPromptId}`;
    promptEvtSource = new EventSource(url);

    promptEvtSource.addEventListener('prompt', (e) => {
        try {
            const entry = JSON.parse(e.data);
            addPromptEntry(entry);
        } catch (err) {
            console.warn('Prompt SSE parse error:', err);
        }
    });

    promptEvtSource.onopen = () => {
        promptConnBadge.textContent = 'connected';
        promptConnBadge.className = 'badge connected';
    };

    promptEvtSource.onerror = () => {
        promptConnBadge.textContent = 'disconnected';
        promptConnBadge.className = 'badge disconnected';
        promptEvtSource.close();
        setTimeout(connectPromptSSE, 3000);
    };
}

btnPromptClear.addEventListener('click', () => {
    clearPromptLog('Prompt log cleared');
});

btnPromptPause.addEventListener('click', () => {
    promptPaused = !promptPaused;
    btnPromptPause.textContent = promptPaused ? '▶ Resume' : '⏸ Pause';
    btnPromptPause.classList.toggle('active', promptPaused);
    if (!promptPaused) promptLogBody.scrollTop = promptLogBody.scrollHeight;
});

promptLogBody.addEventListener('scroll', () => {
    const atBottom = promptLogBody.scrollHeight - promptLogBody.scrollTop - promptLogBody.clientHeight < 40;
    if (!atBottom && !promptPaused) {
        promptPaused = true;
        btnPromptPause.textContent = '▶ Resume';
        btnPromptPause.classList.add('active');
    } else if (atBottom && promptPaused) {
        promptPaused = false;
        btnPromptPause.textContent = '⏸ Pause';
        btnPromptPause.classList.remove('active');
    }
});

// ── Init ──────────────────────────────────────────

connectSSE();
connectPromptSSE();
setInterval(pollState, 3000);
