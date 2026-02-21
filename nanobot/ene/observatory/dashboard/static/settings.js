/* Ene Settings — model configuration & custom LLM management */

'use strict';

const API = location.origin;

// ── State ────────────────────────────────────────────────────────────────

let settingsData = null;  // Full settings response from API

// ── Toast notifications ──────────────────────────────────────────────────

function showToast(msg, type = 'success') {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.className = `toast toast-${type}`;
    // Force reflow for re-animation
    void el.offsetWidth;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.add('hidden'), 3000);
}

// ── Escape HTML ──────────────────────────────────────────────────────────

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ── Load settings ────────────────────────────────────────────────────────

async function loadSettings() {
    const badge = document.getElementById('status-badge');
    try {
        const res = await fetch(API + '/api/settings');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        settingsData = await res.json();
        badge.textContent = 'connected';
        badge.className = 'badge connected';
        renderModelSlots();
        renderPricingTable();
        renderCustomModels();
        loadRuntimeInfo();
    } catch (e) {
        badge.textContent = 'error';
        badge.className = 'badge error';
        console.error('Failed to load settings:', e);
    }
}

// ── Render model slot dropdowns ──────────────────────────────────────────

function renderModelSlots() {
    if (!settingsData) return;

    const { models, known_models, custom_models } = settingsData;

    // Build full model list: known + custom
    const allModels = [...known_models];
    for (const cm of custom_models) {
        if (!allModels.includes(cm.id)) allModels.push(cm.id);
    }
    allModels.sort();

    // Populate each slot
    const slots = ['primary', 'consolidation', 'daemon'];
    for (const slot of slots) {
        const select = document.getElementById(`select-${slot}`);
        const current = document.getElementById(`current-${slot}`);
        const value = models[slot] || '';

        // Update current display
        if (value) {
            current.textContent = value;
        } else {
            current.textContent = slot === 'daemon' ? 'free rotation' : 'not set (uses primary)';
            current.style.opacity = '0.5';
        }

        // Build options
        select.innerHTML = '';

        // Add "not set" option for optional slots
        if (slot !== 'primary') {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = slot === 'daemon' ? '— Free rotation (default) —' : '— Use primary model —';
            select.appendChild(opt);
        }

        for (const modelId of allModels) {
            const opt = document.createElement('option');
            opt.value = modelId;
            opt.textContent = modelId;
            if (modelId === value) opt.selected = true;
            select.appendChild(opt);
        }
    }
}

// ── Render pricing table ─────────────────────────────────────────────────

function renderPricingTable() {
    if (!settingsData) return;

    const { pricing, models, custom_models } = settingsData;
    const tbody = document.getElementById('pricing-tbody');

    // Build rows sorted by model ID
    const entries = Object.entries(pricing).sort((a, b) => a[0].localeCompare(b[0]));

    const customIds = new Set(custom_models.map(m => m.id));
    const activeModels = new Set([models.primary, models.consolidation, models.daemon].filter(Boolean));

    let html = '';
    for (const [modelId, prices] of entries) {
        const isActive = activeModels.has(modelId);
        const isCustom = customIds.has(modelId);
        const isFree = prices.input === 0 && prices.output === 0;

        let statusHtml = '';
        if (isActive) statusHtml += '<span class="model-status model-status-active">Active</span> ';
        if (isCustom) statusHtml += '<span class="model-status model-status-custom">Custom</span>';

        html += `<tr>
            <td class="model-id">${esc(modelId)}</td>
            <td class="price-val ${isFree ? 'price-free' : ''}">$${prices.input.toFixed(2)}</td>
            <td class="price-val ${isFree ? 'price-free' : ''}">$${prices.output.toFixed(2)}</td>
            <td>${statusHtml || '<span class="muted">—</span>'}</td>
        </tr>`;
    }

    tbody.innerHTML = html || '<tr><td colspan="4" class="muted">No pricing data</td></tr>';
}

// ── Render custom models list ────────────────────────────────────────────

function renderCustomModels() {
    if (!settingsData) return;

    const { custom_models } = settingsData;
    const container = document.getElementById('custom-models-list');

    if (!custom_models.length) {
        container.innerHTML = '<span class="muted">No custom models yet. Use the form above to add one.</span>';
        return;
    }

    let html = '';
    for (const m of custom_models) {
        html += `<div class="custom-model-card" data-model-id="${esc(m.id)}">
            <div class="custom-model-info">
                <span class="custom-model-id">${esc(m.id)}</span>
                <span class="custom-model-meta">
                    <span>Input: $${m.input_price.toFixed(2)}/M</span>
                    <span>Output: $${m.output_price.toFixed(2)}/M</span>
                    ${m.label && m.label !== m.id ? `<span>Label: ${esc(m.label)}</span>` : ''}
                </span>
            </div>
            <div class="custom-model-actions">
                <button class="ctrl-btn ctrl-btn-danger ctrl-btn-sm btn-delete-model" data-model-id="${esc(m.id)}">Delete</button>
            </div>
        </div>`;
    }

    container.innerHTML = html;

    // Wire delete buttons
    container.querySelectorAll('.btn-delete-model').forEach(btn => {
        btn.addEventListener('click', () => deleteCustomModel(btn.dataset.modelId));
    });
}

// ── Load runtime info ────────────────────────────────────────────────────

async function loadRuntimeInfo() {
    try {
        const res = await fetch(API + '/api/config');
        if (!res.ok) return;
        const config = await res.json();

        const grid = document.getElementById('runtime-info');
        let html = '';

        // Agent info
        const agent = config.agent || {};
        const items = [
            ['Primary Model', agent.model || '—'],
            ['Consolidation', agent.consolidation_model || '(uses primary)'],
            ['Temperature', agent.temperature ?? '—'],
            ['Max Tokens', agent.max_tokens ?? '—'],
            ['Max Iterations', agent.max_iterations ?? '—'],
            ['Memory Window', agent.memory_window ?? '—'],
        ];

        // Debounce info
        const db = config.debounce || {};
        items.push(
            ['Debounce Window', `${db.window_sec ?? '—'}s`],
            ['Batch Limit', db.batch_limit ?? '—'],
            ['Queue Merge Cap', db.queue_merge_cap ?? '—'],
        );

        // Rate limit
        const rl = config.rate_limit || {};
        items.push(
            ['Rate Limit', `${rl.max_messages ?? '—'} / ${rl.window_sec ?? '—'}s`],
        );

        // Modules
        if (config.modules) {
            items.push(['Modules', config.modules.join(', ')]);
        }

        for (const [label, value] of items) {
            html += `<div class="info-card">
                <div class="info-card-label">${esc(label)}</div>
                <div class="info-card-value">${esc(String(value))}</div>
            </div>`;
        }

        grid.innerHTML = html;
    } catch (e) {
        console.error('Failed to load runtime info:', e);
    }
}

// ── Apply model to slot ──────────────────────────────────────────────────

async function applyModel(slot) {
    const select = document.getElementById(`select-${slot}`);
    const model = select.value;

    if (slot === 'primary' && !model) {
        showToast('Primary model cannot be empty', 'error');
        return;
    }

    try {
        const res = await fetch(API + '/api/settings/model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slot, model }),
        });
        const data = await res.json();

        if (data.ok) {
            showToast(`${slot} model → ${model || 'cleared'}`);
            await loadSettings();
        } else {
            showToast(data.error || 'Failed to set model', 'error');
        }
    } catch (e) {
        showToast('Network error', 'error');
    }
}

// ── Clear daemon model ───────────────────────────────────────────────────

async function clearDaemonModel() {
    try {
        const res = await fetch(API + '/api/settings/daemon-model', {
            method: 'DELETE',
        });
        const data = await res.json();

        if (data.ok) {
            showToast('Daemon reverted to free model rotation');
            await loadSettings();
        } else {
            showToast(data.error || 'Failed to clear daemon model', 'error');
        }
    } catch (e) {
        showToast('Network error', 'error');
    }
}

// ── Add custom model ─────────────────────────────────────────────────────

async function addCustomModel() {
    const modelId = document.getElementById('custom-model-id').value.trim();
    const inputPrice = parseFloat(document.getElementById('custom-input-price').value) || 1.0;
    const outputPrice = parseFloat(document.getElementById('custom-output-price').value) || 2.0;
    const label = document.getElementById('custom-label').value.trim();

    if (!modelId) {
        showToast('Model ID is required', 'error');
        return;
    }

    if (!modelId.includes('/')) {
        showToast('Model ID should be provider/model format', 'error');
        return;
    }

    try {
        const res = await fetch(API + '/api/settings/custom-models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                id: modelId,
                input_price: inputPrice,
                output_price: outputPrice,
                label: label || modelId,
            }),
        });
        const data = await res.json();

        if (data.ok) {
            showToast(`Added ${modelId}`);
            // Clear form
            document.getElementById('custom-model-id').value = '';
            document.getElementById('custom-input-price').value = '1.00';
            document.getElementById('custom-output-price').value = '2.00';
            document.getElementById('custom-label').value = '';
            await loadSettings();
        } else {
            showToast(data.error || 'Failed to add model', 'error');
        }
    } catch (e) {
        showToast('Network error', 'error');
    }
}

// ── Delete custom model ──────────────────────────────────────────────────

async function deleteCustomModel(modelId) {
    try {
        const res = await fetch(API + '/api/settings/custom-models/' + encodeURIComponent(modelId), {
            method: 'DELETE',
        });
        const data = await res.json();

        if (data.ok) {
            showToast(`Deleted ${modelId}`);
            await loadSettings();
        } else {
            showToast(data.error || 'Failed to delete model', 'error');
        }
    } catch (e) {
        showToast('Network error', 'error');
    }
}

// ── Event listeners ──────────────────────────────────────────────────────

function initEventListeners() {
    // Apply buttons
    document.querySelectorAll('.btn-apply').forEach(btn => {
        btn.addEventListener('click', () => applyModel(btn.dataset.slot));
    });

    // Clear daemon button
    const clearBtn = document.querySelector('.btn-clear-daemon');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearDaemonModel);
    }

    // Add model button
    const addBtn = document.getElementById('btn-add-model');
    if (addBtn) {
        addBtn.addEventListener('click', addCustomModel);
    }

    // Refresh button
    const refreshBtn = document.getElementById('btn-refresh');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', loadSettings);
    }

    // Enter key in model ID input triggers add
    const modelIdInput = document.getElementById('custom-model-id');
    if (modelIdInput) {
        modelIdInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') addCustomModel();
        });
    }
}

// ── Init ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    loadSettings();
});
