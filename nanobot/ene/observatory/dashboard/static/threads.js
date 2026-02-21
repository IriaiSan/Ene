/* Ene Thread Inspector — Node-based thread graph visualization
 * Vanilla JS, SVG for graph rendering, SSE for live events.
 *
 * Layout: Messages arrive on the LEFT column. Thread nodes live on the
 * RIGHT column. Edges connect messages → threads with score labels.
 * Pending messages sit in a middle "waiting" zone.
 */

const API = '';
let eventCount = 0;
let lastEventId = 0;
let evtSource = null;

// ── Graph state ──────────────────────────────────────
// All nodes and edges tracked in memory, rendered to SVG.

const MSG_W = 200;   // Message node width
const MSG_H = 32;    // Message node height
const THREAD_W = 160; // Thread node width
const THREAD_H = 56;  // Thread node height
const PEND_W = 140;   // Pending node width
const PEND_H = 28;    // Pending node height
const MSG_X = 20;     // Message column x
const PEND_X = 280;   // Pending column x
const THREAD_X = 480; // Thread column x
const ROW_GAP = 8;    // Gap between rows
const TOP_PAD = 20;   // Top padding

// Node registries
const msgNodes = {};      // msg_id → { y, author, content, cls, el, edgeTo, assignmentData }
const threadNodes = {};   // thread_id → { y, state, msgCount, keywords, participants, el }
const pendingNodes = {};  // msg_id → { y, author, content, el }
const edges = [];         // [{ from, to, outcome, score, el }]

let nextMsgY = TOP_PAD;
let nextThreadY = TOP_PAD;
let nextPendY = TOP_PAD;

// DOM refs
const connBadge = document.getElementById('connection-badge');
const countEl = document.getElementById('event-count');
const btnClear = document.getElementById('btn-clear');
const msgContainer = document.getElementById('msg-container');
const msgLog = document.getElementById('msg-log');
const svg = document.getElementById('graph-svg');
const edgesLayer = document.getElementById('edges-layer');
const nodesLayer = document.getElementById('nodes-layer');
const detailPanel = document.getElementById('detail-panel');
const detailTitle = document.getElementById('detail-title');
const detailBody = document.getElementById('detail-body');
const detailClose = document.getElementById('detail-close');
const graphViewport = document.getElementById('graph-viewport');

// Stats elements
const statThreads = document.getElementById('graph-threads');
const statPending = document.getElementById('graph-pending');
const statMsgs = document.getElementById('graph-msgs');

// Message metadata cache (for left panel reply lookups)
const msgCache = {};

// ── Helpers ──────────────────────────────────────────

function esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = String(str);
    return d.innerHTML;
}

function authorHue(name) {
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
        hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    return Math.abs(hash % 360);
}

function authorColor(name) {
    return `hsl(${authorHue(name)}, 60%, 65%)`;
}

function truncate(str, max) {
    if (!str) return '';
    return str.length > max ? str.slice(0, max - 1) + '\u2026' : str;
}

function svgEl(tag, attrs) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs || {})) {
        el.setAttribute(k, v);
    }
    return el;
}

function updateSvgSize() {
    const maxY = Math.max(nextMsgY, nextThreadY, nextPendY) + 40;
    svg.setAttribute('height', maxY);
    svg.style.minHeight = maxY + 'px';
}

function updateStats() {
    statThreads.textContent = Object.keys(threadNodes).length + ' threads';
    statPending.textContent = Object.keys(pendingNodes).length + ' pending';
    statMsgs.textContent = Object.keys(msgNodes).length + ' msgs';
}

// ── Left Panel: Discord-style message log ────────────

function addChatMessage(evt) {
    const empty = msgContainer.querySelector('.empty-state');
    if (empty) empty.remove();

    const msgId = evt.msg_id || '';
    const sender = evt.sender || '?';
    const content = evt.content_preview || '';
    const replyTo = evt.reply_to || null;
    const ts = evt.ts || '';

    msgCache[msgId] = { sender, content };

    const el = document.createElement('div');
    el.className = 'chat-msg';
    el.dataset.msgId = msgId;

    let replyHtml = '';
    if (replyTo) {
        const parent = msgCache[replyTo];
        const parentAuthor = parent ? parent.sender : '...';
        const parentContent = parent ? parent.content : '';
        replyHtml = `<div class="chat-reply-bar">
            <span class="chat-reply-author">${esc(parentAuthor)}</span>
            <span class="chat-reply-content">${esc(parentContent)}</span>
        </div>`;
    }

    const initial = sender.charAt(0).toUpperCase();
    const color = authorColor(sender);

    el.innerHTML = `
        <div class="chat-avatar" style="background:${color}">${esc(initial)}</div>
        <div class="chat-body">
            ${replyHtml}
            <div class="chat-author-line">
                <span class="chat-author" style="color:${color}">${esc(sender)}</span>
                <span class="chat-ts">${esc(ts)}</span>
                <span class="chat-cls cls-unknown" data-msg-id="${esc(msgId)}" title="awaiting classification"></span>
            </div>
            <div class="chat-content">${esc(content)}</div>
        </div>
    `;

    // Click to select corresponding graph node
    el.addEventListener('click', () => {
        document.querySelectorAll('.chat-msg.selected').forEach(m => m.classList.remove('selected'));
        el.classList.add('selected');
        selectMsgNode(msgId);
    });

    msgContainer.appendChild(el);
    msgLog.scrollTop = msgLog.scrollHeight;

    while (msgContainer.children.length > 200) {
        msgContainer.removeChild(msgContainer.firstChild);
    }
}

function updateClassification(evt) {
    const sender = evt.sender || '';
    const result = (evt.result || evt.classification || '').toLowerCase();
    const dots = msgContainer.querySelectorAll('.chat-cls');
    for (let i = dots.length - 1; i >= 0; i--) {
        const dot = dots[i];
        if (dot.classList.contains('cls-unknown')) {
            const chatMsg = dot.closest('.chat-msg');
            if (!chatMsg) continue;
            const authorEl = chatMsg.querySelector('.chat-author');
            if (authorEl && authorEl.textContent === sender) {
                dot.classList.remove('cls-unknown');
                if (result === 'respond') dot.classList.add('cls-respond');
                else if (result === 'context') dot.classList.add('cls-context');
                else dot.classList.add('cls-drop');
                dot.title = result.toUpperCase();
                break;
            }
        }
    }
}

// ── SVG Graph: Message nodes ─────────────────────────

function addMsgNode(msgId, author, content) {
    if (msgNodes[msgId]) return; // Already exists

    const y = nextMsgY;
    nextMsgY += MSG_H + ROW_GAP;

    const g = svgEl('g', { class: 'msg-node', 'data-msg-id': msgId });
    g.appendChild(svgEl('rect', { x: MSG_X, y, width: MSG_W, height: MSG_H }));

    const authorText = svgEl('text', {
        x: MSG_X + 8, y: y + 14,
        class: 'node-author',
    });
    authorText.textContent = truncate(author, 16);
    authorText.style.fill = authorColor(author);
    g.appendChild(authorText);

    const contentText = svgEl('text', {
        x: MSG_X + 8, y: y + 26,
        class: 'node-content',
    });
    contentText.textContent = truncate(content, 28);
    g.appendChild(contentText);

    g.addEventListener('click', () => {
        selectMsgNode(msgId);
        // Also highlight left panel
        document.querySelectorAll('.chat-msg.selected').forEach(m => m.classList.remove('selected'));
        const chatMsg = document.querySelector(`.chat-msg[data-msg-id="${msgId}"]`);
        if (chatMsg) {
            chatMsg.classList.add('selected');
            chatMsg.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    });

    nodesLayer.appendChild(g);
    msgNodes[msgId] = { y, author, content, cls: null, el: g, edgeTo: null, assignmentData: null };
    updateSvgSize();
    updateStats();
}

function selectMsgNode(msgId) {
    // Deselect all
    nodesLayer.querySelectorAll('.selected').forEach(n => n.classList.remove('selected'));

    const node = msgNodes[msgId];
    if (!node) return;
    node.el.classList.add('selected');

    // Scroll graph to make node visible
    const nodeY = node.y;
    graphViewport.scrollTop = Math.max(0, nodeY - 100);

    // Show detail if we have assignment data
    if (node.assignmentData) {
        showAssignmentDetail(node.assignmentData);
    }
}

// ── SVG Graph: Thread nodes ──────────────────────────

function ensureThreadNode(threadId, state, msgCount, keywords, participants) {
    if (threadNodes[threadId]) {
        // Update existing
        const tn = threadNodes[threadId];
        tn.state = state || tn.state;
        tn.msgCount = msgCount || tn.msgCount;
        tn.keywords = keywords || tn.keywords;
        tn.participants = participants || tn.participants;
        updateThreadNodeVisual(threadId);
        return;
    }

    const y = nextThreadY;
    nextThreadY += THREAD_H + ROW_GAP;

    const g = svgEl('g', { class: `thread-node state-${(state || 'ACTIVE').toUpperCase()}`, 'data-thread-id': threadId });
    g.appendChild(svgEl('rect', { x: THREAD_X, y, width: THREAD_W, height: THREAD_H }));

    const idText = svgEl('text', { x: THREAD_X + 8, y: y + 16, class: 'thread-id' });
    idText.textContent = threadId;
    g.appendChild(idText);

    const stateText = svgEl('text', { x: THREAD_X + THREAD_W - 8, y: y + 16, class: `thread-state state-${(state || 'ACTIVE').toUpperCase()}`, 'text-anchor': 'end' });
    stateText.textContent = (state || 'ACTIVE').toUpperCase();
    g.appendChild(stateText);

    const metaText = svgEl('text', { x: THREAD_X + 8, y: y + 32, class: 'thread-meta' });
    metaText.textContent = `${msgCount || 0} msgs`;
    if (participants && participants.length) metaText.textContent += ` · ${participants.slice(0, 3).join(', ')}`;
    g.appendChild(metaText);

    const kwText = svgEl('text', { x: THREAD_X + 8, y: y + 46, class: 'thread-keywords' });
    kwText.textContent = keywords ? keywords.slice(0, 4).join(', ') : '';
    g.appendChild(kwText);

    g.addEventListener('click', () => {
        nodesLayer.querySelectorAll('.selected').forEach(n => n.classList.remove('selected'));
        g.classList.add('selected');
        showThreadDetail(threadId);
    });

    nodesLayer.appendChild(g);
    threadNodes[threadId] = { y, state: state || 'ACTIVE', msgCount: msgCount || 0, keywords: keywords || [], participants: participants || [], el: g };
    updateSvgSize();
    updateStats();
}

function updateThreadNodeVisual(threadId) {
    const tn = threadNodes[threadId];
    if (!tn) return;
    const g = tn.el;

    // Update class for state color
    g.setAttribute('class', `thread-node state-${(tn.state || 'ACTIVE').toUpperCase()}${g.classList.contains('selected') ? ' selected' : ''}`);

    // Update state text
    const stateEl = g.querySelector('.thread-state');
    if (stateEl) {
        stateEl.textContent = (tn.state || 'ACTIVE').toUpperCase();
        stateEl.setAttribute('class', `thread-state state-${(tn.state || 'ACTIVE').toUpperCase()}`);
    }

    // Update meta
    const metaEl = g.querySelector('.thread-meta');
    if (metaEl) {
        let meta = `${tn.msgCount || 0} msgs`;
        if (tn.participants && tn.participants.length) meta += ` · ${tn.participants.slice(0, 3).join(', ')}`;
        metaEl.textContent = meta;
    }

    // Update keywords
    const kwEl = g.querySelector('.thread-keywords');
    if (kwEl) kwEl.textContent = tn.keywords ? tn.keywords.slice(0, 4).join(', ') : '';
}

// ── SVG Graph: Pending nodes ─────────────────────────

function addPendingNode(msgId, author, content) {
    if (pendingNodes[msgId]) return;

    const y = nextPendY;
    nextPendY += PEND_H + ROW_GAP;

    const g = svgEl('g', { class: 'pending-node', 'data-msg-id': msgId });
    g.appendChild(svgEl('rect', { x: PEND_X, y, width: PEND_W, height: PEND_H }));

    const text = svgEl('text', { x: PEND_X + 8, y: y + 18 });
    text.textContent = `${truncate(author, 10)}: ${truncate(content, 14)}`;
    g.appendChild(text);

    nodesLayer.appendChild(g);
    pendingNodes[msgId] = { y, author, content, el: g };
    updateSvgSize();
    updateStats();
}

function removePendingNode(msgId) {
    const pn = pendingNodes[msgId];
    if (!pn) return;
    pn.el.remove();
    delete pendingNodes[msgId];
    updateStats();
}

// ── SVG Graph: Edges ─────────────────────────────────

function addEdge(fromMsgId, toThreadId, outcome, score) {
    const mn = msgNodes[fromMsgId];
    const tn = threadNodes[toThreadId];
    if (!mn || !tn) return;

    const x1 = MSG_X + MSG_W;
    const y1 = mn.y + MSG_H / 2;
    const x2 = THREAD_X;
    const y2 = tn.y + THREAD_H / 2;

    // Bezier curve from msg → thread
    const cx1 = x1 + (x2 - x1) * 0.4;
    const cx2 = x1 + (x2 - x1) * 0.6;
    const d = `M${x1},${y1} C${cx1},${y1} ${cx2},${y2} ${x2},${y2}`;

    const arrowId = outcome === 'assigned' ? 'arrow-green'
        : outcome === 'promoted' ? 'arrow-cyan'
        : outcome === 'fast' ? 'arrow-accent'
        : 'arrow-yellow';

    const g = svgEl('g', { class: 'edge-group' });

    const path = svgEl('path', {
        d,
        class: `edge-line outcome-${outcome || 'assigned'}`,
        'marker-end': `url(#${arrowId})`,
    });
    g.appendChild(path);

    // Score label at midpoint
    if (score !== undefined && score !== null) {
        const mx = (x1 + x2) / 2;
        const my = (y1 + y2) / 2 - 6;
        const label = svgEl('text', { x: mx, y: my, class: 'edge-label', 'text-anchor': 'middle' });
        label.textContent = typeof score === 'number' ? score.toFixed(1) : score;
        g.appendChild(label);
    }

    edgesLayer.appendChild(g);
    edges.push({ from: fromMsgId, to: toThreadId, outcome, score, el: g });
    mn.edgeTo = toThreadId;
}

function addPendingEdge(fromMsgId) {
    const mn = msgNodes[fromMsgId];
    const pn = pendingNodes[fromMsgId];
    if (!mn || !pn) return;

    const x1 = MSG_X + MSG_W;
    const y1 = mn.y + MSG_H / 2;
    const x2 = PEND_X;
    const y2 = pn.y + PEND_H / 2;

    const cx1 = x1 + (x2 - x1) * 0.4;
    const cx2 = x1 + (x2 - x1) * 0.6;
    const d = `M${x1},${y1} C${cx1},${y1} ${cx2},${y2} ${x2},${y2}`;

    const g = svgEl('g', { class: 'edge-group' });
    const path = svgEl('path', {
        d,
        class: 'edge-line outcome-pending',
        'marker-end': 'url(#arrow-yellow)',
    });
    g.appendChild(path);

    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2 - 6;
    const label = svgEl('text', { x: mx, y: my, class: 'edge-label', 'text-anchor': 'middle' });
    label.textContent = 'pending';
    g.appendChild(label);

    edgesLayer.appendChild(g);
    edges.push({ from: fromMsgId, to: 'pending:' + fromMsgId, outcome: 'pending', score: null, el: g });
}

// ── Detail Panel ─────────────────────────────────────

const SIGNAL_MAX = {
    reply_chain: 1.0,
    mention: 0.9,
    temporal: 0.4,
    speaker: 0.4,
    lexical: 0.3,
};

function showAssignmentDetail(data) {
    detailPanel.classList.remove('hidden');
    const outcomeClass = data.fast_path ? 'fast' : (data.outcome || 'pending');
    detailTitle.textContent = `${data.author}: "${truncate(data.content_preview, 40)}"`;

    let html = '';

    if (data.fast_path) {
        html += `<div class="detail-outcome ${outcomeClass}">Fast path: ${esc(data.fast_path)} → Thread ${esc(data.assigned_to || '?')}</div>`;
    } else {
        const threshold = data.threshold || 0.5;

        // Thread scores
        const threadScores = data.thread_scores || {};
        const threadKeys = Object.keys(threadScores);
        if (threadKeys.length > 0) {
            html += `<div style="margin-bottom:4px;font-weight:600">Thread Scoring (threshold: ${threshold}):</div>`;
            for (const tid of threadKeys) {
                const s = threadScores[tid];
                const pass = s.total >= threshold;
                const isBest = data.best_thread_id === tid;
                html += `<div class="detail-thread-section">`;
                html += `<div class="detail-thread-header">`;
                html += `<span class="detail-thread-id">${esc(tid)}</span>`;
                html += `<span class="${pass ? 'detail-pass' : 'detail-fail'}">${pass ? '\u2713' : '\u2717'} ${s.total.toFixed(2)}</span>`;
                if (isBest) html += `<span class="detail-pass">\u2605 best</span>`;
                html += `<span style="color:var(--text-dim);font-size:9px">${s.msg_count || '?'} msgs · [${(s.keywords || []).join(', ')}]</span>`;
                html += `</div>`;

                html += `<div class="detail-scores">`;
                for (const [name, maxVal] of Object.entries(SIGNAL_MAX)) {
                    const val = s[name] || 0;
                    const pct = maxVal > 0 ? Math.round(val / maxVal * 100) : 0;
                    html += `<div class="detail-score-row">`;
                    html += `<span class="detail-score-label">${name}:</span>`;
                    html += `<div class="detail-score-bar"><div class="detail-score-fill ${val > 0 ? '' : 'zero'}" style="width:${pct}%"></div></div>`;
                    html += `<span class="detail-score-value">${val.toFixed(2)}</span>`;
                    html += `</div>`;
                }
                html += `</div>`;
                html += `</div>`;
            }
        } else {
            html += `<div style="color:var(--text-dim)">No active threads to score against</div>`;
        }

        // Pending scores
        const pendingScores = data.pending_scores || {};
        const pendingKeys = Object.keys(pendingScores);
        if (pendingKeys.length > 0) {
            html += `<div style="margin:6px 0 4px;font-weight:600">Pending Scoring:</div>`;
            for (const pid of pendingKeys) {
                const p = pendingScores[pid];
                const pass = p.total >= threshold;
                html += `<div class="detail-thread-section">`;
                html += `<span class="${pass ? 'detail-pass' : 'detail-fail'}">${pass ? '\u2713' : '\u2717'} ${p.total.toFixed(2)}</span> `;
                html += `${esc(p.author || pid)}: "${esc(p.content_preview || '')}"`;
                html += `</div>`;
            }
        }

        // Outcome
        const outcomeLabel = data.outcome === 'assigned'
            ? `Assigned to Thread ${esc(data.assigned_to || '?')} (score: ${data.best_thread_score})`
            : data.outcome === 'promoted'
                ? `Promoted pending → Thread ${esc(data.assigned_to || '?')} (score: ${data.best_pending_score})`
                : `Added to Pending (best thread: ${data.best_thread_score || 0}, best pending: ${data.best_pending_score || 0}, threshold: ${threshold})`;
        html += `<div class="detail-outcome ${outcomeClass}">${outcomeLabel}</div>`;
    }

    if (data.keywords && data.keywords.length) {
        html += `<div style="margin-top:4px;color:var(--text-dim)">Keywords: ${data.keywords.map(k => `<code>${esc(k)}</code>`).join(' ')}</div>`;
    }

    detailBody.innerHTML = html;
}

function showThreadDetail(threadId) {
    const tn = threadNodes[threadId];
    if (!tn) return;

    detailPanel.classList.remove('hidden');
    detailTitle.textContent = `Thread ${threadId}`;

    let html = `<div style="margin-bottom:6px">`;
    html += `<b>State:</b> <span class="detail-${tn.state === 'ACTIVE' ? 'pass' : 'fail'}">${tn.state}</span> · `;
    html += `<b>Messages:</b> ${tn.msgCount} · `;
    html += `<b>Keywords:</b> ${(tn.keywords || []).join(', ') || 'none'}`;
    html += `</div>`;

    // Find all edges pointing to this thread
    const incoming = edges.filter(e => e.to === threadId);
    if (incoming.length > 0) {
        html += `<div style="font-weight:600;margin-bottom:4px">${incoming.length} messages assigned to this thread:</div>`;
        for (const edge of incoming) {
            const mn = msgNodes[edge.from];
            if (mn) {
                html += `<div style="padding:2px 0;color:var(--text-secondary)">`;
                html += `<span style="color:${authorColor(mn.author)};font-weight:600">${esc(mn.author)}</span>: `;
                html += `"${esc(truncate(mn.content, 50))}" `;
                html += `<span class="detail-${edge.outcome || 'assigned'}" style="font-size:10px">[${edge.outcome}, score: ${edge.score || '?'}]</span>`;
                html += `</div>`;
            }
        }
    }

    detailBody.innerHTML = html;
}

detailClose.addEventListener('click', () => {
    detailPanel.classList.add('hidden');
});

// Start with detail panel hidden
detailPanel.classList.add('hidden');

// ── Thread Assignment Handler ────────────────────────

function handleThreadAssignment(evt) {
    const msgId = evt.msg_id || '';
    const author = evt.author || '?';
    const content = evt.content_preview || '';
    const outcome = evt.fast_path ? 'fast' : (evt.outcome || 'pending');
    const assignedTo = evt.assigned_to || null;

    // Ensure message node exists on graph
    addMsgNode(msgId, author, content);

    // Store full assignment data for detail panel
    if (msgNodes[msgId]) {
        msgNodes[msgId].assignmentData = evt;
    }

    if (outcome === 'pending') {
        // Add pending node and edge
        addPendingNode(msgId, author, content);
        addPendingEdge(msgId);
    } else if (assignedTo) {
        // Ensure thread node exists
        const threadScores = evt.thread_scores || {};
        const threadInfo = threadScores[assignedTo] || {};
        ensureThreadNode(
            assignedTo,
            threadInfo.state || 'ACTIVE',
            threadInfo.msg_count || 0,
            threadInfo.keywords || evt.keywords || [],
            []
        );

        // If this was a pending message that got promoted, remove the pending node
        if (outcome === 'promoted' || evt.outcome === 'promoted') {
            // Check if there's a pending node for this msg or the matched pending
            removePendingNode(msgId);
            // Also try to remove edges pointing to old pending
            const oldEdges = edges.filter(e => e.to === 'pending:' + msgId);
            for (const oe of oldEdges) oe.el.remove();
        }

        // Determine score for edge label
        const score = outcome === 'fast' ? 'fast'
            : (evt.best_thread_score || evt.best_pending_score || null);

        addEdge(msgId, assignedTo, outcome, score);
    }

    updateSvgSize();
}

// ── Thread Lifecycle Handler ─────────────────────────

function handleThreadLifecycle(evt) {
    const threadId = evt.thread_id || '';
    if (!threadId) return;

    if (threadNodes[threadId]) {
        threadNodes[threadId].state = evt.new_state || 'DEAD';
        threadNodes[threadId].msgCount = evt.msg_count || threadNodes[threadId].msgCount;
        updateThreadNodeVisual(threadId);
    }
}

function handleThreadSplit(evt) {
    const parentId = evt.parent_id || '';
    const childId = evt.child_id || '';

    // Create child thread node
    ensureThreadNode(childId, 'ACTIVE', 1, evt.child_keywords || [], []);

    // If parent exists, we could draw a split edge (parent → child)
    if (parentId && threadNodes[parentId] && threadNodes[childId]) {
        const ptn = threadNodes[parentId];
        const ctn = threadNodes[childId];

        const x1 = THREAD_X + THREAD_W / 2;
        const y1 = ptn.y + THREAD_H;
        const x2 = THREAD_X + THREAD_W / 2;
        const y2 = ctn.y;

        const g = svgEl('g', { class: 'edge-group' });
        const path = svgEl('path', {
            d: `M${x1},${y1} L${x2},${y2}`,
            class: 'edge-line',
            stroke: '#a78bfa',
            'stroke-dasharray': '4 3',
            'marker-end': 'url(#arrow-cyan)',
        });
        g.appendChild(path);

        const label = svgEl('text', {
            x: x1 + 10, y: (y1 + y2) / 2,
            class: 'edge-label',
        });
        label.textContent = 'split';
        g.appendChild(label);

        edgesLayer.appendChild(g);
    }
}

function handleThreadResolved(evt) {
    const threadId = evt.thread_id || '';
    if (threadNodes[threadId]) {
        threadNodes[threadId].state = 'RESOLVED';
        updateThreadNodeVisual(threadId);
    }
}

function handlePendingExpired(evt) {
    // Try to find and fade the pending node
    for (const [id, pn] of Object.entries(pendingNodes)) {
        if (pn.author === evt.author) {
            pn.el.style.opacity = '0.3';
            setTimeout(() => removePendingNode(id), 3000);
            break;
        }
    }
}

// ── Polling for thread state (updates existing nodes) ──

async function pollThreadState() {
    try {
        const [threadsRes, pendingRes] = await Promise.all([
            fetch(`${API}/api/threads`),
            fetch(`${API}/api/threads/pending`),
        ]);
        if (threadsRes.ok) {
            const threads = await threadsRes.json();
            for (const t of threads) {
                const id = t.id ? t.id.slice(0, 8) : null;
                if (id) {
                    ensureThreadNode(id, (t.state || 'ACTIVE').toUpperCase(), t.msg_count, t.keywords, t.participants);
                }
            }
        }
        if (pendingRes.ok) {
            const pending = await pendingRes.json();
            // Sync pending nodes — add new ones, track existing
            const currentPendingIds = new Set();
            for (const p of pending) {
                const pid = p.msg_id || p.author;  // use whatever ID is available
                currentPendingIds.add(pid);
                if (!pendingNodes[pid]) {
                    addPendingNode(pid, p.author || '?', p.content || '');
                }
            }
        }
    } catch (e) { /* silent */ }
}

// ── SSE Connection ───────────────────────────────────

function connectSSE() {
    const url = `${API}/api/live?last_id=${lastEventId}`;
    evtSource = new EventSource(url);

    evtSource.addEventListener('event', (e) => {
        try {
            const evt = JSON.parse(e.data);
            handleEvent(evt);
        } catch (err) {
            console.warn('SSE parse error:', err);
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

function handleEvent(evt) {
    lastEventId = evt.id;
    eventCount++;
    countEl.textContent = `${eventCount} events`;

    switch (evt.type) {
        case 'msg_arrived':
            addChatMessage(evt);
            // Also add msg node to graph (will be positioned when assignment comes)
            addMsgNode(evt.msg_id || '', evt.sender || '?', evt.content_preview || '');
            break;

        case 'classification':
        case 'daemon_result':
            updateClassification(evt);
            break;

        case 'thread_assignment':
            handleThreadAssignment(evt);
            break;

        case 'thread_split':
            handleThreadSplit(evt);
            break;

        case 'thread_resolved':
            handleThreadResolved(evt);
            break;

        case 'thread_lifecycle':
            handleThreadLifecycle(evt);
            break;

        case 'pending_expired':
            handlePendingExpired(evt);
            break;

        case 'hard_reset':
            clearAll('Hard reset');
            break;
    }
}

// ── Controls ─────────────────────────────────────────

function clearAll(msg) {
    msgContainer.innerHTML = `<div class="empty-state"><p>${esc(msg || 'Cleared')}</p></div>`;
    eventCount = 0;
    countEl.textContent = '0 events';
    Object.keys(msgCache).forEach(k => delete msgCache[k]);

    // Clear graph
    edgesLayer.innerHTML = '';
    nodesLayer.innerHTML = '';
    Object.keys(msgNodes).forEach(k => delete msgNodes[k]);
    Object.keys(threadNodes).forEach(k => delete threadNodes[k]);
    Object.keys(pendingNodes).forEach(k => delete pendingNodes[k]);
    edges.length = 0;
    nextMsgY = TOP_PAD;
    nextThreadY = TOP_PAD;
    nextPendY = TOP_PAD;

    detailPanel.classList.add('hidden');
    updateStats();
}

btnClear.addEventListener('click', () => clearAll('Cleared'));

// ── Init ─────────────────────────────────────────────

connectSSE();
pollThreadState();
setInterval(pollThreadState, 3000);
