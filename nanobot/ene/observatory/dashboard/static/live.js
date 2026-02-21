/* Ene Live Trace — Real-time processing dashboard
 * Vanilla JS, SSE for live events, no build step.
 */

const API = '';  // Same origin
let paused = false;
let eventCount = 0;
let lastEventId = 0;
let evtSource = null;
let lastBatchChannel = null;

// ── Rolling stats (accumulated from SSE events) ──
let statsReceived = 0;
let statsResponded = 0;
let statsLurked = 0;
let latencySum = 0;
let latencyCount = 0;
let lastMsgTime = null;
let lastMsgSender = '';
let lastRespTime = null;
let lastDaemonTime = null;
let lastDaemonLatency = 0;

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
    queue_merge:    'evt-system',
    queue_merge_drop: 'evt-error',
    brain_paused:   'evt-system',
    brain_status_changed: 'evt-system',
    model_switch:   'evt-system',
};

// Module events (mod_*) get the module class
function getEvtClass(type) {
    if (TYPE_CLASS[type]) return TYPE_CLASS[type];
    if (type.startsWith('mod_')) return 'evt-module';
    return 'evt-system';
}

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
    brain_paused:   '🧠 PAUSED',
    brain_status_changed: '🧠 BRAIN',
    model_switch:   '🔄 MODEL',
    queue_merge:    'Q MERGE',
    queue_merge_drop: 'Q DROP',
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
            if (evt.confidence != null) html += ` (${Math.round(evt.confidence * 100)}%)`;
            if (evt.model) html += ` <code>${esc(evt.model)}</code>`;
            if (evt.latency_ms) html += ` ${evt.latency_ms}ms`;
            if (evt.fallback) html += ' <span style="color:var(--yellow)">(fallback)</span>';
            if (evt.reason) html += `<div class="evt-detail">${esc(evt.reason)}</div>`;
            if (evt.topic || evt.tone) {
                let topicTone = '';
                if (evt.topic) topicTone += `topic: ${esc(evt.topic)}`;
                if (evt.topic && evt.tone) topicTone += ' · ';
                if (evt.tone) topicTone += `tone: ${esc(evt.tone)}`;
                html += `<div class="evt-detail">${topicTone}</div>`;
            }
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
            if (evt.content_preview) {
                const len = evt.content_preview.length;
                html += ` · ${len > 999 ? (len/1000).toFixed(1)+'k' : len} chars`;
            }
            if (evt.has_reasoning) html += ` <span style="color:var(--yellow)">💭 reasoning</span>`;
            if (evt.tool_calls && evt.tool_calls.length > 0) {
                html += `<div class="evt-detail">Tools: <code>${esc(evt.tool_calls.join(', '))}</code></div>`;
            }
            if (evt.reasoning_preview) {
                html += `<div class="evt-detail" style="color:var(--yellow);opacity:0.7">💭 "${esc(evt.reasoning_preview)}"</div>`;
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
            if (evt.raw_length > 0 && evt.clean_length < evt.raw_length) {
                const stripped = evt.raw_length - evt.clean_length;
                const pct = Math.round(stripped / evt.raw_length * 100);
                html += ` <span style="color:var(--text-secondary)">(${stripped} stripped · ${pct}%)</span>`;
            }
            if (evt.was_blocked) html += ' <b style="color:var(--red)">BLOCKED</b>';
            else if (evt.was_truncated) html += ' <span style="color:var(--yellow)">(truncated)</span>';
            return html;
        }

        case 'response_sent': {
            let html = `"${esc(evt.content_preview)}"`;
            if (evt.reply_to) html += `<div class="evt-detail">reply_to: ${esc(evt.reply_to)}</div>`;
            return html;
        }

        case 'mute_event':
            return `<b>${esc(evt.sender)}</b> muted ${evt.duration_min}min` +
                (evt.reason ? ` <span style="color:var(--text-secondary)">(${esc(evt.reason)})</span>` : '');

        case 'hard_reset':
            return `<b>Hard reset</b> — queues cleared, session dropped, fresh start`;

        case 'error':
            return `<b>${esc(evt.stage)}</b>: ${esc(evt.error_message)}`;

        case 'brain_paused':
            return `<b>${esc(evt.sender)}</b>: "${esc(evt.content_preview)}" <span style="color:var(--yellow)">(brain OFF — message observed, not processed)</span>`;

        case 'brain_status_changed':
            return evt.status === 'resumed'
                ? '<b style="color:var(--green)">Brain resumed</b> — LLM responses active'
                : '<b style="color:var(--red)">Brain paused</b> — messages observed, no LLM calls';

        case 'model_switch':
            return `<b>${esc(evt.old_model)}</b> → <b style="color:var(--green)">${esc(evt.new_model)}</b>`;

        case 'queue_merge':
            return `Merged <b>${evt.batches_merged}</b> batches → <b>${evt.total_messages}</b> messages`;

        case 'queue_merge_drop':
            return `Queue overflow: dropped <b style="color:var(--red)">${evt.dropped}</b>, kept <b>${evt.kept}</b>`;

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
    // Hard reset from server — wipe timeline and rolling stats
    if (evt.type === 'hard_reset') {
        clearTimeline('⚡ Hard reset — fresh start');
        lastEventId = evt.id;
        eventCount = 0;
        // Reset rolling stats
        statsReceived = 0; statsResponded = 0; statsLurked = 0;
        latencySum = 0; latencyCount = 0;
        lastMsgTime = null; lastMsgSender = '';
        lastRespTime = null; lastDaemonTime = null; lastDaemonLatency = 0;
        document.getElementById('st-received').textContent = '0';
        document.getElementById('st-responded').textContent = '0';
        document.getElementById('st-lurked').textContent = '0';
        document.getElementById('st-avg-latency').textContent = '—';
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
    const cssClass = getEvtClass(evt.type);
    const label = TYPE_LABEL[evt.type] || evt.type.replace(/^mod_/, '').toUpperCase();
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

    // ── Update rolling stats ──
    const now = Date.now();
    switch (evt.type) {
        case 'msg_arrived':
            statsReceived++;
            lastMsgTime = now;
            lastMsgSender = evt.sender || '';
            document.getElementById('st-received').textContent = statsReceived;
            break;
        case 'response_sent':
            statsResponded++;
            lastRespTime = now;
            document.getElementById('st-responded').textContent = statsResponded;
            break;
        case 'should_respond':
            if (!evt.decision) {
                statsLurked++;
                document.getElementById('st-lurked').textContent = statsLurked;
            }
            break;
        case 'llm_response':
            if (evt.latency_ms) {
                latencySum += evt.latency_ms;
                latencyCount++;
                const avg = Math.round(latencySum / latencyCount);
                document.getElementById('st-avg-latency').textContent =
                    avg > 999 ? (avg/1000).toFixed(1)+'s' : avg+'ms';
            }
            break;
        case 'daemon_result':
            lastDaemonTime = now;
            lastDaemonLatency = evt.latency_ms || 0;
            break;
    }

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
    // Pulse animation on processing state-item
    const procItem = procEl.closest('.state-item');
    if (procItem) procItem.classList.toggle('processing-active', !!proc);

    // Muted
    document.getElementById('st-muted').textContent = state.muted_count || 0;

    // Brain indicator (interactive button in header)
    const brainEl = document.getElementById('brain-indicator');
    if (brainEl && state.brain_enabled !== undefined) {
        brainEl._brainState = state.brain_enabled;
        if (state.brain_enabled) {
            brainEl.textContent = '🧠 ON';
            brainEl.className = 'ctrl-btn brain-btn brain-on';
            brainEl.title = 'Brain is active — click to pause';
        } else {
            brainEl.textContent = '🧠 OFF';
            brainEl.className = 'ctrl-btn brain-btn brain-off';
            brainEl.title = 'Brain is paused — click to resume';
        }
    }

    // Model indicator (in state panel + sync dropdown)
    const modelEl = document.getElementById('st-model');
    if (modelEl && state.current_model) {
        // Show short model name (last segment after /)
        const parts = state.current_model.split('/');
        modelEl.textContent = parts[parts.length - 1];
        modelEl.title = state.current_model;
        // Sync dropdown if value changed externally
        const sel = document.getElementById('model-select');
        if (sel && sel.value !== state.current_model) {
            sel.value = state.current_model;
        }
    }

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

// Brain toggle
const brainBtn = document.getElementById('brain-indicator');
brainBtn.addEventListener('click', async () => {
    const isOn = brainBtn._brainState;
    if (isOn) {
        // Pausing requires confirmation
        if (!confirm('Pause brain? Ene will observe messages but won\'t respond until resumed.')) return;
    }
    const action = isOn ? 'pause' : 'resume';
    brainBtn.disabled = true;
    try {
        const res = await fetch(`${API}/api/brain/${action}`, { method: 'POST' });
        if (!res.ok) {
            const data = await res.json();
            alert(`Brain ${action} failed: ` + (data.error || res.statusText));
        }
        // State update comes via SSE state event
    } catch (e) {
        alert(`Brain ${action} failed: ` + e.message);
    } finally {
        brainBtn.disabled = false;
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
        const roleClass = role === 'system' ? 'msg-system'
            : role === 'assistant' ? 'msg-assistant'
            : role === 'tool' ? 'msg-tool'
            : 'msg-user';
        // System prompts: collapsible (default collapsed, click to expand)
        if (role === 'system' && text.length > 200) {
            const charCount = text.length;
            return `<div class="msg-block ${roleClass} msg-collapsible msg-collapsed" onclick="this.classList.toggle('msg-collapsed')">` +
                `<span class="msg-role">SYSTEM (${charCount} chars) ▸</span>` +
                `<pre class="msg-text">${esc(text)}</pre></div>`;
        }
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
        // Rough token estimate from total content length
        let totalChars = 0;
        for (const m of msgs) {
            const c = m.content;
            if (Array.isArray(c)) {
                for (const p of c) totalChars += (p.text || '').length;
            } else if (c) {
                totalChars += c.length;
            }
        }
        const tokenEst = Math.round(totalChars / 4);
        const tokenLabel = tokenEst > 999 ? (tokenEst/1000).toFixed(1)+'k' : tokenEst;
        el.innerHTML = `
            <div class="pe-header">
                <span class="pe-badge pe-badge-ene">ENE PROMPT</span>
                <span class="pe-ts">${esc(entry.ts)}</span>
                <span class="pe-model">${esc(entry.model || '')}</span>
                <span class="pe-meta">${msgs.length} messages · ~${tokenLabel} tokens</span>
            </div>
            <div class="pe-messages">${renderMessages(msgs)}</div>
        `;
    } else if (t === 'prompt_ene_response') {
        el.classList.add('pe-ene-resp');
        // Format tool calls as individual cards instead of raw JSON
        let toolHtml = '';
        if (entry.tool_calls && entry.tool_calls.length) {
            const cards = entry.tool_calls.map(tc => {
                const name = tc.name || tc.function?.name || '?';
                const args = tc.args || tc.function?.arguments || '';
                const argsStr = typeof args === 'object' ? JSON.stringify(args) : String(args);
                const argsShort = argsStr.length > 200 ? argsStr.slice(0, 200) + '…' : argsStr;
                return `<div class="tool-call-card"><span class="tool-call-name">${esc(name)}</span>` +
                    `<span class="tool-call-args">${esc(argsShort)}</span></div>`;
            }).join('');
            toolHtml = `<div class="pe-section"><div class="pe-section-label">TOOL CALLS (${entry.tool_calls.length})</div><div class="pe-tool-cards">${cards}</div></div>`;
        }
        const reasoningHtml = entry.reasoning_content
            ? `<div class="pe-section pe-reasoning"><div class="pe-section-label" style="color:var(--yellow)">💭 REASONING (${entry.reasoning_content.length} chars)</div><pre class="pe-text" style="color:var(--yellow);opacity:0.8">${esc(entry.reasoning_content)}</pre></div>`
            : '';
        el.innerHTML = `
            <div class="pe-header">
                <span class="pe-badge pe-badge-ene-resp">ENE RESPONSE</span>
                <span class="pe-ts">${esc(entry.ts)}</span>
                <span class="pe-meta">iter ${entry.iteration || '?'} — ${entry.latency_ms || '?'}ms</span>
            </div>
            ${reasoningHtml}
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

// ── Module Health Panel ───────────────────────────

async function pollModuleHealth() {
    try {
        const res = await fetch(API + '/api/live/modules?hours=1');
        if (!res.ok) return;
        const data = await res.json();
        renderModuleHealth(data);
    } catch (e) { /* silent */ }
}

function renderModuleHealth(data) {
    const el = document.getElementById('module-health');
    if (!el) return;

    const lines = [];

    // Tracker
    const t = data.tracker || {};
    if (t.threads_created !== undefined) {
        lines.push(`<div class="mh-row"><span class="mh-label">tracker</span> ${t.threads_created} threads | ${t.total_assignments} assigned | avg ${(t.avg_messages_per_thread || 0).toFixed(1)} msg/thread</div>`);
    }

    // Signals
    const s = data.signals || {};
    if (s.total > 0) {
        const d = s.distribution || {};
        const r = d.RESPOND || 0, c = d.CONTEXT || 0, dr = d.DROP || 0;
        const total = s.total;
        lines.push(`<div class="mh-row"><span class="mh-label">signals</span> ${pct(r,total)}% R | ${pct(c,total)}% C | ${pct(dr,total)}% D (${total} total)</div>`);
    }

    // Daemon
    const dm = data.daemon || {};
    if (dm.total_events > 0) {
        const bt = dm.by_type || {};
        const ok = (bt.classified || {}).count || 0;
        const to = (bt.timeout || {}).count || 0;
        const rot = (bt.model_rotation || {}).count || 0;
        lines.push(`<div class="mh-row"><span class="mh-label">daemon</span> ${ok} ok | ${to} timeout | ${rot} rotations</div>`);
    }

    // Cleaning
    const cl = data.cleaning || {};
    if (cl.total_events > 0) {
        const bt = cl.by_type || {};
        const cleaned = (bt.cleaned || {}).count || 0;
        lines.push(`<div class="mh-row"><span class="mh-label">cleaning</span> ${cleaned} cleaned</div>`);
    }

    // Memory
    const m = data.memory || {};
    if (m.total_events > 0) {
        const bt = m.by_type || {};
        const facts = (bt.facts_extracted || {}).count || 0;
        const refl = (bt.reflection_generated || {}).count || 0;
        lines.push(`<div class="mh-row"><span class="mh-label">memory</span> ${facts} extractions | ${refl} reflections</div>`);
    }

    // Prompts
    const p = data.prompts || {};
    if (p.version) {
        lines.push(`<div class="mh-row"><span class="mh-label">prompts</span> v${esc(p.version)}</div>`);
    }

    el.innerHTML = lines.length ? lines.join('') : '<span class="muted">No module data yet</span>';
}

function pct(n, total) {
    return total > 0 ? Math.round(n / total * 100) : 0;
}

// ── Context Inspector ─────────────────────────────

let ctxAutoRefresh = true;

function renderContextMemory(mem) {
    const el = document.getElementById('ctx-mem-sections');
    const badge = document.getElementById('ctx-mem-budget');
    if (!mem) { el.innerHTML = '<div class="ctx-empty">Memory not available</div>'; return; }

    const pctUsed = mem.budget > 0 ? Math.round(mem.total_tokens / mem.budget * 100) : 0;
    const budgetClass = pctUsed > 80 ? 'danger' : pctUsed > 50 ? 'warn' : '';
    badge.textContent = `${mem.total_tokens} / ${mem.budget} tokens (${pctUsed}%)`;

    // Overall budget bar at top
    let html = `<div class="ctx-mem-budget-bar">`;
    html += `<div class="ctx-token-bar" style="height:6px;margin-bottom:8px">`;
    html += `<div class="ctx-token-fill ${budgetClass}" style="width:${Math.min(pctUsed,100)}%"></div>`;
    html += `</div></div>`;

    for (const [name, sec] of Object.entries(mem.sections || {})) {
        const secPct = sec.max_tokens > 0 ? Math.round(sec.used_tokens / sec.max_tokens * 100) : 0;
        const fillClass = secPct > 80 ? 'danger' : secPct > 50 ? 'warn' : '';
        html += `<div class="ctx-mem-section">`;
        html += `<div class="ctx-mem-section-header">`;
        html += `<span>${esc(sec.label || name)}</span>`;
        html += `<span style="font-size:9px;color:var(--muted)">${sec.count} entries · ${sec.used_tokens}t</span>`;
        html += `<div class="ctx-token-bar"><div class="ctx-token-fill ${fillClass}" style="width:${secPct}%"></div></div>`;
        html += `</div>`;
        if (sec.entries && sec.entries.length > 0) {
            html += `<div class="ctx-mem-entries">`;
            for (const e of sec.entries) {
                // Color importance by value: 9-10 red (critical), 7-8 yellow (high), else dim
                const impClass = e.importance >= 9 ? 'importance-critical'
                    : e.importance >= 7 ? 'importance-high' : 'importance';
                html += `<div class="ctx-mem-entry">`;
                html += `<span class="${impClass}">★${e.importance}</span> `;
                html += esc(e.content);
                html += `</div>`;
            }
            html += `</div>`;
        }
        html += `</div>`;
    }
    el.innerHTML = html || '<div class="ctx-empty">No memory entries</div>';
}

function renderContextThreads(threads) {
    const listEl = document.getElementById('ctx-thread-list');
    const countEl = document.getElementById('ctx-thread-count');
    const pendingEl = document.getElementById('ctx-pending-list');

    if (!threads) { listEl.innerHTML = '<div class="ctx-empty">Tracker not available</div>'; return; }

    const active = threads.active || [];
    const pending = threads.pending || [];
    countEl.textContent = `${active.length} active · ${pending.length} pending`;

    let html = '';
    for (const t of active) {
        html += `<div class="ctx-thread-entry">`;
        html += `<div class="ctx-thread-meta">`;
        html += `<span class="ctx-thread-state ${esc(t.state)}">${esc(t.state)}</span>`;
        html += `<span style="font-size:10px;color:var(--muted)">${t.msg_count} msgs · ${(t.participants || []).join(', ')}</span>`;
        if (t.ene_involved) html += `<span class="ctx-ene-badge">Ene ✓</span>`;
        html += `</div>`;
        // Show last_shown_index for debugging re-replay issues
        if (t.last_shown_index != null) {
            html += `<div style="font-size:9px;color:var(--muted);margin:2px 0">shown: ${t.last_shown_index}/${t.msg_count}</div>`;
        }
        // Show time since last activity
        if (t.last_activity) {
            const ago = formatAgo(new Date(t.last_activity).getTime());
            html += `<div style="font-size:9px;color:var(--muted)">active ${ago}</div>`;
        }
        if (t.recent_messages && t.recent_messages.length > 0) {
            html += `<div class="ctx-thread-msgs">`;
            for (const m of t.recent_messages) {
                const isEne = m.author === 'Ene';
                html += `<div class="ctx-thread-msg">`;
                html += `<span class="author ${isEne ? 'ene' : ''}">${esc(m.author)}:</span> `;
                html += esc(m.content);
                html += `</div>`;
            }
            html += `</div>`;
        }
        html += `</div>`;
    }
    listEl.innerHTML = html || '<div class="ctx-empty">No active threads</div>';

    let pendingHtml = '';
    for (const p of pending) {
        pendingHtml += `<div class="ctx-thread-msg"><span class="author">${esc(p.author)}:</span> ${esc(p.content)}</div>`;
    }
    pendingEl.innerHTML = pendingHtml || '<div class="ctx-empty">None</div>';
}

function renderContextSessions(sessions) {
    const el = document.getElementById('ctx-session-messages');
    const info = document.getElementById('ctx-session-info');

    if (!sessions || sessions.length === 0) {
        el.innerHTML = '<div class="ctx-empty">No active sessions</div>';
        return;
    }

    // Show the most active session (usually the main Discord channel)
    const s = sessions[0];
    const tokenEst = s.token_estimate || 0;
    const tokenMax = 60000; // Auto-rotation threshold
    const tokenPct = Math.round(tokenEst / tokenMax * 100);
    const respRate = s.msg_count > 0 ? Math.round((s.responded_count || 0) / s.msg_count * 100) : 0;

    info.textContent = `${s.msg_count || 0} msgs · ${s.responded_count || 0} responses (${respRate}%)`;

    // Token budget bar
    const barClass = tokenPct > 80 ? 'danger' : tokenPct > 50 ? 'warn' : '';
    const tokenLabel = tokenEst > 999 ? (tokenEst/1000).toFixed(1)+'k' : tokenEst;
    let html = `<div style="padding:4px 10px;font-size:10px;color:var(--muted)">`;
    html += `${tokenLabel} / 60k tokens (${tokenPct}%)`;
    html += `<div class="ctx-token-bar" style="height:5px;margin-top:3px">`;
    html += `<div class="ctx-token-fill ${barClass}" style="width:${Math.min(tokenPct,100)}%"></div>`;
    html += `</div></div>`;

    for (const m of (s.recent || [])) {
        const cls = m.role === 'assistant' ? 'assistant' : 'user';
        html += `<div class="ctx-session-msg ${cls}">`;
        html += `<span class="ctx-session-role">${esc(m.role)}</span>`;
        html += `<div class="ctx-session-content">${esc(m.content)}</div>`;
        html += `</div>`;
    }
    el.innerHTML = html || '<div class="ctx-empty">No messages</div>';
}

async function pollContext() {
    if (!ctxAutoRefresh) return;
    const statusEl = document.getElementById('context-status');
    try {
        const res = await fetch('/api/live/context');
        const data = await res.json();
        statusEl.textContent = 'live';
        statusEl.className = 'badge badge-ok';
        renderContextMemory(data.memory);
        renderContextThreads(data.threads);
        renderContextSessions(data.sessions);
    } catch (e) {
        statusEl.textContent = 'error';
        statusEl.className = 'badge badge-error';
    }
}

// Context panel controls
document.getElementById('ctx-auto-refresh').addEventListener('change', (e) => {
    ctxAutoRefresh = e.target.checked;
});
document.getElementById('btn-ctx-refresh').addEventListener('click', () => {
    pollContext();
});

// ── Model Selector ────────────────────────────────

const modelSelect = document.getElementById('model-select');
let modelOptionsLoaded = false;

async function loadModelOptions() {
    if (modelOptionsLoaded) return;
    try {
        const res = await fetch(API + '/api/model/options');
        if (!res.ok) return;
        const data = await res.json();
        const models = data.models || [];
        modelSelect.innerHTML = '';
        for (const m of models) {
            const opt = document.createElement('option');
            opt.value = m;
            // Short display name (last segment)
            const parts = m.split('/');
            opt.textContent = parts[parts.length - 1];
            modelSelect.appendChild(opt);
        }
        modelOptionsLoaded = true;

        // Set current model
        const curRes = await fetch(API + '/api/model');
        if (curRes.ok) {
            const curData = await curRes.json();
            if (curData.model) {
                modelSelect.value = curData.model;
            }
        }
    } catch (e) { /* silent */ }
}

modelSelect.addEventListener('change', async () => {
    const model = modelSelect.value;
    if (!model) return;
    modelSelect.disabled = true;
    try {
        const res = await fetch(API + '/api/model', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({model}),
        });
        const data = await res.json();
        if (!res.ok) {
            alert('Model switch failed: ' + (data.error || res.statusText));
        }
    } catch (e) {
        alert('Model switch failed: ' + e.message);
    } finally {
        modelSelect.disabled = false;
    }
});

// ── Activity timer updates ────────────────────────

function formatAgo(ts) {
    if (!ts) return '—';
    const sec = Math.round((Date.now() - ts) / 1000);
    if (sec < 5) return 'just now';
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    return Math.floor(sec / 3600) + 'h ago';
}

function updateActivityTimers() {
    const msgEl = document.getElementById('st-last-msg');
    const respEl = document.getElementById('st-last-resp');
    const daemonEl = document.getElementById('st-last-daemon');
    if (msgEl) {
        msgEl.textContent = lastMsgTime
            ? `${formatAgo(lastMsgTime)}${lastMsgSender ? ' · ' + lastMsgSender : ''}`
            : '—';
    }
    if (respEl) respEl.textContent = formatAgo(lastRespTime);
    if (daemonEl) {
        daemonEl.textContent = lastDaemonTime
            ? `${formatAgo(lastDaemonTime)}${lastDaemonLatency ? ' (' + lastDaemonLatency + 'ms)' : ''}`
            : '—';
    }
}

// ── Cost by Model polling ────────────────────────

async function pollCostByModel() {
    try {
        const res = await fetch(API + '/api/cost/by-model?days=7');
        if (!res.ok) return;
        const data = await res.json();
        renderCostByModel(data);
    } catch (_) { /* network error, skip */ }
}

function renderCostByModel(data) {
    const wrap = document.getElementById('cost-by-model');
    if (!wrap) return;

    if (!data || !data.length) {
        wrap.innerHTML = '<span class="muted">No cost data yet</span>';
        return;
    }

    // Sort by cost descending
    data.sort((a, b) => (b.cost || 0) - (a.cost || 0));
    const maxCost = data[0]?.cost || 1;
    let totalCost = 0;
    let totalCalls = 0;
    let totalTokens = 0;

    let html = '';
    for (const row of data) {
        const cost = row.cost || 0;
        const calls = row.calls || 0;
        const tokens = row.tokens || 0;
        const avgLatency = row.avg_latency || 0;
        totalCost += cost;
        totalCalls += calls;
        totalTokens += tokens;

        // Split model name: provider/model
        const parts = (row.model || 'unknown').split('/');
        const provider = parts.length > 1 ? parts[0] : '';
        const modelName = parts.length > 1 ? parts.slice(1).join('/') : parts[0];
        const barPct = maxCost > 0 ? Math.round((cost / maxCost) * 100) : 0;
        const avgCost = calls > 0 ? (cost / calls).toFixed(4) : '—';
        const tokPerCall = calls > 0 ? Math.round(tokens / calls).toLocaleString() : '—';

        html += `<div class="cost-row" title="${esc(row.model)}\nCalls: ${calls}\nAvg cost: $${avgCost}/call\nAvg tokens: ${tokPerCall}/call\nAvg latency: ${Math.round(avgLatency)}ms">`;
        html += `<div class="cost-model">${provider ? '<span class="cost-provider">' + esc(provider) + '/</span>' : ''}${esc(modelName)}</div>`;
        html += `<div class="cost-bar-track"><div class="cost-bar-fill" style="width:${barPct}%"></div></div>`;
        html += `<div class="cost-value">$${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(2)}</div>`;
        html += `<div class="cost-calls">${calls}×</div>`;
        html += `</div>`;
    }

    // Total row
    html += `<div class="cost-total-row">`;
    html += `<span class="cost-total-label">Total</span>`;
    html += `<span class="cost-total-value">$${totalCost < 0.01 ? totalCost.toFixed(4) : totalCost.toFixed(2)}</span>`;
    html += `</div>`;

    // Avg per response
    if (totalCalls > 0) {
        const avgPerCall = totalCost / totalCalls;
        const avgTok = Math.round(totalTokens / totalCalls);
        html += `<div class="cost-avg">${totalCalls} calls · avg $${avgPerCall.toFixed(4)}/call · avg ${avgTok.toLocaleString()} tok/call</div>`;
    }

    wrap.innerHTML = html;
}

// ── Init ──────────────────────────────────────────

connectSSE();
connectPromptSSE();
loadModelOptions();
setInterval(pollState, 3000);
pollModuleHealth();
setInterval(pollModuleHealth, 10000);
pollContext();
setInterval(pollContext, 5000);
setInterval(updateActivityTimers, 1000);
pollCostByModel();
setInterval(pollCostByModel, 15000);
