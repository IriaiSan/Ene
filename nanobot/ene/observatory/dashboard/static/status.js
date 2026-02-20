/* ── Ene Status Page — Bento Box Pipeline View ──────────────── */
/* Static snapshot dashboard. Each section fully replaces on update. */

'use strict';

const API = '';
const THREAD_POLL_MS = 3000;
const BRAIN_POLL_MS = 5000;
const TIMER_TICK_MS = 1000;

// ── Utility ────────────────────────────────────────────────────

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function relTime(ts) {
    if (!ts) return '--';
    const sec = Math.floor((Date.now() - ts) / 1000);
    if (sec < 5) return 'just now';
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    return Math.floor(sec / 3600) + 'h ago';
}

function relTimeFromUnix(unix) {
    if (!unix) return '--';
    const sec = Math.floor(Date.now() / 1000 - unix);
    if (sec < 5) return 'just now';
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    return Math.floor(sec / 3600) + 'h ago';
}

function timerClass(ts) {
    if (!ts) return 'stale';
    const delta = Date.now() - ts;
    if (delta < 10000) return 'fresh';
    if (delta < 60000) return 'warm';
    return 'stale';
}

async function fetchJSON(url) {
    try {
        const res = await fetch(API + url);
        if (!res.ok) return null;
        return await res.json();
    } catch { return null; }
}

// ── State ──────────────────────────────────────────────────────

const timers = {
    threads: null, intake: null, daemon: null, classify: null,
    person: null, memory: null, context: null, llm: null, response: null,
};

let lastEventId = 0;
let lastPromptId = 0;
let evtSource = null;
let promptSource = null;
let pipelineDimmed = false;

// Accumulated within a processing cycle (reset on debounce_flush)
let classRows = [];
let toolCards = [];
let llmIterations = 0;
let llmModel = '';
let llmTotalLatency = 0;
let mergeData = null;
let respondDecision = null;

// Person tracking
let currentSenderId = null;
let peopleIndex = null; // name → person, built on first use

// ── Timer Ticking ──────────────────────────────────────────────

function tickTimers() {
    for (const [section, ts] of Object.entries(timers)) {
        const el = document.getElementById('timer-' + section);
        if (!el) continue;
        el.textContent = relTime(ts);
        el.className = 'timer ' + timerClass(ts);
    }
}

function markUpdated(section) {
    timers[section] = Date.now();
}

// ── Brain Indicator ────────────────────────────────────────────

async function pollBrain() {
    const data = await fetchJSON('/api/brain');
    const el = document.getElementById('brain-indicator');
    if (!el) return;
    if (!data || data.error) {
        el.textContent = '🧠 --';
        el.className = 'badge';
        return;
    }
    el.textContent = data.enabled ? '🧠 ON' : '🧠 OFF';
    el.className = 'badge ' + (data.enabled ? 'connected' : 'disconnected');
}

// ── Zone 1: Thread Kanban ──────────────────────────────────────

async function pollThreads() {
    const [threads, pending] = await Promise.all([
        fetchJSON('/api/threads?full=1'),
        fetchJSON('/api/threads/pending'),
    ]);
    markUpdated('threads');

    const container = document.getElementById('thread-cards');
    if (!threads || threads.length === 0) {
        container.innerHTML = '<div class="empty-state">No active threads</div>';
    } else {
        container.innerHTML = threads.map(renderThreadColumn).join('');
    }

    const pendSec = document.getElementById('pending-section');
    const pendCards = document.getElementById('pending-cards');
    if (pending && pending.length > 0) {
        pendSec.classList.remove('hidden');
        pendCards.innerHTML = pending.map(renderPendingCard).join('');
    } else {
        pendSec.classList.add('hidden');
    }
}

function renderThreadColumn(t) {
    const state = t.state || 'active';
    const age = relTimeFromUnix(t.created_at);
    const lastAct = relTimeFromUnix(t.last_activity);
    const participants = (t.participants || []).join(', ') || 'unknown';
    const eneBadge = t.ene_involved ? ' 🤖' : '';

    // Render all messages in the thread
    const messages = t.messages || [];
    let msgsHtml = '';
    if (messages.length === 0) {
        msgsHtml = '<div class="kanban-empty">No messages</div>';
    } else {
        msgsHtml = messages.map(m => {
            const isEne = m.is_ene;
            const cls = m.classification || '';
            return `<div class="kanban-msg ${isEne ? 'kanban-msg-ene' : ''} ${cls === 'respond' ? 'kanban-msg-respond' : ''}">
                <div class="kanban-msg-head">
                    <span class="kanban-msg-author${isEne ? ' ene-author' : ''}">${esc(m.author || '?')}</span>
                    <span class="kanban-msg-time">${relTimeFromUnix(m.timestamp)}</span>
                </div>
                <div class="kanban-msg-text">${esc(m.content || '')}</div>
            </div>`;
        }).join('');
    }

    return `<div class="kanban-column state-${esc(state)}">
        <div class="kanban-header">
            <div class="kanban-header-top">
                <span class="thread-state">
                    <span class="dot ${esc(state)}"></span>
                    ${esc(state)}${eneBadge}
                </span>
                <span class="kanban-msg-count">${t.msg_count || 0} msgs</span>
            </div>
            <div class="kanban-participants">${esc(participants)}</div>
            <div class="kanban-times">
                <span>started ${age}</span>
                <span>active ${lastAct}</span>
            </div>
        </div>
        <div class="kanban-messages">${msgsHtml}</div>
    </div>`;
}

function renderPendingCard(p) {
    // API sends flat fields: { author, content, channel, timestamp }
    const sender = p.author || (p.message && p.message.author_name) || 'unknown';
    const content = p.content || (p.message && p.message.content) || '';
    return `<div class="pending-card">
        <span class="sender">${esc(sender)}</span>: ${esc(content.substring(0, 80))}
    </div>`;
}

// ── Zone 2: Pipeline Sections ──────────────────────────────────

// 2a: Message Intake
function updateIntake(evt) {
    const body = document.getElementById('body-intake');
    const sender = evt.sender || '?';
    const content = evt.content_preview || '';
    const flags = evt.metadata_flags || [];
    const flagBadges = (Array.isArray(flags) ? flags : String(flags).split(',')).filter(Boolean)
        .map(f => `<span class="intake-badge">${esc(f.trim())}</span>`).join(' ');

    body.innerHTML = `
        <div class="intake-msg">
            <span class="intake-sender">${esc(sender)}</span>
            ${flagBadges}
            <div class="intake-content">${esc(content)}</div>
        </div>
        <div class="intake-buffer" id="intake-buffer-info">Buffering...</div>
    `;
    markUpdated('intake');

    // Track sender for person card
    if (evt.sender && evt.sender !== currentSenderId) {
        currentSenderId = evt.sender;
        fetchPersonCard(evt.sender);
    }
}

function updateIntakeBuffer(evt) {
    const el = document.getElementById('intake-buffer-info');
    if (!el) return;
    if (evt.type === 'debounce_add') {
        el.textContent = `Buffer: ${evt.buffer_size || '?'} msgs`;
    } else if (evt.type === 'debounce_flush') {
        el.textContent = `Batch flushed: ${evt.batch_size || '?'} msgs (${evt.trigger || 'timer'})`;
    }
    markUpdated('intake');
}

function showIntakeNotice(evt) {
    const body = document.getElementById('body-intake');
    if (!body) return;
    if (evt.type === 'rate_limited') {
        body.insertAdjacentHTML('beforeend',
            `<div class="intake-notice rate-limited">⚠ Rate limited: ${esc(evt.sender || '?')} (${evt.count || '?'} msgs)</div>`
        );
    } else if (evt.type === 'mute_event') {
        body.insertAdjacentHTML('beforeend',
            `<div class="intake-notice muted">🔇 Muted: ${esc(evt.sender || '?')} for ${evt.duration_min || '?'}m — ${esc(evt.reason || '')}</div>`
        );
    }
}

// 2b: Daemon Analysis
function updateDaemon(data) {
    const body = document.getElementById('body-daemon');
    const cls = (data.classification || '').toLowerCase();
    const conf = Math.round((data.confidence || 0) * 100);
    const confClass = conf >= 70 ? 'high' : conf >= 40 ? 'mid' : 'low';

    const secFlags = data.security_flags;
    const hasFlags = secFlags && ((Array.isArray(secFlags) && secFlags.length > 0) ||
        (typeof secFlags === 'string' && secFlags.trim()));
    const flagsHtml = hasFlags
        ? `<div class="daemon-security">⚠ Security: ${esc(Array.isArray(secFlags) ? secFlags.map(f => typeof f === 'object' ? f.type + ':' + f.severity : f).join(', ') : String(secFlags))}</div>`
        : '';

    const fallbackHtml = data.fallback
        ? '<div class="daemon-fallback">⚠ Math classifier fallback (daemon timed out)</div>'
        : '';

    body.innerHTML = `
        <div class="daemon-classification">
            <span class="cls-badge cls-${esc(cls)}">${esc(cls || '?')}</span>
            <div class="confidence-bar">
                <div class="confidence-fill ${confClass}" style="width:${conf}%"></div>
            </div>
            <span class="confidence-pct">${conf}%</span>
        </div>
        <div class="daemon-details">
            <span class="daemon-label">Model</span>
            <span class="daemon-value">${esc(data.model || data.model_used || '?')}</span>
            <span class="daemon-label">Latency</span>
            <span class="daemon-value">${data.latency_ms ? data.latency_ms + 'ms' : '?'}</span>
            <span class="daemon-label">Reason</span>
            <span class="daemon-value">${esc(data.reason || data.classification_reason || '--')}</span>
            <span class="daemon-label">Topic</span>
            <span class="daemon-value">${esc(data.topic || data.topic_summary || '--')}</span>
            <span class="daemon-label">Tone</span>
            <span class="daemon-value">${esc(data.tone || data.emotional_tone || '--')}</span>
        </div>
        ${flagsHtml}
        ${fallbackHtml}
    `;
    markUpdated('daemon');
}

// 2c: Classification & Merge
function addClassification(evt) {
    classRows.push({
        sender: evt.sender || '?',
        result: (evt.result || '?').toLowerCase(),
        source: evt.source || '--',
        override: evt.override || '',
    });
    renderClassify();
}

function updateMerge(evt) {
    mergeData = {
        respond: evt.respond_count || 0,
        context: evt.context_count || 0,
        dropped: evt.dropped_count || 0,
    };
    renderClassify();
}

function updateShouldRespond(evt) {
    respondDecision = {
        decision: evt.decision,
        reason: evt.reason || '',
    };
    renderClassify();
}

function renderClassify() {
    const body = document.getElementById('body-classify');
    let html = '';

    // Classification table
    if (classRows.length > 0) {
        html += '<table class="classify-table"><thead><tr><th>Sender</th><th>Result</th><th>Source</th><th>Override</th></tr></thead><tbody>';
        for (const r of classRows) {
            html += `<tr>
                <td>${esc(r.sender)}</td>
                <td><span class="cls-mini cls-${esc(r.result)}">${esc(r.result)}</span></td>
                <td>${esc(r.source)}</td>
                <td>${esc(r.override)}</td>
            </tr>`;
        }
        html += '</tbody></table>';
    }

    // Merge summary
    if (mergeData) {
        html += `<div class="classify-merge">
            Merge: <strong>${mergeData.respond}</strong> respond,
            <strong>${mergeData.context}</strong> context,
            <strong>${mergeData.dropped}</strong> dropped
        </div>`;
    }

    // Should-respond decision
    if (respondDecision !== null) {
        const yes = respondDecision.decision;
        html += `<div class="classify-decision ${yes ? 'yes' : 'no'}">
            ${yes ? '✅ Should respond: YES' : '❌ Should respond: NO'}
            ${respondDecision.reason ? ' — ' + esc(respondDecision.reason) : ''}
        </div>`;
    }

    if (!html) html = '<div class="empty-state">Waiting for classification...</div>';
    body.innerHTML = html;
    markUpdated('classify');
}

// 2d: Person & Scene
async function fetchPersonCard(senderName) {
    // Build people index if needed
    if (!peopleIndex) {
        const people = await fetchJSON('/api/people');
        if (people && Array.isArray(people)) {
            peopleIndex = {};
            for (const p of people) {
                if (p.name) peopleIndex[p.name.toLowerCase()] = p;
                if (p.display_name) peopleIndex[p.display_name.toLowerCase()] = p;
            }
        }
    }

    if (!peopleIndex) return;
    const match = peopleIndex[senderName.toLowerCase()];
    if (!match || !match.id) return;

    const person = await fetchJSON('/api/people/' + encodeURIComponent(match.id));
    if (!person || person.error) return;

    renderPersonCard(person);
}

function renderPersonCard(p) {
    const body = document.getElementById('body-person');
    const trust = p.trust || {};
    const score = trust.score || 0;
    const tier = (trust.tier || 'stranger').toLowerCase();
    const signals = trust.signals || {};

    const notesHtml = (p.notes || []).slice(-3).map(n =>
        `<div class="person-note">${esc(typeof n === 'string' ? n : n.content || '')}</div>`
    ).join('');

    body.innerHTML = `
        <div class="person-header">
            <span class="person-name">${esc(p.name || p.display_name || '?')}</span>
            <span class="tier-badge tier-${esc(tier)}">${esc(tier.replace('_', ' '))}</span>
        </div>
        <div class="trust-bar-container">
            <div class="trust-bar"><div class="trust-fill" style="width:${score}%"></div></div>
            <div class="trust-label"><span>Trust</span><span>${(score / 100).toFixed(2)}</span></div>
        </div>
        <div class="person-stats">
            <span class="stat-label">Messages</span>
            <span class="stat-value">${signals.message_count || 0}</span>
            <span class="stat-label">Sessions</span>
            <span class="stat-value">${signals.session_count || 0}</span>
            <span class="stat-label">Days active</span>
            <span class="stat-value">${signals.days_active || 0}</span>
            <span class="stat-label">+/- interactions</span>
            <span class="stat-value">${trust.positive || 0} / ${trust.negative || 0}</span>
        </div>
        ${p.summary ? `<div class="person-summary">${esc(p.summary)}</div>` : ''}
        ${notesHtml ? '<div class="person-notes">' + notesHtml + '</div>' : ''}
    `;
    markUpdated('person');
}

// 2e: Context (Full LLM Prompt)
// System prompt gets styled readable rendering.
// History (user/assistant/tool turns) shown as raw text — no UI chrome
// that could be confused with content.
function updateContext(promptData) {
    const body = document.getElementById('body-context');
    const messages = promptData.messages;
    if (!messages || !Array.isArray(messages)) {
        body.innerHTML = '<div class="empty-state">No prompt data</div>';
        return;
    }

    let html = '<div class="context-sections">';

    // System prompt(s) — styled and readable
    const systemMsgs = messages.filter(m => m.role === 'system');
    if (systemMsgs.length > 0) {
        html += '<div class="context-system-block">';
        html += '<div class="context-section-label">SYSTEM PROMPT</div>';
        for (const msg of systemMsgs) {
            const content = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
            html += `<div class="context-system-content">${esc(content)}</div>`;
        }
        html += '</div>';
    }

    // History (user/assistant/tool) — raw, minimal formatting
    const historyMsgs = messages.filter(m => m.role !== 'system');
    if (historyMsgs.length > 0) {
        html += '<div class="context-history-block">';
        html += '<div class="context-section-label">CONVERSATION HISTORY (raw — what the LLM sees)</div>';
        html += '<pre class="context-history-raw">';
        for (const msg of historyMsgs) {
            const role = msg.role || 'unknown';
            let content = '';
            if (typeof msg.content === 'string') {
                content = msg.content;
            } else if (Array.isArray(msg.content)) {
                content = msg.content.filter(p => p.type === 'text').map(p => p.text).join('\n') || '[media]';
            } else {
                content = JSON.stringify(msg.content);
            }
            // Tool calls on assistant messages
            let toolCallStr = '';
            if (msg.tool_calls && msg.tool_calls.length > 0) {
                const names = msg.tool_calls.map(tc => tc.function ? tc.function.name : tc.name || '?').join(', ');
                toolCallStr = `  [tool_calls: ${names}]`;
            }
            html += `<span class="history-role">${esc(role)}</span>${esc(toolCallStr)}\n${esc(content)}\n\n`;
        }
        html += '</pre>';
        html += '</div>';
    }

    html += '</div>';
    body.innerHTML = html;
    markUpdated('context');
}

// 2f: Memory & Diary
async function fetchMemory() {
    const data = await fetchJSON('/api/memory');
    if (!data || data.error) return;

    renderMemory(data);
}

function renderMemory(data) {
    const body = document.getElementById('body-memory');
    const total = data.total_tokens || 0;
    const budget = data.budget || 4000;
    const pct = Math.min(100, Math.round(total / budget * 100));
    const fillClass = pct >= 80 ? 'critical' : pct >= 50 ? 'warning' : '';

    let sectionsHtml = '';
    const sections = data.sections || {};
    for (const [name, sec] of Object.entries(sections)) {
        const used = sec.used_tokens || 0;
        const max = sec.max_tokens || 1;
        const secPct = Math.min(100, Math.round(used / max * 100));
        sectionsHtml += `<div class="memory-section-row">
            <span class="memory-section-name">${esc(sec.label || name)}</span>
            <div class="memory-section-bar">
                <div class="memory-section-fill" style="width:${secPct}%"></div>
            </div>
            <span class="memory-section-count">${used}/${max}</span>
        </div>`;
    }

    body.innerHTML = `
        <div class="memory-budget-total">
            <div class="budget-label"><span>Total Budget</span><span>${total} / ${budget} tokens</span></div>
            <div class="budget-bar"><div class="budget-fill ${fillClass}" style="width:${pct}%"></div></div>
        </div>
        <div class="memory-sections">${sectionsHtml}</div>
    `;
    markUpdated('memory');
}

// 2g: LLM Processing
function updateLLMCall(evt) {
    llmModel = evt.model || llmModel;
    llmIterations = evt.iteration || llmIterations;

    const body = document.getElementById('body-llm');
    body.innerHTML = `
        <div class="llm-header-info">
            <span>Model: <strong>${esc(llmModel)}</strong></span>
            <span>Iteration: <strong>${llmIterations}</strong></span>
            <span>Messages: <strong>${evt.message_count || '?'}</strong></span>
            <span>Tools available: <strong>${evt.tool_count || '?'}</strong></span>
        </div>
        <div class="llm-tools" id="llm-tool-list"></div>
        <div class="llm-summary" id="llm-summary">Processing...</div>
    `;
    markUpdated('llm');
}

function addToolExec(evt) {
    toolCards.push({
        name: evt.tool_name || '?',
        latency: evt.latency_ms || 0,
        args: evt.args_preview || '',
        result: evt.result_preview || '',
    });

    const list = document.getElementById('llm-tool-list');
    if (!list) return;

    list.innerHTML = toolCards.map(t => `
        <div class="tool-card">
            <div class="tool-card-header">
                <span class="tool-name">${esc(t.name)}</span>
                <span class="tool-latency">${t.latency}ms</span>
            </div>
            <div class="tool-args-label">args</div>
            <div class="tool-args">${esc(t.args)}</div>
            <div class="tool-result-label">result</div>
            <div class="tool-result">${esc(t.result)}</div>
        </div>
    `).join('');
    markUpdated('llm');
}

function updateLLMResponse(evt) {
    llmTotalLatency += (evt.latency_ms || 0);
    llmIterations = evt.iteration || llmIterations;
    markUpdated('llm');
}

function updateLoopBreak(evt) {
    const el = document.getElementById('llm-summary');
    if (!el) return;

    const tools = (evt.tools_used || []).join(', ') || 'none';
    el.innerHTML = `
        Total: <strong>${evt.iterations || llmIterations}</strong> iterations,
        <strong>${llmTotalLatency}ms</strong>,
        reason: <strong>${esc(evt.reason || '?')}</strong>,
        tools: ${esc(tools)}
    `;
    markUpdated('llm');
}

// 2h: Ene's Response
function updateResponseFull(data) {
    const body = document.getElementById('body-response');
    const content = data.content || '';
    if (!content) {
        body.innerHTML = '<div class="empty-state">No response content</div>';
        return;
    }

    body.innerHTML = `
        <div class="response-text">${esc(content)}</div>
        <div class="response-stats" id="response-stats">
            <span>Iteration: ${data.iteration || '?'}</span>
            <span>Latency: ${data.latency_ms || '?'}ms</span>
        </div>
    `;
    markUpdated('response');
}

function updateResponseClean(evt) {
    const el = document.getElementById('response-stats');
    if (!el) return;

    let stats = `Raw: ${evt.raw_length || '?'} → Clean: ${evt.clean_length || '?'} chars`;
    if (evt.was_blocked) stats += ' <span class="response-stat-error">BLOCKED</span>';
    if (evt.was_truncated) stats += ' <span class="response-stat-warn">truncated</span>';
    el.innerHTML = stats;
    markUpdated('response');
}

function updateResponseSent(evt) {
    const el = document.getElementById('response-stats');
    if (!el) return;

    const replyTo = evt.reply_to ? ` | Reply to: ${esc(evt.reply_to)}` : ' | New message';
    el.innerHTML += replyTo;
    markUpdated('response');
}

// ── Pipeline Clearing / Reset ──────────────────────────────────

function clearPipeline(reason) {
    if (pipelineDimmed) return;
    pipelineDimmed = true;

    const overlay = document.getElementById('pipeline-overlay');
    const overlayMsg = document.getElementById('pipeline-overlay-msg');
    const overlayIcon = document.getElementById('pipeline-overlay-icon');
    overlay.classList.add('visible');
    overlayMsg.textContent = reason || 'Idle';
    overlayIcon.textContent = reason.includes('Brain OFF') ? '🧠' : '⏸';

    // Dim pipeline sections (except intake)
    const sections = ['daemon', 'classify', 'person', 'memory', 'context', 'llm', 'response'];
    for (const s of sections) {
        const el = document.getElementById('section-' + s);
        if (el) el.classList.add('dimmed');
    }
}

function resetPipeline() {
    pipelineDimmed = false;

    const overlay = document.getElementById('pipeline-overlay');
    overlay.classList.remove('visible');

    const sections = ['daemon', 'classify', 'person', 'memory', 'context', 'llm', 'response'];
    for (const s of sections) {
        const el = document.getElementById('section-' + s);
        if (el) el.classList.remove('dimmed');
    }

    // Reset accumulated state for new cycle
    classRows = [];
    toolCards = [];
    llmIterations = 0;
    llmModel = '';
    llmTotalLatency = 0;
    mergeData = null;
    respondDecision = null;
}

function clearAll() {
    resetPipeline();
    // Reset all section bodies
    const sections = ['intake', 'daemon', 'classify', 'person', 'memory', 'context', 'llm', 'response'];
    for (const s of sections) {
        const body = document.getElementById('body-' + s);
        if (body) body.innerHTML = '<div class="empty-state">Waiting...</div>';
    }
    // Reset timers
    for (const key of Object.keys(timers)) timers[key] = null;
}

// ── Event SSE Router ───────────────────────────────────────────

function handleEvent(evt) {
    switch (evt.type) {
        case 'msg_arrived':
            resetPipeline();
            updateIntake(evt);
            break;

        case 'debounce_add':
        case 'debounce_flush':
            updateIntakeBuffer(evt);
            if (evt.type === 'debounce_flush') {
                // Reset accumulators for new batch
                classRows = [];
                toolCards = [];
                llmIterations = 0;
                llmTotalLatency = 0;
                mergeData = null;
                respondDecision = null;
            }
            break;

        case 'daemon_result':
            // Event stream daemon_result has timing data
            // Full data comes from prompt stream's prompt_daemon_response
            updateDaemon(evt);
            break;

        case 'classification':
            addClassification(evt);
            break;

        case 'dad_promotion':
            classRows.push({
                sender: '(all)',
                result: 'respond',
                source: 'dad_promotion',
                override: `${evt.count || '?'} msgs promoted`,
            });
            renderClassify();
            break;

        case 'merge_complete':
            updateMerge(evt);
            break;

        case 'should_respond':
            updateShouldRespond(evt);
            if (!evt.decision) {
                clearPipeline(evt.reason ? 'Lurking — ' + evt.reason : 'Lurking — no response needed');
            } else {
                // Trigger memory fetch when Ene is going to respond
                fetchMemory();
            }
            break;

        case 'llm_call':
            updateLLMCall(evt);
            break;

        case 'llm_response':
            updateLLMResponse(evt);
            break;

        case 'tool_exec':
            addToolExec(evt);
            break;

        case 'loop_break':
            updateLoopBreak(evt);
            break;

        case 'response_clean':
            updateResponseClean(evt);
            break;

        case 'response_sent':
            updateResponseSent(evt);
            break;

        case 'brain_paused':
            clearPipeline('Brain OFF — messages observed, not processed');
            break;

        case 'brain_status_changed':
            pollBrain();
            if (evt.status === 'paused') {
                clearPipeline('Brain OFF — messages observed, not processed');
            }
            break;

        case 'rate_limited':
        case 'mute_event':
            showIntakeNotice(evt);
            break;

        case 'hard_reset':
            clearAll();
            break;

        case 'error':
            // Show error inline — could enhance to show in specific section
            console.error('Pipeline error:', evt.stage, evt.error_message);
            break;
    }
}

// ── Prompt SSE Router ──────────────────────────────────────────

function handlePrompt(entry) {
    switch (entry.type) {
        case 'prompt_daemon_response':
            // Full daemon analysis — primary data source for daemon section
            updateDaemon(entry);
            break;

        case 'prompt_ene':
            // Full prompt array — render in context section
            updateContext(entry);
            break;

        case 'prompt_ene_response':
            // Full response text — render in response section
            updateResponseFull(entry);
            break;
    }
}

// ── SSE Connections ────────────────────────────────────────────

function connectEventSSE() {
    const badge = document.getElementById('evt-badge');
    if (badge) { badge.textContent = 'events: connecting'; badge.className = 'badge connecting'; }

    evtSource = new EventSource(`${API}/api/live?last_id=${lastEventId}`);

    evtSource.addEventListener('event', e => {
        const evt = JSON.parse(e.data);
        if (evt.id) lastEventId = evt.id;
        handleEvent(evt);
    });

    evtSource.addEventListener('state', e => {
        const state = JSON.parse(e.data);
        // Update buffer info in intake if visible
        const bufEl = document.getElementById('intake-buffer-info');
        if (bufEl && state.buffers) {
            const totalBuf = Object.values(state.buffers).reduce((a, b) => a + b, 0);
            if (totalBuf > 0) bufEl.textContent = `Buffer: ${totalBuf} msgs`;
        }
    });

    evtSource.onopen = () => {
        if (badge) { badge.textContent = 'events: live'; badge.className = 'badge connected'; }
    };

    evtSource.onerror = () => {
        if (badge) { badge.textContent = 'events: offline'; badge.className = 'badge disconnected'; }
        evtSource.close();
        setTimeout(connectEventSSE, 3000);
    };
}

function connectPromptSSE() {
    const badge = document.getElementById('prompt-badge');
    if (badge) { badge.textContent = 'prompts: connecting'; badge.className = 'badge connecting'; }

    promptSource = new EventSource(`${API}/api/live/prompts?last_id=${lastPromptId}`);

    promptSource.addEventListener('prompt', e => {
        const entry = JSON.parse(e.data);
        if (entry.id) lastPromptId = entry.id;
        handlePrompt(entry);
    });

    promptSource.onopen = () => {
        if (badge) { badge.textContent = 'prompts: live'; badge.className = 'badge connected'; }
    };

    promptSource.onerror = () => {
        if (badge) { badge.textContent = 'prompts: offline'; badge.className = 'badge disconnected'; }
        promptSource.close();
        setTimeout(connectPromptSSE, 3000);
    };
}

// ── Init ───────────────────────────────────────────────────────

connectEventSSE();
connectPromptSSE();
pollBrain();
pollThreads();

setInterval(tickTimers, TIMER_TICK_MS);
setInterval(pollThreads, THREAD_POLL_MS);
setInterval(pollBrain, BRAIN_POLL_MS);
