/* Ene Control Panel — vanilla JS, no build step.
 * Tab switching, CRUD operations, brain toggle.
 */

const API = '';  // Same origin

// ── Helpers ──────────────────────────────────────

function esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = String(str);
    return d.innerHTML;
}

async function fetchJSON(url, opts) {
    try {
        const res = await fetch(API + url, opts);
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            return { error: data.error || res.statusText, status: res.status };
        }
        return await res.json();
    } catch (e) {
        return { error: e.message };
    }
}

function timeAgo(ts) {
    if (!ts) return '—';
    const sec = Math.floor(Date.now() / 1000 - ts);
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
    return Math.floor(sec / 86400) + 'd ago';
}

function formatDate(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString();
}

function trustColor(score) {
    if (score >= 80) return 'var(--purple)';
    if (score >= 60) return 'var(--green)';
    if (score >= 35) return '#7ee87e';
    if (score >= 15) return 'var(--yellow)';
    return 'var(--text-secondary)';
}

function budgetColor(pct) {
    if (pct >= 80) return 'var(--red)';
    if (pct >= 50) return 'var(--yellow)';
    return 'var(--green)';
}

// ── Tab Switching ────────────────────────────────

const tabBtns = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');

tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        tabBtns.forEach(b => b.classList.toggle('active', b === btn));
        tabPanels.forEach(p => p.classList.toggle('active', p.id === 'panel-' + tab));
        loadTab(tab);
    });
});

const loadedTabs = {};
function loadTab(tab) {
    // Always refresh on tab switch
    switch (tab) {
        case 'people': loadPeople(); break;
        case 'memory': loadMemory(); break;
        case 'threads': loadThreads(); break;
        case 'sessions': loadSessions(); break;
        case 'security': loadSecurity(); break;
        case 'config': loadConfig(); break;
        case 'experiments': loadExperiments(); break;
    }
}

// ── Brain Toggle ─────────────────────────────────

const btnBrain = document.getElementById('btn-brain');
const brainLabel = document.getElementById('brain-label');
let brainEnabled = null;

async function pollBrain() {
    const data = await fetchJSON('/api/brain');
    if (data.error) {
        brainLabel.textContent = 'Unavailable';
        btnBrain.className = 'brain-toggle';
        return;
    }
    brainEnabled = data.enabled;
    updateBrainUI();
}

function updateBrainUI() {
    if (brainEnabled) {
        brainLabel.textContent = 'Brain ON';
        btnBrain.className = 'brain-toggle brain-on';
    } else {
        brainLabel.textContent = 'Brain OFF';
        btnBrain.className = 'brain-toggle brain-off';
    }
}

btnBrain.addEventListener('click', async () => {
    if (brainEnabled === null) return;

    const action = brainEnabled ? 'pause' : 'resume';
    const confirmMsg = brainEnabled
        ? 'Pause the brain? Ene will stop responding to messages (Discord stays connected, dashboard keeps working).'
        : 'Resume the brain? Ene will start responding to messages again.';

    if (!confirm(confirmMsg)) return;

    btnBrain.disabled = true;
    const data = await fetchJSON(`/api/brain/${action}`, { method: 'POST' });
    btnBrain.disabled = false;

    if (!data.error) {
        brainEnabled = data.enabled;
        updateBrainUI();
    }
});

// ── People Tab ───────────────────────────────────

const peopleBody = document.getElementById('people-body');
const personDetail = document.getElementById('person-detail');
const personDetailContent = document.getElementById('person-detail-content');
const personBack = document.getElementById('person-back');
const peopleListWrap = document.getElementById('people-list-wrap');

async function loadPeople() {
    const data = await fetchJSON('/api/people');
    if (data.error) {
        peopleBody.innerHTML = `<tr><td colspan="6" class="muted">${esc(data.error)}</td></tr>`;
        return;
    }
    if (!data.length) {
        peopleBody.innerHTML = '<tr><td colspan="6" class="muted">No people registered yet</td></tr>';
        return;
    }

    peopleBody.innerHTML = data.map(p => {
        const tier = p.tier || 'stranger';
        const color = trustColor(p.score);
        const pct = Math.min(100, p.score);
        return `<tr data-pid="${esc(p.id)}">
            <td>${esc(p.name)}</td>
            <td><span class="tier-badge tier-${tier}">${tier}</span></td>
            <td>
                <div class="trust-bar-wrap">
                    <div class="trust-bar"><div class="trust-bar-fill" style="width:${pct}%;background:${color}"></div></div>
                    <span class="trust-val">${p.score}%</span>
                </div>
            </td>
            <td>${p.msg_count || 0}</td>
            <td>${p.days_active || 0}</td>
            <td>${(p.platform_ids || []).map(x => esc(x)).join(', ')}</td>
        </tr>`;
    }).join('');

    // Click handler for rows
    peopleBody.querySelectorAll('tr[data-pid]').forEach(row => {
        row.addEventListener('click', () => loadPersonDetail(row.dataset.pid));
    });
}

async function loadPersonDetail(pid) {
    const data = await fetchJSON(`/api/people/${encodeURIComponent(pid)}`);
    if (data.error) {
        alert('Failed to load person: ' + data.error);
        return;
    }

    const t = data.trust || {};
    const signals = t.signals || {};
    const tier = t.tier || 'stranger';

    let html = `<h3 style="margin-bottom:12px">${esc(data.name)}</h3>`;

    // Trust overview
    html += `<div class="detail-section">
        <h3>Trust</h3>
        <div class="detail-row"><span class="detail-label">Tier</span><span class="detail-value"><span class="tier-badge tier-${tier}">${tier}</span></span></div>
        <div class="detail-row"><span class="detail-label">Score</span><span class="detail-value">${t.score}%</span></div>
        <div class="detail-row"><span class="detail-label">Positive</span><span class="detail-value">${t.positive || 0}</span></div>
        <div class="detail-row"><span class="detail-label">Negative</span><span class="detail-value">${t.negative || 0}</span></div>
        <div class="detail-row"><span class="detail-label">Manual Override</span><span class="detail-value">${t.manual_override != null ? t.manual_override : '<span class="muted">none</span>'}</span></div>
    </div>`;

    // Signals
    if (Object.keys(signals).length) {
        html += `<div class="detail-section"><h3>Signals</h3>`;
        for (const [k, v] of Object.entries(signals)) {
            html += `<div class="detail-row"><span class="detail-label">${esc(k)}</span><span class="detail-value">${typeof v === 'number' ? v.toFixed ? v.toFixed(2) : v : esc(String(v))}</span></div>`;
        }
        html += `</div>`;
    }

    // Platform IDs
    if (data.platform_ids && Object.keys(data.platform_ids).length) {
        html += `<div class="detail-section"><h3>Platform IDs</h3>`;
        for (const [k, v] of Object.entries(data.platform_ids)) {
            html += `<div class="detail-row"><span class="detail-label">${esc(k)}</span><span class="detail-value">${esc(String(v))}</span></div>`;
        }
        html += `</div>`;
    }

    // Summary (editable)
    html += `<div class="detail-section">
        <h3>Summary</h3>
        <div class="inline-edit">
            <textarea id="edit-summary">${esc(data.summary || '')}</textarea>
            <button class="ctrl-btn ctrl-btn-primary ctrl-btn-sm" id="btn-save-summary">Save</button>
        </div>
    </div>`;

    // Manual override (editable)
    html += `<div class="detail-section">
        <h3>Manual Trust Override</h3>
        <div class="inline-form">
            <input type="number" id="edit-override" step="0.01" min="0" max="1" value="${t.manual_override != null ? t.manual_override : ''}" placeholder="0.0 - 1.0" class="ctrl-input ctrl-input-sm">
            <button class="ctrl-btn ctrl-btn-primary ctrl-btn-sm" id="btn-save-override">Set</button>
            <button class="ctrl-btn ctrl-btn-sm" id="btn-clear-override">Clear</button>
        </div>
    </div>`;

    // Notes
    const notes = data.notes || [];
    html += `<div class="detail-section"><h3>Notes</h3>`;
    if (notes.length) {
        notes.forEach(n => {
            html += `<div class="note-card"><div class="note-content">${esc(n.content || String(n))}</div></div>`;
        });
    } else {
        html += `<p class="empty-msg">No notes</p>`;
    }
    html += `<div class="inline-form" style="margin-top:8px">
        <input type="text" id="add-note-input" placeholder="Add a note..." class="ctrl-input" style="flex:1">
        <button class="ctrl-btn ctrl-btn-primary ctrl-btn-sm" id="btn-add-note">Add</button>
    </div></div>`;

    // Violations
    const violations = (t.violations || []);
    if (violations.length) {
        html += `<div class="detail-section"><h3>Violations</h3>`;
        violations.forEach(v => {
            html += `<div class="violation-card">${esc(v.description)} <span style="color:var(--text-secondary)">(severity: ${v.severity})</span></div>`;
        });
        html += `</div>`;
    }

    personDetailContent.innerHTML = html;
    peopleListWrap.style.display = 'none';
    personDetail.style.display = 'block';

    // Wire edit handlers
    document.getElementById('btn-save-summary').addEventListener('click', async () => {
        const summary = document.getElementById('edit-summary').value;
        const result = await fetchJSON(`/api/people/${encodeURIComponent(pid)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ summary }),
        });
        if (result.ok) alert('Summary saved');
        else alert('Failed: ' + (result.error || 'unknown'));
    });

    document.getElementById('btn-save-override').addEventListener('click', async () => {
        const val = parseFloat(document.getElementById('edit-override').value);
        if (isNaN(val)) { alert('Enter a number 0-1'); return; }
        const result = await fetchJSON(`/api/people/${encodeURIComponent(pid)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ manual_override: val }),
        });
        if (result.ok) alert('Override set');
        else alert('Failed: ' + (result.error || 'unknown'));
    });

    document.getElementById('btn-clear-override').addEventListener('click', async () => {
        if (!confirm('Clear manual trust override?')) return;
        const result = await fetchJSON(`/api/people/${encodeURIComponent(pid)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ manual_override: null }),
        });
        if (result.ok) {
            document.getElementById('edit-override').value = '';
            alert('Override cleared');
        }
    });

    document.getElementById('btn-add-note').addEventListener('click', async () => {
        const content = document.getElementById('add-note-input').value.trim();
        if (!content) return;
        const result = await fetchJSON(`/api/people/${encodeURIComponent(pid)}/notes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        if (result.ok) {
            document.getElementById('add-note-input').value = '';
            loadPersonDetail(pid);  // Refresh
        } else {
            alert('Failed: ' + (result.error || 'unknown'));
        }
    });
}

personBack.addEventListener('click', () => {
    personDetail.style.display = 'none';
    peopleListWrap.style.display = 'block';
});

// ── Memory Tab ───────────────────────────────────

const memBudget = document.getElementById('memory-budget');
const memSections = document.getElementById('memory-sections');

async function loadMemory() {
    const data = await fetchJSON('/api/memory');
    if (data.error) {
        memBudget.innerHTML = `<p class="empty-msg">${esc(data.error)}</p>`;
        return;
    }

    // Total budget bar
    const totalPct = data.budget > 0 ? Math.round(data.total_tokens / data.budget * 100) : 0;
    memBudget.innerHTML = `
        <div class="budget-header">
            <span class="budget-label">Total Budget</span>
            <span class="budget-value">${data.total_tokens} / ${data.budget} tokens (${data.budget_remaining} remaining)</span>
        </div>
        <div class="budget-bar"><div class="budget-bar-fill" style="width:${Math.min(100, totalPct)}%;background:${budgetColor(totalPct)}"></div></div>
    `;

    // Per-section
    const sections = data.sections || {};
    let html = '<div class="section-budgets">';
    for (const [name, sec] of Object.entries(sections)) {
        const pct = sec.max_tokens > 0 ? Math.round(sec.used_tokens / sec.max_tokens * 100) : 0;
        const entries = sec.entries || [];
        html += `<div class="mem-section" data-section="${esc(name)}">
            <div class="mem-section-header">
                <span class="mem-section-title">${esc(sec.label || name)}</span>
                <span class="mem-section-meta">${sec.used_tokens}/${sec.max_tokens} tok · ${entries.length} entries</span>
            </div>
            <div class="mem-section-body">
                <div class="budget-bar" style="margin-bottom:12px"><div class="budget-bar-fill" style="width:${Math.min(100, pct)}%;background:${budgetColor(pct)}"></div></div>`;

        entries.forEach(e => {
            html += `<div class="mem-entry" data-eid="${esc(e.id)}">
                <div class="mem-entry-header">
                    <span class="mem-entry-id">${esc(e.id)}</span>
                    <span class="mem-entry-importance">importance: ${e.importance}</span>
                </div>
                <div class="mem-entry-content">${esc(e.content)}</div>
                <div class="mem-entry-actions">
                    <button class="ctrl-btn ctrl-btn-sm btn-edit-entry" data-eid="${esc(e.id)}" data-section="${esc(name)}">Edit</button>
                    <button class="ctrl-btn ctrl-btn-sm ctrl-btn-danger btn-delete-entry" data-eid="${esc(e.id)}">Delete</button>
                </div>
            </div>`;
        });

        // Add entry form
        html += `<div class="add-entry-form">
            <textarea placeholder="New entry content..." class="add-content" data-section="${esc(name)}"></textarea>
            <div class="add-entry-row">
                <span class="input-hint">Importance:</span>
                <input type="number" value="5" min="1" max="10" class="ctrl-input ctrl-input-sm add-importance" data-section="${esc(name)}">
                <button class="ctrl-btn ctrl-btn-primary ctrl-btn-sm btn-add-entry" data-section="${esc(name)}">Add Entry</button>
            </div>
        </div>`;

        html += `</div></div>`;
    }
    html += '</div>';
    memSections.innerHTML = html;

    // Section toggle
    memSections.querySelectorAll('.mem-section-header').forEach(h => {
        h.addEventListener('click', () => {
            h.parentElement.classList.toggle('open');
        });
    });

    // Edit entry
    memSections.querySelectorAll('.btn-edit-entry').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const eid = btn.dataset.eid;
            const entry = btn.closest('.mem-entry');
            const contentEl = entry.querySelector('.mem-entry-content');
            const current = contentEl.textContent;

            const textarea = document.createElement('textarea');
            textarea.value = current;
            textarea.style.cssText = 'width:100%;min-height:60px;background:var(--bg);border:1px solid var(--accent);border-radius:4px;color:var(--text);padding:8px;font-size:13px;font-family:inherit';

            const saveBtn = document.createElement('button');
            saveBtn.textContent = 'Save';
            saveBtn.className = 'ctrl-btn ctrl-btn-primary ctrl-btn-sm';
            saveBtn.style.marginTop = '6px';

            contentEl.replaceWith(textarea);
            btn.replaceWith(saveBtn);

            saveBtn.addEventListener('click', async () => {
                const result = await fetchJSON(`/api/memory/entries/${encodeURIComponent(eid)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: textarea.value }),
                });
                if (result.ok) loadMemory();
                else alert('Failed: ' + (result.error || 'unknown'));
            });
        });
    });

    // Delete entry
    memSections.querySelectorAll('.btn-delete-entry').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!confirm('Delete this memory entry? This cannot be undone.')) return;
            const eid = btn.dataset.eid;
            const result = await fetchJSON(`/api/memory/entries/${encodeURIComponent(eid)}`, { method: 'DELETE' });
            if (result.ok) loadMemory();
            else alert('Failed: ' + (result.error || 'unknown'));
        });
    });

    // Add entry
    memSections.querySelectorAll('.btn-add-entry').forEach(btn => {
        btn.addEventListener('click', async () => {
            const section = btn.dataset.section;
            const form = btn.closest('.add-entry-form');
            const content = form.querySelector('.add-content').value.trim();
            const importance = parseInt(form.querySelector('.add-importance').value) || 5;
            if (!content) return;

            const result = await fetchJSON('/api/memory/entries', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ section, content, importance }),
            });
            if (result.ok) loadMemory();
            else alert('Failed: ' + (result.error || 'unknown'));
        });
    });
}

// ── Threads Tab ──────────────────────────────────

const threadsList = document.getElementById('threads-list');
const threadDetail = document.getElementById('thread-detail');
const threadDetailContent = document.getElementById('thread-detail-content');
const threadBack = document.getElementById('thread-back');
const pendingList = document.getElementById('pending-list');

async function loadThreads() {
    threadDetail.style.display = 'none';
    threadsList.style.display = 'block';
    document.getElementById('threads-pending-section').style.display = 'block';

    const data = await fetchJSON('/api/threads');
    if (data.error) {
        threadsList.innerHTML = `<p class="empty-msg">${esc(data.error)}</p>`;
        return;
    }
    if (!data.length) {
        threadsList.innerHTML = '<p class="empty-msg">No active threads</p>';
    } else {
        threadsList.innerHTML = data.map(t => {
            const stateClass = 'thread-state-' + (t.state || 'active');
            return `<div class="thread-card" data-tid="${esc(t.id)}">
                <div class="thread-card-header">
                    <span class="thread-participants">${(t.participants || []).map(p => esc(p)).join(', ') || '<span class="muted">no participants</span>'}</span>
                    <span class="thread-state ${stateClass}">${t.state}</span>
                </div>
                <div class="thread-meta">
                    <span>${t.msg_count} msgs</span>
                    <span>${t.ene_involved ? '🧠 Ene involved' : ''}</span>
                    <span>${timeAgo(t.last_activity)}</span>
                    <span style="opacity:0.5">${esc(t.channel)}</span>
                </div>
            </div>`;
        }).join('');

        threadsList.querySelectorAll('.thread-card').forEach(card => {
            card.addEventListener('click', () => loadThreadDetail(card.dataset.tid));
        });
    }

    // Pending
    const pending = await fetchJSON('/api/threads/pending');
    if (!pending.error && pending.length) {
        pendingList.innerHTML = pending.map(p => `<div class="thread-card">
            <div class="thread-participants">${esc(p.author)}</div>
            <div class="thread-meta">
                <span>"${esc(p.content)}"</span>
                <span>${timeAgo(p.timestamp)}</span>
            </div>
        </div>`).join('');
    } else {
        pendingList.innerHTML = '<p class="empty-msg">No pending messages</p>';
    }
}

async function loadThreadDetail(tid) {
    const data = await fetchJSON(`/api/threads/${encodeURIComponent(tid)}`);
    if (data.error) {
        alert('Failed: ' + data.error);
        return;
    }

    let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <div>
            <span class="thread-state thread-state-${data.state}">${data.state}</span>
            <span style="margin-left:8px;color:var(--text-secondary);font-size:12px">${esc(data.channel)}</span>
            ${data.ene_involved ? '<span style="margin-left:8px">🧠</span>' : ''}
        </div>
        ${data.state !== 'dead' ? `<button class="ctrl-btn ctrl-btn-danger ctrl-btn-sm" id="btn-archive-thread" data-tid="${esc(tid)}">Archive (DEAD)</button>` : ''}
    </div>`;

    html += `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:12px">
        Created: ${formatDate(data.created_at)} · last_shown_index: ${data.last_shown_index}
    </div>`;

    // Messages
    const msgs = data.messages || [];
    msgs.forEach(m => {
        const authorClass = m.is_ene ? 'is-ene' : '';
        html += `<div class="thread-msg">
            <span class="thread-msg-author ${authorClass}">${esc(m.author)}</span>
            <span class="thread-msg-content">${esc(m.content)}</span>
            <span class="thread-msg-ts">${timeAgo(m.timestamp)}</span>
        </div>`;
    });

    threadDetailContent.innerHTML = html;
    threadsList.style.display = 'none';
    document.getElementById('threads-pending-section').style.display = 'none';
    threadDetail.style.display = 'block';

    // Archive handler
    const archiveBtn = document.getElementById('btn-archive-thread');
    if (archiveBtn) {
        archiveBtn.addEventListener('click', async () => {
            if (!confirm('Archive this thread (mark as DEAD)?')) return;
            const result = await fetchJSON(`/api/threads/${encodeURIComponent(tid)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ state: 'dead' }),
            });
            if (result.ok) loadThreads();
            else alert('Failed: ' + (result.error || 'unknown'));
        });
    }
}

threadBack.addEventListener('click', () => {
    threadDetail.style.display = 'none';
    threadsList.style.display = 'block';
    document.getElementById('threads-pending-section').style.display = 'block';
});

// ── Sessions Tab ─────────────────────────────────

const sessionsList = document.getElementById('sessions-list');
const sessionDetail = document.getElementById('session-detail');
const sessionDetailContent = document.getElementById('session-detail-content');
const sessionBack = document.getElementById('session-back');

async function loadSessions() {
    sessionDetail.style.display = 'none';
    sessionsList.style.display = 'block';

    const data = await fetchJSON('/api/sessions');
    if (data.error) {
        sessionsList.innerHTML = `<p class="empty-msg">${esc(data.error)}</p>`;
        return;
    }
    if (!data.length) {
        sessionsList.innerHTML = '<p class="empty-msg">No active sessions</p>';
        return;
    }

    sessionsList.innerHTML = data.map(s => {
        return `<div class="session-card" data-skey="${esc(s.key || s.channel_key || s)}">
            <div class="session-key">${esc(s.key || s.channel_key || s)}</div>
            <div class="session-meta">
                ${s.message_count != null ? `<span>${s.message_count} msgs</span>` : ''}
                ${s.responded_count != null ? `<span>${s.responded_count} responded</span>` : ''}
                ${s.token_estimate != null ? `<span>~${s.token_estimate} tokens</span>` : ''}
            </div>
        </div>`;
    }).join('');

    sessionsList.querySelectorAll('.session-card').forEach(card => {
        card.addEventListener('click', () => loadSessionDetail(card.dataset.skey));
    });
}

async function loadSessionDetail(key) {
    const data = await fetchJSON(`/api/sessions/${encodeURIComponent(key)}/history`);
    if (data.error) {
        alert('Failed: ' + data.error);
        return;
    }

    let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <div>
            <span style="font-family:monospace;font-size:14px;font-weight:500">${esc(key)}</span>
            <span style="margin-left:12px;font-size:12px;color:var(--text-secondary)">~${data.token_estimate || '?'} tokens · ${data.responded_count || 0} responses</span>
        </div>
        <button class="ctrl-btn ctrl-btn-danger ctrl-btn-sm" id="btn-clear-session" data-skey="${esc(key)}">Clear Session</button>
    </div>`;

    const msgs = data.messages || [];
    if (!msgs.length) {
        html += '<p class="empty-msg">No messages in session</p>';
    } else {
        msgs.forEach(m => {
            const role = m.role || '?';
            const content = typeof m.content === 'string' ? m.content : JSON.stringify(m.content);
            html += `<div class="sess-msg">
                <span class="sess-msg-role role-${role}">${role}</span>
                <div class="sess-msg-content">${esc(content.substring(0, 500))}${content.length > 500 ? '...' : ''}</div>
            </div>`;
        });
    }

    sessionDetailContent.innerHTML = html;
    sessionsList.style.display = 'none';
    sessionDetail.style.display = 'block';

    // Clear handler
    document.getElementById('btn-clear-session').addEventListener('click', async () => {
        if (!confirm(`Clear session "${key}"?\n\nThis deletes all conversation history for this channel. This CANNOT be undone.`)) return;
        if (!confirm('Are you absolutely sure? This destroys session history.')) return;

        const result = await fetchJSON(`/api/sessions/${encodeURIComponent(key)}`, { method: 'DELETE' });
        if (result.ok) {
            alert('Session cleared');
            loadSessions();
        } else {
            alert('Failed: ' + (result.error || 'unknown'));
        }
    });
}

sessionBack.addEventListener('click', () => {
    sessionDetail.style.display = 'none';
    sessionsList.style.display = 'block';
});

// ── Security Tab ─────────────────────────────────

const mutedList = document.getElementById('muted-list');
const rateLimitList = document.getElementById('rate-limit-list');
const jailbreakList = document.getElementById('jailbreak-list');
const btnMute = document.getElementById('btn-mute');

async function loadSecurity() {
    const data = await fetchJSON('/api/security/state');
    if (data.error) {
        mutedList.innerHTML = `<p class="empty-msg">${esc(data.error)}</p>`;
        return;
    }

    // Muted users
    const muted = data.muted || [];
    if (muted.length) {
        mutedList.innerHTML = muted.map(m => `<div class="muted-item">
            <span class="muted-item-id">${esc(m.caller_id)}</span>
            <span class="muted-item-info">${Math.ceil(m.remaining_sec / 60)}m remaining</span>
            <button class="ctrl-btn ctrl-btn-sm btn-unmute" data-cid="${esc(m.caller_id)}">Unmute</button>
        </div>`).join('');

        mutedList.querySelectorAll('.btn-unmute').forEach(btn => {
            btn.addEventListener('click', async () => {
                const result = await fetchJSON(`/api/security/mute/${encodeURIComponent(btn.dataset.cid)}`, { method: 'DELETE' });
                if (result.ok) loadSecurity();
            });
        });
    } else {
        mutedList.innerHTML = '<p class="empty-msg">No muted users</p>';
    }

    // Rate limits
    const rates = data.rate_limits || [];
    if (rates.length) {
        rateLimitList.innerHTML = rates.map(r => `<div class="rate-item">
            <span class="rate-item-id">${esc(r.caller_id)}</span>
            <span>${r.count}/${r.max} in ${r.window_sec}s</span>
            <button class="ctrl-btn ctrl-btn-sm btn-clear-rate" data-cid="${esc(r.caller_id)}">Clear</button>
        </div>`).join('');

        rateLimitList.querySelectorAll('.btn-clear-rate').forEach(btn => {
            btn.addEventListener('click', async () => {
                const result = await fetchJSON(`/api/security/rate-limit/clear/${encodeURIComponent(btn.dataset.cid)}`, { method: 'POST' });
                if (result.ok) loadSecurity();
            });
        });
    } else {
        rateLimitList.innerHTML = '<p class="empty-msg">No active rate limits</p>';
    }

    // Jailbreak scores
    const jb = data.jailbreak_scores || [];
    if (jb.length) {
        jailbreakList.innerHTML = jb.map(j => `<div class="jb-item">
            <span class="jb-item-id">${esc(j.caller_id)}</span>
            <span>${j.count} flags (threshold: ${j.threshold})</span>
        </div>`).join('');
    } else {
        jailbreakList.innerHTML = '<p class="empty-msg">No jailbreak flags</p>';
    }
}

btnMute.addEventListener('click', async () => {
    const callerId = document.getElementById('mute-caller-id').value.trim();
    const duration = parseFloat(document.getElementById('mute-duration').value) || 30;
    if (!callerId) { alert('Enter a caller ID'); return; }

    if (!confirm(`Mute ${callerId} for ${duration} minutes?`)) return;

    const result = await fetchJSON('/api/security/mute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caller_id: callerId, duration_min: duration }),
    });
    if (result.ok) {
        document.getElementById('mute-caller-id').value = '';
        loadSecurity();
    } else {
        alert('Failed: ' + (result.error || 'unknown'));
    }
});

// ── Config Tab ───────────────────────────────────

const configContent = document.getElementById('config-content');

async function loadConfig() {
    const data = await fetchJSON('/api/config');
    if (data.error) {
        configContent.innerHTML = `<p class="empty-msg">${esc(data.error)}</p>`;
        return;
    }

    let html = '';

    // Agent config
    if (data.agent) {
        html += `<div class="config-group"><h3>Agent</h3>`;
        for (const [k, v] of Object.entries(data.agent)) {
            html += `<div class="config-row"><span class="config-key">${esc(k)}</span><span class="config-val">${esc(String(v))}</span></div>`;
        }
        html += `</div>`;
    }

    // Debounce
    if (data.debounce) {
        html += `<div class="config-group"><h3>Debounce</h3>`;
        for (const [k, v] of Object.entries(data.debounce)) {
            html += `<div class="config-row"><span class="config-key">${esc(k)}</span><span class="config-val">${esc(String(v))}</span></div>`;
        }
        html += `</div>`;
    }

    // Rate Limit
    if (data.rate_limit) {
        html += `<div class="config-group"><h3>Rate Limit</h3>`;
        for (const [k, v] of Object.entries(data.rate_limit)) {
            html += `<div class="config-row"><span class="config-key">${esc(k)}</span><span class="config-val">${esc(String(v))}</span></div>`;
        }
        html += `</div>`;
    }

    // Observatory
    if (data.observatory) {
        html += `<div class="config-group"><h3>Observatory</h3>`;
        for (const [k, v] of Object.entries(data.observatory)) {
            html += `<div class="config-row"><span class="config-key">${esc(k)}</span><span class="config-val">${esc(String(v))}</span></div>`;
        }
        html += `</div>`;
    }

    // Modules
    if (data.modules) {
        html += `<div class="config-group"><h3>Modules</h3>`;
        data.modules.forEach(m => {
            html += `<div class="config-row"><span class="config-val">${esc(m)}</span></div>`;
        });
        html += `</div>`;
    }

    configContent.innerHTML = html || '<p class="empty-msg">No config data</p>';
}

// ── Experiments Tab ──────────────────────────────

const experimentsList = document.getElementById('experiments-list');

async function loadExperiments() {
    const data = await fetchJSON('/api/experiments');
    if (data.error) {
        experimentsList.innerHTML = `<p class="empty-msg">${esc(data.error)}</p>`;
        return;
    }
    if (!data.length) {
        experimentsList.innerHTML = '<p class="empty-msg">No experiments</p>';
        return;
    }

    experimentsList.innerHTML = data.map(exp => {
        const status = exp.status || 'unknown';
        const statusClass = status === 'active' ? 'experiment-status active'
            : status === 'completed' ? 'experiment-status completed'
            : 'experiment-status paused';
        let html = `<div class="experiment">
            <div class="experiment-header">
                <span class="experiment-name">${esc(exp.name || exp.id)}</span>
                <span class="${statusClass}">${status}</span>
            </div>`;
        if (exp.description) html += `<div style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">${esc(exp.description)}</div>`;
        if (exp.variants) {
            exp.variants.forEach(v => {
                html += `<div class="variant-row"><span>${esc(v.name || v.id)}</span><span>${v.count || 0} obs</span></div>`;
            });
        }
        html += `</div>`;
        return html;
    }).join('');
}

// ── Init ─────────────────────────────────────────

pollBrain();
setInterval(pollBrain, 5000);
loadPeople();  // Load first tab
