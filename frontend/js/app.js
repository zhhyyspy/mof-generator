/**
 * MOF M1 Generator — Main Application
 * Workflow: Upload docs → AI extract M1 → (optional) AI derive M2
 */
import { API } from './api.js';

// ============================================================
// Unified Dialog System (replaces native alert/confirm/prompt)
// ============================================================

/**
 * Show a themed dialog. Returns a Promise.
 *   opts = {
 *     type: 'confirm' | 'alert' | 'prompt' | 'warning' | 'danger' | 'info' | 'success' | 'error',
 *     title: '...',
 *     message: '...',
 *     okText: '确定', cancelText: '取消',
 *     defaultValue: '' (for prompt),
 *     danger: false (style OK button as danger)
 *   }
 *   Resolves to:
 *     - confirm: true/false
 *     - alert: undefined
 *     - prompt: string (OK) or null (cancel)
 */
function showDialog(opts) {
  return new Promise(resolve => {
    const overlay = document.getElementById('app-dialog');
    const card = overlay.querySelector('.app-dialog-card');
    const icon = document.getElementById('app-dialog-icon');
    const title = document.getElementById('app-dialog-title');
    const msg = document.getElementById('app-dialog-message');
    const inputWrap = document.getElementById('app-dialog-input-wrap');
    const input = document.getElementById('app-dialog-input');
    const okBtn = document.getElementById('app-dialog-ok');
    const cancelBtn = document.getElementById('app-dialog-cancel');

    const type = opts.type || 'confirm';
    const iconMap = {
      confirm: '❓', alert: '💡', info: '💡',
      warning: '⚠️', danger: '⚠️', error: '❌',
      success: '✓', prompt: '✏️',
    };
    icon.textContent = iconMap[type] || '💡';
    icon.className = `app-dialog-icon ${type}`;
    title.textContent = opts.title || (type === 'alert' ? '提示' : type === 'prompt' ? '请输入' : '确认');
    msg.textContent = opts.message || '';

    // Reset classes
    card.classList.toggle('alert-only', type === 'alert' || type === 'error' || type === 'success');
    okBtn.classList.toggle('danger', !!opts.danger);

    // Prompt input
    if (type === 'prompt') {
      inputWrap.style.display = '';
      input.value = opts.defaultValue || '';
      input.placeholder = opts.placeholder || '';
      setTimeout(() => input.focus(), 50);
    } else {
      inputWrap.style.display = 'none';
    }

    okBtn.textContent = opts.okText || '确定';
    cancelBtn.textContent = opts.cancelText || '取消';

    // Event handlers
    const cleanup = () => {
      overlay.classList.add('hidden');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      document.removeEventListener('keydown', onKey);
    };
    const onOk = () => {
      const result = type === 'prompt' ? input.value : (type === 'alert' || type === 'error' || type === 'success' ? undefined : true);
      cleanup();
      resolve(result);
    };
    const onCancel = () => {
      cleanup();
      resolve(type === 'prompt' ? null : false);
    };
    const onKey = e => {
      if (e.key === 'Enter' && (type === 'prompt' || document.activeElement !== input)) {
        e.preventDefault();
        onOk();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        onCancel();
      }
    };

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    document.addEventListener('keydown', onKey);

    overlay.classList.remove('hidden');
  });
}

// Shorthand wrappers matching native signatures
async function appAlert(message, title) {
  return showDialog({ type: 'alert', message, title: title || '提示' });
}
async function appConfirm(message, title) {
  return showDialog({ type: 'confirm', message, title: title || '确认' });
}
async function appPrompt(message, defaultValue, title) {
  return showDialog({ type: 'prompt', message, title: title || '请输入', defaultValue });
}

// ============================================================
// State
// ============================================================
const state = {
  documents: [],
  m1ModelId: null,
  m1Model: null,
  m2ModelId: null,
  m2Model: null,
  activeTab: 'm1',           // 'm1' or 'm2'
  selectedElement: null,      // { type, id, classId?, data, layer }
};

// ============================================================
// Init
// ============================================================
document.addEventListener('DOMContentLoaded', async () => {
  setupUpload();
  setupToolbar();
  setupTabs();
  setupEditorActions();
  setupReviewPanel();
  setupProgressMinimize();
  setupEntityDetailPage();
  setupLLMSettings();
  setupM2ScopeModal();
  EntityEdit.setup();
  await loadM3();
  await loadDocuments();
  await loadExistingModels();
  updateViewToggleVisibility();
});

// ============================================================
// Tabs: M1 / M2
// ============================================================
function setupTabs() {
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      state.activeTab = target;
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      document.querySelector(`.tab-content[data-tab="${target}"]`).classList.add('active');
      state.selectedElement = null;
      renderInspector();
      updateToolbarState();
      if (target === 'diagram') renderDiagram();
      updateViewToggleVisibility();
    });
  });

  // Global view mode toggle (层级 vs 平铺)
  document.querySelectorAll('#view-toggle-bar .view-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const mode = btn.dataset.mode;
      const layer = state.activeTab;
      if (layer !== 'm1' && layer !== 'm2') return;
      _treeViewMode[layer] = mode;
      document.querySelectorAll('#view-toggle-bar .view-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.mode === mode);
      });
      renderTree(layer);
    });
  });
}

function updateViewToggleVisibility() {
  const bar = document.getElementById('view-toggle-bar');
  if (!bar) return;
  const layer = state.activeTab;
  // Only show for M1/M2 tabs
  bar.style.display = (layer === 'm1' || layer === 'm2') ? '' : 'none';
  if (layer === 'm1' || layer === 'm2') {
    const mode = _treeViewMode[layer] || 'hierarchy';
    bar.querySelectorAll('.view-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  }
}

function activeModelId() {
  if (state.activeTab === 'm2') return state.m2ModelId;
  if (state.activeTab === 'm1') return state.m1ModelId;
  return null; // m3 and diagram are read-only
}

function activeModel() {
  if (state.activeTab === 'm2') return state.m2Model;
  if (state.activeTab === 'm1') return state.m1Model;
  return null;
}

// ============================================================
// Document Upload
// ============================================================
function setupUpload() {
  const zone = document.getElementById('upload-zone');
  const input = document.getElementById('file-input');
  const link = document.getElementById('upload-link');

  link.addEventListener('click', e => { e.preventDefault(); input.click(); });
  zone.addEventListener('click', () => input.click());
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', async e => {
    e.preventDefault(); zone.classList.remove('dragover');
    if (e.dataTransfer.files.length) await uploadFiles(e.dataTransfer.files);
  });
  input.addEventListener('change', async () => {
    if (input.files.length) await uploadFiles(input.files);
    input.value = '';
  });

  document.getElementById('btn-clear-docs').addEventListener('click', async () => {
    for (const d of state.documents) { try { await API.deleteDocument(d.id); } catch {} }
    state.documents = [];
    renderDocList();
    updateToolbarState();
  });
}

async function uploadFiles(fileList) {
  try {
    const res = await API.uploadDocuments(fileList);
    state.documents.push(...res.documents);
    renderDocList();
    updateToolbarState();
  } catch (e) { showDialog({ type: 'error', title: '上传失败', message: e.message }); }
}

async function loadDocuments() {
  try {
    const res = await API.listDocuments();
    state.documents = res.documents;
    renderDocList();
  } catch {}
}

function renderDocList() {
  const list = document.getElementById('document-list');
  list.innerHTML = '';
  for (const doc of state.documents) {
    const li = document.createElement('li');
    li.className = 'doc-item';
    const ext = doc.filename.split('.').pop().toUpperCase();
    const icons = { PDF: '\u{1F4D5}', DOCX: '\u{1F4D8}', TXT: '\u{1F4C4}', MD: '\u{1F4DD}', XLSX: '\u{1F4CA}', XLS: '\u{1F4CA}', CSV: '\u{1F4CA}' };
    li.innerHTML = `
      <span class="doc-icon">${icons[ext] || '\u{1F4C4}'}</span>
      <div class="doc-info">
        <div class="doc-name">${doc.filename}</div>
        <div class="doc-meta">${(doc.char_count / 1000).toFixed(1)}K 字符</div>
      </div>
      <button class="btn-icon doc-delete btn-danger" title="删除">&times;</button>
    `;
    li.querySelector('.doc-delete').addEventListener('click', async e => {
      e.stopPropagation();
      await API.deleteDocument(doc.id);
      state.documents = state.documents.filter(d => d.id !== doc.id);
      renderDocList();
      updateToolbarState();
    });
    list.appendChild(li);
  }
}

// ============================================================
// Toolbar
// ============================================================
async function deleteCurrentModel() {
  if (!state.m1ModelId) return;
  const model = state.m1Model;
  const label = model?.label || state.m1ModelId;
  const hasM2 = state.m2ModelId && state.allModels?.some(m => m.id === state.m2ModelId);
  const msg = hasM2
    ? `确定删除 M1 模型 "${label}" 吗？\n\n关联的 M2 元模型 (${state.m2ModelId}) 也会一并删除，且无法恢复。`
    : `确定删除 M1 模型 "${label}" 吗？\n\n此操作无法恢复。`;
  const ok = await showDialog({
    type: 'danger', title: '删除模型', message: msg,
    okText: '确定删除', cancelText: '取消', danger: true,
  });
  if (!ok) return;
  try {
    await API.deleteModel(state.m1ModelId);
    if (hasM2) { try { await API.deleteModel(state.m2ModelId); } catch {} }
    showToast(`✓ 已删除: ${label}`);
    state.m1ModelId = null; state.m1Model = null;
    state.m2ModelId = null; state.m2Model = null;
    await loadExistingModels();
    renderTree('m1'); renderTree('m2');
  } catch (e) {
    showToast('删除失败: ' + e.message, 'error');
  }
}

function setupToolbar() {
  document.getElementById('btn-extract-m1').addEventListener('click', extractM1);
  document.getElementById('btn-model-delete').addEventListener('click', deleteCurrentModel);
  document.getElementById('btn-derive-m2').addEventListener('click', deriveM2);
  document.getElementById('model-picker').addEventListener('change', async e => {
    const id = e.target.value;
    if (!id) return;
    state.m1ModelId = id;
    // Also try to load matching M2 (m2_{id}) if exists
    const matching_m2 = `m2_${id}`;
    const m2_exists = (state.allModels || []).some(m => m.id === matching_m2);
    if (m2_exists) {
      state.m2ModelId = matching_m2;
      await loadModel(matching_m2, 'm2');
    } else {
      state.m2ModelId = null;
      state.m2Model = null;
      renderTree('m2');
    }
    await loadModel(id, 'm1');
  });
  document.getElementById('btn-validate').addEventListener('click', validateModel);
  document.getElementById('btn-export').addEventListener('click', showExportModal);
  document.getElementById('btn-version').addEventListener('click', createVersion);
  document.getElementById('btn-export-confirm').addEventListener('click', doExport);
  document.getElementById('btn-export-cancel').addEventListener('click', () => {
    document.getElementById('export-modal').classList.add('hidden');
  });
  // Export mode tab switching (raw vs review package vs complete package)
  document.querySelectorAll('.export-mode-tab').forEach(tab => {
    tab.addEventListener('click', () => switchExportMode(tab.dataset.mode));
  });
  // Wire complete-package Import modal (V3.0 .mofpkg.zip)
  wireImportPackageModal();
  document.getElementById('btn-validation-close').addEventListener('click', () => {
    document.getElementById('validation-modal').classList.add('hidden');
  });
  document.getElementById('btn-validation-x').addEventListener('click', () => {
    document.getElementById('validation-modal').classList.add('hidden');
  });
  document.getElementById('validation-mode').addEventListener('change', e => {
    const pickers = document.getElementById('validation-llm-pickers');
    const content = document.getElementById('validation-content');
    if (e.target.value === 'llm') {
      pickers.classList.remove('hidden');
      content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim)">选择M1和M2后点击"开始AI校验"</div>';
    } else {
      pickers.classList.add('hidden');
      runLocalValidate();
    }
  });
  document.getElementById('btn-run-llm-validate').addEventListener('click', runLLMValidate);
  document.getElementById('btn-version-close').addEventListener('click', () => {
    document.getElementById('version-modal').classList.add('hidden');
  });
  document.getElementById('btn-version-create').addEventListener('click', doCreateVersionSnapshot);
}

function updateToolbarState() {
  const hasDocs = state.documents.length > 0;
  const hasM1 = state.m1ModelId !== null;
  const hasM2 = state.m2ModelId !== null;
  const hasActive = activeModelId() !== null;
  const hasAnyModel = hasM1 || hasM2;

  document.getElementById('btn-extract-m1').disabled = !hasDocs;
  document.getElementById('btn-derive-m2').disabled = !hasM1;
  document.getElementById('btn-validate').disabled = !hasActive;
  // 导出按钮不再依赖当前激活 tab — 审查包是跨层导出, 原始数据也可以在 modal 里选择层
  document.getElementById('btn-export').disabled = !hasAnyModel;
  document.getElementById('btn-version').disabled = !hasActive;
  const delBtn = document.getElementById('btn-model-delete');
  if (delBtn) delBtn.disabled = !hasM1;
  document.getElementById('btn-add-class').disabled = !hasActive;
  document.getElementById('btn-add-enum').disabled = !hasActive;
  document.getElementById('btn-add-assoc').disabled = !hasActive;

  // M2 tab indicator
  const m2Tab = document.querySelector('.tab[data-tab="m2"]');
  m2Tab.textContent = hasM2 ? 'M2 元模型（抽象层）' : 'M2 元模型（待推导）';

  // Status badge
  const badge = document.getElementById('model-status');
  const nameDisplay = document.getElementById('model-name-display');
  const model = activeModel();
  if (model) {
    badge.className = `badge badge-${model.status}`;
    badge.textContent = { draft: '草稿', review: '审核中', published: '已发布' }[model.status] || model.status;
    badge.classList.remove('hidden');
    const layer = state.activeTab === 'm2' ? '[M2]' : '[M1]';
    nameDisplay.textContent = `${layer} ${model.label || model.name} v${model.current_version}`;
  } else {
    badge.classList.add('hidden');
    nameDisplay.textContent = '';
  }
}

// ============================================================
// AI Extraction: Documents → M1
// ============================================================
async function extractM1() {
  const docIds = state.documents.map(d => d.id);
  if (!docIds.length) return;

  const docNames = state.documents.map(d => d.filename).join('、');
  showProgress(
    'AI提取M1模型',
    `准备分析 ${state.documents.length} 份文档 · 点击 [开始] 启动`,
    M1_STEPS
  );
  addLog('info', `输入文档: ${docNames}`);
  addLog('info', `共 ${state.documents.reduce((a, d) => a + d.char_count, 0).toLocaleString()} 字符`);

  try {
    // Create task in 'ready' state (auto_start=false) — wait for user click
    const { task_id } = await API.startM1Extraction(docIds);  // auto_start=false by default in backend
    _currentTaskId = task_id;
    _reviewTaskId = task_id;
    updateProgressControlButtons('ready');
    await pollTask(task_id, async result => {
      _currentTaskId = null;
      addLog('success', `提取完成: ${result.classes_found} 类, ${result.attributes_found} 属性, ${result.associations_found} 关联, ${result.enumerations_found} 枚举`);
      // Results zone is already live-updated; just refresh with final state + failed batches
      let taskState = null;
      try { taskState = await API.pollTask(task_id); } catch {}
      const failedBatches = taskState?.failed_batches || [];
      updateWorkbenchResults(result, failedBatches, true);
      showToast('✓ 提取完成，请在成果区勾选保存');
    }, {
      parsing_documents: [
        '正在加载全量文档内容...',
      ],
      // NEW combined phase — replaces separate discover+attribute phases
      extracting_entities: [
        '单趟提取实体类型、属性与枚举 (已合并原来的实体发现+属性提取)',
        '每份文档批只调用一次 AI，一次性拿到类、属性、数据类型、单位...',
        '后续批次会带上已发现的类+属性作为上下文，保证命名一致并避免重复',
        '跨文档的同名类会自动合并属性集',
      ],
      // Backward compat — retained for in-flight tasks from older server build
      discovering_entities: [
        '全量数据按批次发送给AI大模型...',
      ],
      extracting_attributes: [
        '逐类提取领域专属属性...',
      ],
      extracting_associations: [
        '并行分析类之间的包含与引用关系...',
        '识别组合(composition)与聚合(aggregation)模式...',
        '按文档批次并行处理，后续批次带入已发现关联名称去重',
      ],
    });
  } catch (e) {
    addLog('info', '提取失败: ' + e.message);
    setTimeout(hideProgress, 2000);
    showDialog({ type: 'error', title: 'M1提取失败', message: e.message });
  }
}

// ============================================================
// AI Derivation: M1 → M2
// ============================================================
async function deriveM2() {
  if (!state.m1ModelId) return;
  // Open scope selector first; actual M2 derivation kicks off after user confirms.
  openM2ScopeSelector();
}

// ============================================================
// M2 Scope Selector (choose which M1 classes to abstract)
// ============================================================

let _m2SelectedClassIds = new Set();
let _m2ScopeFilter = 'all';  // all | root | inherit | large
let _m2ScopeSearch = '';

function openM2ScopeSelector() {
  const model = state.m1Model;
  if (!model || !model.versions?.length) {
    appAlert('没有可用的M1模型');
    return;
  }
  const pkg = model.versions.slice(-1)[0].package;
  const classes = pkg.classes || [];
  if (classes.length < 2) {
    appAlert('当前M1中类数量不足 (< 2)，无法反推M2。M2需要分析多个类的共性才能抽象。');
    return;
  }

  // Default: pre-select all classes (user can deselect unwanted ones)
  _m2SelectedClassIds = new Set(classes.map(c => c.id));
  _m2ScopeFilter = 'all';
  _m2ScopeSearch = '';

  document.getElementById('m2-scope-source').textContent =
    `${model.label || model.name} (共 ${classes.length} 个类)`;
  document.getElementById('m2-scope-search').value = '';
  document.querySelectorAll('.m2-scope-chip').forEach(c =>
    c.classList.toggle('active', c.dataset.filter === 'all'));

  renderM2ScopeList();
  updateM2ScopeSummary();

  document.getElementById('m2-scope-modal').classList.remove('hidden');
}

function getM2ScopeClasses() {
  return state.m1Model?.versions?.slice(-1)[0]?.package?.classes || [];
}

function renderM2ScopeList() {
  const container = document.getElementById('m2-scope-list');
  const all = getM2ScopeClasses();
  const q = _m2ScopeSearch.toLowerCase().trim();

  const filtered = all.filter(c => {
    // Text filter
    if (q) {
      const text = (c.name + ' ' + (c.description || '')).toLowerCase();
      if (!text.includes(q)) return false;
    }
    // Chip filter
    if (_m2ScopeFilter === 'root' && c.parent_class_name) return false;
    if (_m2ScopeFilter === 'inherit' && !c.parent_class_name) return false;
    if (_m2ScopeFilter === 'large' && (c.attributes?.length || 0) < 5) return false;
    return true;
  });

  if (!filtered.length) {
    container.innerHTML = '<div class="m2-scope-empty">没有匹配的类，请调整筛选条件</div>';
    return;
  }

  container.innerHTML = filtered.map(c => {
    const attrs = c.attributes?.length || 0;
    const sel = _m2SelectedClassIds.has(c.id);
    const parentHtml = c.parent_class_name
      ? `<span class="meta-parent">↑ ${escapeHtml(c.parent_class_name)}</span>`
      : `<span class="meta-root">根类</span>`;
    const desc = c.description ? `<div class="m2-scope-item-desc">${escapeHtml(c.description)}</div>` : '';
    return `<div class="m2-scope-item ${sel ? 'selected' : ''}" data-cls-id="${c.id}">
      <input type="checkbox" class="m2-scope-item-chk" ${sel ? 'checked' : ''} data-cls-id="${c.id}">
      <div class="m2-scope-item-body">
        <div class="m2-scope-item-name">${escapeHtml(c.name)}</div>
        <div class="m2-scope-item-meta">
          <span class="meta-attrs">${attrs} 属性</span>
          ${parentHtml}
        </div>
        ${desc}
      </div>
    </div>`;
  }).join('');

  // Click anywhere on card toggles; checkbox click doesn't double-trigger
  container.querySelectorAll('.m2-scope-item').forEach(el => {
    el.addEventListener('click', e => {
      const id = el.dataset.clsId;
      if (_m2SelectedClassIds.has(id)) _m2SelectedClassIds.delete(id);
      else _m2SelectedClassIds.add(id);
      el.classList.toggle('selected', _m2SelectedClassIds.has(id));
      el.querySelector('.m2-scope-item-chk').checked = _m2SelectedClassIds.has(id);
      updateM2ScopeSummary();
      e.stopPropagation();
    });
  });
}

function updateM2ScopeSummary() {
  const all = getM2ScopeClasses();
  const total = all.length;
  const sel = _m2SelectedClassIds.size;
  document.getElementById('m2-scope-count').textContent = `已选 ${sel} / ${total}`;

  // Breakdown of selected
  const selected = all.filter(c => _m2SelectedClassIds.has(c.id));
  const totalAttrs = selected.reduce((s, c) => s + (c.attributes?.length || 0), 0);
  const rootCnt = selected.filter(c => !c.parent_class_name).length;
  const inheritCnt = selected.length - rootCnt;
  document.getElementById('m2-scope-breakdown').textContent =
    sel === 0 ? '—' : `${totalAttrs} 属性 · ${rootCnt} 根类 · ${inheritCnt} 继承类`;

  document.getElementById('btn-m2-scope-start').disabled = sel < 2;
  document.getElementById('btn-m2-scope-start').textContent =
    sel < 2 ? '至少选择 2 个类' : `▶ 开始推导 (${sel} 个类)`;
}

function setupM2ScopeModal() {
  const modal = document.getElementById('m2-scope-modal');
  const close = () => modal.classList.add('hidden');

  document.getElementById('btn-m2-scope-close').addEventListener('click', close);
  document.getElementById('btn-m2-scope-cancel').addEventListener('click', close);

  document.getElementById('btn-m2-scope-all').addEventListener('click', () => {
    getM2ScopeClasses().forEach(c => _m2SelectedClassIds.add(c.id));
    renderM2ScopeList();
    updateM2ScopeSummary();
  });

  document.getElementById('btn-m2-scope-none').addEventListener('click', () => {
    _m2SelectedClassIds.clear();
    renderM2ScopeList();
    updateM2ScopeSummary();
  });

  document.getElementById('btn-m2-scope-invert').addEventListener('click', () => {
    getM2ScopeClasses().forEach(c => {
      if (_m2SelectedClassIds.has(c.id)) _m2SelectedClassIds.delete(c.id);
      else _m2SelectedClassIds.add(c.id);
    });
    renderM2ScopeList();
    updateM2ScopeSummary();
  });

  document.getElementById('m2-scope-search').addEventListener('input', e => {
    _m2ScopeSearch = e.target.value;
    renderM2ScopeList();
  });

  document.querySelectorAll('.m2-scope-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.m2-scope-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      _m2ScopeFilter = chip.dataset.filter;
      renderM2ScopeList();
    });
  });

  document.getElementById('btn-m2-scope-start').addEventListener('click', async () => {
    if (_m2SelectedClassIds.size < 2) return;
    close();
    await startM2Derivation(Array.from(_m2SelectedClassIds));
  });
}

async function startM2Derivation(selectedClassIds) {
  const m1Label = state.m1Model?.label || state.m1Model?.name || 'M1模型';
  const classCount = selectedClassIds.length;
  const totalClasses = getM2ScopeClasses().length;
  const scopeNote = classCount === totalClasses
    ? `全部 ${classCount} 个类`
    : `已选 ${classCount} / ${totalClasses} 个类`;

  showProgress(
    'AI推导M2元模型',
    `准备分析 ${m1Label} 的 ${scopeNote} · 点击 [开始] 启动`,
    M2_STEPS,
    'm2'
  );
  addLog('info', `源M1模型: ${m1Label}`);
  addLog('info', `参与抽象范围: ${scopeNote}`);

  try {
    const { task_id } = await API.startM2Derivation(state.m1ModelId, selectedClassIds);
    _currentTaskId = task_id;
    _reviewTaskId = task_id;
    updateProgressControlButtons('ready');
    await pollTask(task_id, async result => {
      _currentTaskId = null;
      addLog('success',
        `M2推导完成: ${result.classes_found} 个抽象类, ${result.attributes_found} 个通用属性, ${result.m1_class_mappings?.length || 0} 条继承映射`);
      let taskState = null;
      try { taskState = await API.pollTask(task_id); } catch {}
      const failedBatches = taskState?.failed_batches || [];
      updateWorkbenchResults(result, failedBatches, true);
      showToast('✓ M2推导完成，请在成果区确认并保存');
    }, {
      // NEW 3-phase pipeline
      clustering_m1: [
        'Phase 1/3: 按业务观测维度对 M1 类分组 (单次 LLM 调用)',
        '分组锚点是业务分析场景, 不是命名/属性相似度',
        '例如: 抽水蓄能/电化学储能/常规水电 的设备台账 → 一组"设备台账"',
      ],
      synthesizing_m2: [
        'Phase 2/4: 为每组并行抽象出 1 个 M2 基类 (最多 3 路并发)',
        'M2 严格扁平单层, 差异属性下沉到 M1 子类',
        '共享率 ≥ 50% 的属性才上升到 M2',
        'M2 自关联指向自身, 保持混合子类树的完整性',
      ],
      detecting_hierarchy: [
        'Phase 2.5/4: 为每个 M2 基类探测层级结构 (并行)',
        '检测纵向包含关系 (如 设施→功能分组→设备→部件)',
        '动态发现, 每个主题的层级取值独立生成',
        '有层级的 M2 基类会自动生成 level 枚举 + parent/children 自关联',
      ],
      consolidating_m2: [
        'Phase 3/4: 跨组去重, 合并业务含义实质相同的 M2 基类',
        '合并后仍是扁平结构, 不做多级抽象',
      ],
      // Back-compat for in-flight tasks from old server
      deriving_m2: [
        '分析各M1类的共性属性...',
      ],
    });
  } catch (e) {
    addLog('info', 'M2推导失败: ' + e.message);
    setTimeout(hideProgress, 2000);
    showToast('M2推导失败: ' + e.message, 'error');
  }
}

// ============================================================
// Rich Progress Display
// ============================================================

const M1_STEPS = [
  { key: 'parsing_documents',      label: '加载全量文档' },
  // Combined extraction — replaces old discovering_entities + extracting_attributes
  { key: 'extracting_entities',    label: '提取实体/属性/枚举 (单趟扫描)' },
  { key: 'extracting_associations', label: '分析类间关联关系 (并行)' },
  { key: 'saving',                 label: '保存M1模型' },
  { key: 'completed',              label: '完成' },
];

const M2_STEPS = [
  { key: 'clustering_m1',      label: 'Phase 1: 业务观测维度聚类' },
  { key: 'synthesizing_m2',    label: 'Phase 2: 组内抽象 M2 基类 (并行)' },
  { key: 'detecting_hierarchy', label: 'Phase 2.5: 层级结构探测 (并行)' },
  { key: 'consolidating_m2',   label: 'Phase 3: 跨组去重合并' },
  { key: 'saving',             label: '保存M2模型' },
  { key: 'completed',          label: '完成' },
];

const TIPS = [
  'MOF体系将数据模型分为M3→M2→M1→M0四层，每层各有明确职责。',
  'M1模型是领域特定的——它直接反映业务文档中描述的具体设备和参数。',
  'M2元模型是通用抽象层——多个M1模型可以共享同一个M2基类。',
  '属性的数据类型严格限制为M3定义的6种：String、Float、Integer、Date、Boolean、Enum。',
  'Association类型有三种：composition（组合）、aggregation（聚合）、association（关联）。',
  '一个M2可以有多个M1：如"设备台账"（M2）下有"抽蓄机组台账""电化学储能台账"等M1。',
  '台账节点路径是AI溯源和跨电站对标的完整依据，包含M2类型+M1版本+节点层级。',
  'Multiplicity [0..*] 表示可选且不限数量，[1..1] 表示必填且唯一。',
  '关联字段是对象中心的核心创新——将跨业务关联预计算到宽表中。',
  '枚举（Enumeration）适合有限可选值的场景，如运行状态：运行/检修/停用/退役。',
];

let _progTimer = null;
let _progStart = 0;
let _progTipIdx = 0;
let _progTipInterval = null;
let _progLogs = [];
let _progStepTimes = {};

let _progActuallyStarted = false;  // false until user clicks Start (or auto-start)
let _progMode = 'm1';  // 'm1' | 'm2' — drives labels/visibility of context-specific UI

function showProgress(title, subtitle, steps, mode = 'm1') {
  _progStart = null;  // Will be set on actual start
  _progActuallyStarted = false;
  _progLogs = [];
  _progStepTimes = {};
  _progTipIdx = Math.floor(Math.random() * TIPS.length);
  _progMode = (mode === 'm2') ? 'm2' : 'm1';
  const isM2 = (_progMode === 'm2');

  document.getElementById('progress-title').textContent = title;
  document.getElementById('progress-subtitle').textContent = subtitle || '';
  document.getElementById('progress-fill').style.width = '0%';
  document.getElementById('progress-pct').textContent = '0%';
  document.getElementById('progress-message').textContent = '就绪 · 等待用户点击 [开始]';
  document.getElementById('progress-timer').textContent = '00:00';
  document.getElementById('progress-tip-text').textContent = TIPS[_progTipIdx];

  // ---- Context-specific UI (M1 vs M2) ----
  // "文档状态" section: only M1 processes documents; M2 derives from existing M1 data
  const docsWrap = document.getElementById('progress-docs-wrap');
  if (docsWrap) docsWrap.classList.toggle('hidden', isM2);

  // "枚举" stat card: M2 derivation (3-phase) does not auto-produce enumerations
  const enumCard = document.querySelector('.wb-stat-card.stat-enums');
  if (enumCard) enumCard.classList.toggle('hidden', isM2);

  // "类" label becomes "抽象基类" for M2 to match the mental model
  const classLabel = document.querySelector('.stat-classes .wb-stat-label');
  if (classLabel) classLabel.textContent = isM2 ? '抽象基类' : '类';

  // Attributes and associations still exist for both, but with different meanings
  const attrLabel = document.querySelector('.stat-attrs .wb-stat-label');
  if (attrLabel) attrLabel.textContent = isM2 ? '共性属性' : '属性';

  // Model-name label + placeholder reflect the layer being saved
  const modelNameLabel = document.querySelector('.wb-entities-section')?.previousElementSibling?.querySelector('.wb-section-title');
  // Fallback: locate by known text structure
  const modelNameInput = document.getElementById('wb-model-label');
  const modelNameSection = modelNameInput?.closest('.wb-section');
  const modelNameTitle = modelNameSection?.querySelector('.wb-section-title');
  if (modelNameTitle) modelNameTitle.textContent = isM2 ? 'M2元模型名称' : 'M1模型名称';
  if (modelNameInput) modelNameInput.placeholder = isM2
    ? '推导到内容后再命名保存...'
    : '提取到内容后再命名保存...';

  // Work-zone title — "提取过程" for M1, "推导过程" for M2
  const workTitle = document.querySelector('.wb-zone.wb-work .wb-zone-title');
  if (workTitle) workTitle.textContent = isM2 ? '📋 工作区 · 推导过程' : '📋 工作区 · 提取过程';

  // Reset right-zone stats to 0
  document.getElementById('wb-stat-classes').textContent = '0';
  document.getElementById('wb-stat-attrs').textContent = '0';
  document.getElementById('wb-stat-assocs').textContent = '0';
  document.getElementById('wb-stat-enums').textContent = '0';
  document.getElementById('wb-model-label').value = '';
  document.getElementById('review-entities').innerHTML =
    `<div class="empty-state">${isM2 ? '等待推导结果...' : '等待提取结果...'}</div>`;
  document.getElementById('wb-failed-section').classList.add('hidden');
  document.getElementById('review-notes').classList.add('hidden');

  // LIVE badge: show "就绪" (not blinking) until extraction actually begins
  const liveBadge = document.getElementById('wb-live-badge');
  if (liveBadge) {
    liveBadge.textContent = '○ 就绪';
    liveBadge.style.color = 'var(--text-dim)';
    liveBadge.style.animation = 'none';
  }

  // Clear parallel tasks and conversation stream
  document.getElementById('progress-parallel').innerHTML = '';
  document.getElementById('conv-stream').innerHTML = '<div class="prog-conv-empty">等待LLM调用...</div>';
  document.getElementById('conv-count').textContent = '0';

  // Reset document statuses to pending
  const docsEl = document.getElementById('progress-docs');
  // Will be populated by polling with actual doc list

  updateReviewConfirmBtn();

  // Render step pipeline — ALL pending initially (no step active, no step done)
  const stepsEl = document.getElementById('progress-steps');
  stepsEl.innerHTML = '';
  for (const s of (steps || [])) {
    const div = document.createElement('div');
    div.className = 'prog-step pending';  // pending, not done
    div.dataset.key = s.key;
    div.innerHTML = `
      <div class="prog-step-icon"></div>
      <span class="prog-step-label">${s.label}</span>
      <span class="prog-step-time"></span>`;
    stepsEl.appendChild(div);
  }

  // Clear log (no auto-logging until start)
  document.getElementById('progress-log').innerHTML = '<div class="prog-log-line info"><span class="prog-log-text">等待用户点击 [开始] 按钮，任务即将启动</span></div>';

  // Do NOT start timer yet — wait for actual start
  clearInterval(_progTimer);
  clearInterval(_progTipInterval);

  // Add 'ready' banner class to the workbench
  document.querySelector('.workbench').classList.add('wb-ready');

  document.getElementById('progress-overlay').classList.remove('hidden');
}

// Called when user clicks Start — NOW activate timer, logging, tips rotation
function onExtractionActuallyStarted() {
  if (_progActuallyStarted) return;
  _progActuallyStarted = true;
  _progStart = Date.now();
  _progLogs = [];

  document.getElementById('progress-timer').textContent = '00:00';
  document.getElementById('progress-message').textContent = '准备中...';

  // LIVE badge now starts blinking red
  const liveBadge = document.getElementById('wb-live-badge');
  if (liveBadge) {
    liveBadge.textContent = '● LIVE';
    liveBadge.style.color = 'var(--red)';
    liveBadge.style.animation = '';
  }

  // Clear initial "waiting" log, add actual start log
  document.getElementById('progress-log').innerHTML = '';
  addLog('success', '▶ 任务启动');
  addLog('info', '正在连接AI大模型...');

  // Start timer
  clearInterval(_progTimer);
  _progTimer = setInterval(updateTimer, 1000);

  // Rotate tips
  clearInterval(_progTipInterval);
  _progTipInterval = setInterval(() => {
    _progTipIdx = (_progTipIdx + 1) % TIPS.length;
    const tipEl = document.getElementById('progress-tip-text');
    tipEl.style.opacity = '0';
    setTimeout(() => {
      tipEl.textContent = TIPS[_progTipIdx];
      tipEl.style.opacity = '1';
    }, 300);
  }, 8000);

  // Remove ready banner
  document.querySelector('.workbench').classList.remove('wb-ready');
}

function hideProgress() {
  clearInterval(_progTimer);
  clearInterval(_progTipInterval);
  _progressMinimized = false;
  _progActuallyStarted = false;
  _progStart = null;
  document.getElementById('progress-overlay').classList.add('hidden');
  document.querySelector('.workbench').classList.remove('wb-ready');
  const badge = document.getElementById('progress-badge');
  badge.classList.add('hidden');
}

function updateTimer() {
  if (!_progStart) return;  // Not started yet
  const elapsed = Math.floor((Date.now() - _progStart) / 1000);
  const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
  const s = String(elapsed % 60).padStart(2, '0');
  document.getElementById('progress-timer').textContent = `${m}:${s}`;
}

function updateProgress(step, progress, message) {
  const pct = Math.round((progress || 0) * 100);
  document.getElementById('progress-fill').style.width = `${pct}%`;
  document.getElementById('progress-pct').textContent = `${pct}%`;
  if (_progressMinimized) {
    document.getElementById('badge-pct').textContent = `${pct}%`;
    document.getElementById('badge-step').textContent = (message || '').substring(0, 30);
  }
  document.getElementById('progress-message').textContent = message || '';

  // KEY FIX: Don't touch pipeline steps if task hasn't actually started yet.
  // When step is 'ready' or task is in ready state, all pipeline steps stay pending.
  if (!_progActuallyStarted || step === 'ready') return;

  // Known step keys (to avoid marking pipeline as done when step is an unknown value)
  const allSteps = document.querySelectorAll('#progress-steps .prog-step');
  const stepKeys = [...allSteps].map(el => el.dataset.key);
  const stepIdx = stepKeys.indexOf(step);

  // If current step is unknown (not in pipeline), leave existing states as-is
  if (stepIdx < 0 && step !== 'completed') return;

  for (let i = 0; i < allSteps.length; i++) {
    const el = allSteps[i];
    const key = el.dataset.key;
    if (key === step) {
      if (!el.classList.contains('active')) {
        el.className = 'prog-step active';
        _progStepTimes[key] = Date.now();
        addLog('step', message || el.querySelector('.prog-step-label').textContent);
      }
    } else if (i < stepIdx) {
      // Step comes before the current step — mark as done
      if (!el.classList.contains('done')) {
        el.className = 'prog-step done';
        const startTime = _progStepTimes[key];
        if (startTime) {
          const dur = ((Date.now() - startTime) / 1000).toFixed(1);
          el.querySelector('.prog-step-time').textContent = `${dur}s`;
        }
      }
    }
    // Future steps (i > stepIdx) stay in their current state
  }

  // If completed, mark all as done
  if (step === 'completed') {
    for (const el of allSteps) {
      if (!el.classList.contains('done')) {
        el.className = 'prog-step done';
        const key = el.dataset.key;
        const startTime = _progStepTimes[key];
        if (startTime) {
          const dur = ((Date.now() - startTime) / 1000).toFixed(1);
          el.querySelector('.prog-step-time').textContent = `${dur}s`;
        }
      }
    }
    addLog('success', message || '处理完成！');
  }
}

function addLog(type, text) {
  const elapsed = _progStart ? ((Date.now() - _progStart) / 1000).toFixed(1) : '0.0';
  const logEl = document.getElementById('progress-log');
  const line = document.createElement('div');
  line.className = `prog-log-line ${type}`;
  line.innerHTML = `<span class="prog-log-time">${elapsed}s</span><span class="prog-log-text">${text}</span>`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;

  // Keep max 30 lines
  while (logEl.children.length > 30) logEl.removeChild(logEl.firstChild);
}

let _pollAborted = false;  // Set true to break polling loop immediately

async function pollTask(taskId, onComplete, logMessages) {
  let lastStep = '';
  let lastLogCount = 0;
  _pollAborted = false;
  _lastConvCount = 0;
  document.getElementById('conv-stream').innerHTML = '<div class="prog-conv-empty">等待LLM调用...</div>';
  document.getElementById('conv-count').textContent = '0';

  while (true) {
    await new Promise(r => setTimeout(r, 1200));
    if (_pollAborted) return;  // Abort check after sleep

    try {
      const s = await API.pollTask(taskId);
      if (_pollAborted) return;  // Abort check after fetch

      // Sync control buttons FIRST (so downstream logic knows state)
      if (s.status === 'ready') updateProgressControlButtons('ready');
      else if (s.status === 'paused') updateProgressControlButtons('paused');
      else if (s.status === 'running') {
        updateProgressControlButtons('running');
        // If transitioning from ready → running, activate extraction UI
        if (!_progActuallyStarted) onExtractionActuallyStarted();
      }
      else if (s.status === 'completed' || s.status === 'cancelled' || s.status === 'failed') {
        updateProgressControlButtons('done');
        if (!_progActuallyStarted && s.status === 'completed') onExtractionActuallyStarted();
      }

      updateProgress(s.step, s.progress, s.message);

      if (s.documents && s.documents.length) {
        renderDocProgress(s.documents);
      }

      if (s.parallel_tasks && s.parallel_tasks.length) {
        renderParallelTasks(s.parallel_tasks);
      } else {
        document.getElementById('progress-parallel').innerHTML = '';
      }

      // Only stream server logs after extraction has actually started
      // (Before start, logs just have "等待用户点击..." which is shown elsewhere)
      const serverLogs = s.logs || [];
      if (_progActuallyStarted && serverLogs.length > lastLogCount) {
        for (let i = lastLogCount; i < serverLogs.length; i++) {
          const l = serverLogs[i];
          addLogRaw(l.type, l.text, l.time);
        }
        lastLogCount = serverLogs.length;
      } else if (!_progActuallyStarted) {
        lastLogCount = serverLogs.length;  // Skip ahead so we don't replay old logs
      }

      // Render LLM conversation stream
      if (s.llm_conversations && s.llm_conversations.length) {
        renderConversations(s.llm_conversations);
      }

      // Live update right-zone results (partial or final)
      if (s.result && s.result.package) {
        const isFinal = s.status === 'completed';
        updateWorkbenchResults(s.result, s.failed_batches || [], isFinal);
      }

      if (s.step !== lastStep) {
        const msgs = (logMessages || {})[s.step];
        if (msgs) {
          for (const m of msgs) addLog('info', m);
        }
        lastStep = s.step;
      }

      if (s.status === 'completed') { onComplete(s.result); return; }
      if (s.status === 'cancelled') {
        _currentTaskId = null;
        hideProgress();
        return;
      }
      if (s.status === 'failed') { throw new Error(s.error || 'Unknown error'); }
    } catch (e) {
      if (_pollAborted) return;
      if (e.message && e.message.includes('not found')) {
        addLog('error', '服务器重启导致任务丢失，请重新提取');
        throw new Error('任务已丢失（服务器可能重启），请重新点击提取');
      }
    }
  }
}

function renderDocProgress(docs) {
  const container = document.getElementById('progress-docs');
  if (!docs.length) { container.innerHTML = ''; return; }

  let html = '<div class="prog-docs-title">文档处理进度</div>';
  for (const d of docs) {
    html += `<div class="prog-doc-item ${d.status}">
      <span class="prog-doc-icon"></span>
      <span class="prog-doc-name">${d.filename}</span>
      ${d.char_count ? `<span class="prog-doc-chars">${(d.char_count/1000).toFixed(1)}K</span>` : ''}
    </div>`;
  }
  container.innerHTML = html;

  // Auto-scroll to show active doc
  const active = container.querySelector('.parsing');
  if (active) active.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

let _lastConvCount = 0;

function renderConversations(convs) {
  if (convs.length === _lastConvCount) return;
  _lastConvCount = convs.length;

  const realCount = convs.filter(c => c.role !== 'waiting').length;
  document.getElementById('conv-count').textContent = realCount;

  const stream = document.getElementById('conv-stream');
  stream.innerHTML = '';

  for (let idx = 0; idx < convs.length; idx++) {
    const c = convs[idx];
    const arrows = { prompt: '▶', response: '◀', waiting: '⏳' };
    const arrow = arrows[c.role] || '•';
    const timeStr = typeof c.time === 'number' ? c.time.toFixed(1) + 's' : '';
    const hasFull = c.full && c.full.length > 0;

    const entry = document.createElement('div');
    entry.className = `prog-conv-entry ${c.role}`;
    entry.innerHTML = `
      <span class="prog-conv-arrow">${arrow}</span>
      <div class="prog-conv-body">
        <div class="prog-conv-header">
          <span class="prog-conv-text">${escapeHtml(c.content)}</span>
          ${hasFull ? `<button class="prog-conv-expand" data-idx="${idx}">展开</button>` : ''}
        </div>
        <div class="prog-conv-meta">
          <span>${c.meta || ''}</span>
          <span>${timeStr}</span>
        </div>
        ${hasFull ? `<pre class="prog-conv-full hidden" id="conv-full-${idx}">${escapeHtml(c.full)}</pre>` : ''}
      </div>`;
    stream.appendChild(entry);
  }

  // Wire expand/collapse buttons
  stream.querySelectorAll('.prog-conv-expand').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const fullEl = document.getElementById(`conv-full-${btn.dataset.idx}`);
      if (fullEl) {
        const open = fullEl.classList.toggle('hidden');
        btn.textContent = open ? '展开' : '收起';
      }
    });
  });

  stream.scrollTop = stream.scrollHeight;
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderParallelTasks(tasks) {
  const container = document.getElementById('progress-parallel');
  if (!tasks.length) { container.innerHTML = ''; return; }

  const running = tasks.filter(t => t.status === 'running').length;
  const done = tasks.filter(t => t.status === 'done').length;
  let html = `<div class="prog-parallel-title">并行处理中 <span class="prog-parallel-count">${running} 运行 / ${done} 完成 / ${tasks.length} 总计</span></div>`;
  html += '<div class="prog-parallel-list">';
  for (const t of tasks) {
    html += `<div class="prog-ptask ${t.status}">
      <span class="prog-ptask-icon"></span>
      <span class="prog-ptask-name">${t.name}</span>
    </div>`;
  }
  html += '</div>';
  container.innerHTML = html;
}

function addLogRaw(type, text, timeVal) {
  const logEl = document.getElementById('progress-log');
  // Deduplicate: skip if last line has same text
  const lastLine = logEl.lastElementChild;
  if (lastLine && lastLine.querySelector('.prog-log-text')?.textContent === text) return;

  const t = typeof timeVal === 'number' ? timeVal.toFixed(1) : timeVal;
  const line = document.createElement('div');
  line.className = `prog-log-line ${type}`;
  line.innerHTML = `<span class="prog-log-time">${t}s</span><span class="prog-log-text">${text}</span>`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
  while (logEl.children.length > 50) logEl.removeChild(logEl.firstChild);
}

// ============================================================
// Model Loading
// ============================================================
async function loadExistingModels() {
  try {
    const res = await API.listModels();
    state.allModels = res.models;  // All models for picker
    // Newest-first ordering (backend already sorts by mtime desc)
    const m1s = res.models.filter(m => !m.id.startsWith('m2_'));
    const m2s = res.models.filter(m => m.id.startsWith('m2_'));
    if (m1s.length) {
      state.m1ModelId = m1s[0].id;  // Most recent M1
      // Load the M2 that matches this M1 *first* (by `m2_${m1Id}` convention),
      // so when we render the M1 tree below, state.m2Model is already populated
      // and the V3.0 metastructure panel can appear on first paint.
      const matchingM2 = `m2_${m1s[0].id}`;
      const hasMatch = m2s.some(m => m.id === matchingM2);
      if (hasMatch) {
        state.m2ModelId = matchingM2;
        await loadModel(matchingM2, 'm2');
      } else if (m2s.length) {
        // No direct match — fall back to most-recent M2 so M2 tab isn't empty
        state.m2ModelId = m2s[0].id;
        await loadModel(m2s[0].id, 'm2');
      }
      await loadModel(m1s[0].id, 'm1');
    } else if (m2s.length) {
      // No M1 at all, still show the most recent M2
      state.m2ModelId = m2s[0].id;
      await loadModel(m2s[0].id, 'm2');
    }
    renderModelPicker();
  } catch (e) { console.error('Load models failed:', e); }
}

/**
 * Publish-status switch dialog (V3.0 § 2.4).
 * Fetches allowed transitions from backend, shows a small inline modal,
 * calls setPublishStatus, then refreshes the model.
 */
async function openPublishStatusDialog(layer, modelId, currentStatus) {
  if (!modelId) { showToast('模型未保存,无法切换发布状态', 'error'); return; }
  const STATUS_META = {
    draft:      { label: '🟡 草稿',     desc: '可随意编辑。下一步: 提交评审或直接发布。' },
    review:     { label: '🟠 评审中',   desc: '组织内部评审中。可回退草稿或正式发布。' },
    published:  { label: '🟢 已发布',   desc: '冻结发布版本。只可变更为"已废弃"。' },
    deprecated: { label: '⚫ 已废弃',   desc: '模型已停用 (终态)。不能再切换。' },
  };
  let resp;
  try {
    resp = await API.getPublishStatus(modelId);
  } catch (e) {
    showToast('获取发布状态失败: ' + e.message, 'error');
    return;
  }
  const allowed = resp.allowed_transitions || [];
  // Remove any existing dialog
  const existing = document.getElementById('publish-status-dialog');
  if (existing) existing.remove();

  const ovl = document.createElement('div');
  ovl.id = 'publish-status-dialog';
  ovl.className = 'publish-dialog-overlay';
  const curMeta = STATUS_META[currentStatus] || { label: currentStatus, desc: '' };
  const btnsHtml = allowed.length
    ? allowed.map(s => {
        const m = STATUS_META[s] || { label: s, desc: '' };
        return `<button type="button" class="publish-option status-${s}" data-target="${s}">
          <span class="publish-option-label">${m.label}</span>
          <span class="publish-option-desc">${m.desc}</span>
        </button>`;
      }).join('')
    : '<div class="publish-no-options">当前状态无可用转换 (终态)</div>';

  ovl.innerHTML = `
    <div class="publish-dialog">
      <div class="publish-dialog-header">
        <span>切换发布状态</span>
        <button type="button" class="publish-dialog-close" aria-label="关闭">×</button>
      </div>
      <div class="publish-dialog-body">
        <div class="publish-current">
          <span class="publish-current-label">当前:</span>
          <span class="tree-dashboard-status status-${currentStatus}">${curMeta.label}</span>
          <div class="publish-current-desc">${curMeta.desc}</div>
        </div>
        <div class="publish-options-title">可切换为:</div>
        <div class="publish-options">${btnsHtml}</div>
        <div class="publish-dialog-hint">
          ${layer === 'm1' ? 'M1 Package 发布后将作为正式版本冻结,供 M0 实例引用。' : 'M2 元模型发布后,下游 M1 可稳定绑定。'}
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(ovl);

  const close = () => ovl.remove();
  ovl.querySelector('.publish-dialog-close').addEventListener('click', close);
  ovl.addEventListener('click', (e) => { if (e.target === ovl) close(); });

  ovl.querySelectorAll('.publish-option').forEach(btn => {
    btn.addEventListener('click', async () => {
      const target = btn.dataset.target;
      if (!window.confirm(`确认将发布状态切换为 "${(STATUS_META[target]||{}).label||target}" ?`)) return;
      btn.disabled = true; btn.textContent = '处理中...';
      try {
        await API.setPublishStatus(modelId, target, '');
        showToast(`发布状态已切换为 ${(STATUS_META[target]||{}).label||target}`, 'success');
        close();
        // Reload the model to pick up new status, then re-render
        if (typeof loadModel === 'function') {
          await loadModel(modelId, layer);
        } else {
          renderTree(layer);
        }
      } catch (e) {
        showToast('切换失败: ' + e.message, 'error');
        btn.disabled = false;
      }
    });
  });
}

/**
 * Render the V3.0 元结构 inspector panel for M2 packages.
 * Shows each structural pattern: level chain (L1→L2→L3), root, participating classes,
 * hierarchy associations. Supports in-place rename of pattern label and level names,
 * persists via PUT /models/{id} with the updated package.
 */
function renderMetaStructurePanel(modelId, pkg, patterns) {
  const panel = document.createElement('div');
  panel.className = 'meta-structure-panel';
  const classesById = {};
  (pkg.classes || []).forEach(c => { classesById[c.id] = c; });
  const assocsById = {};
  (pkg.associations || []).forEach(a => { assocsById[a.id] = a; });

  const patternCards = patterns.map(sp => {
    // Resolve level chain (ordered by hierarchy_order via sp.hierarchy_association_ids).
    const participating = (sp.participating_class_ids || []).map(id => classesById[id]).filter(Boolean);
    const rootCls = classesById[sp.root_class_id];
    const hierarchyEdges = (sp.hierarchy_association_ids || [])
      .map(id => assocsById[id]).filter(Boolean)
      .sort((a, b) => (a.hierarchy_order || 0) - (b.hierarchy_order || 0));
    const levelNames = sp.level_names && sp.level_names.length
      ? sp.level_names
      : participating.map((c, i) => c.meta_structure_level || `L${i + 1}`);

    // Walk the level chain starting from root
    const chain = [];
    if (rootCls) chain.push(rootCls);
    let cur = rootCls;
    const seen = new Set(cur ? [cur.id] : []);
    let safety = 0;
    while (cur && safety++ < 20) {
      const outEdge = hierarchyEdges.find(e => e.source?.class_ref === cur.id);
      if (!outEdge) break;
      const nxt = classesById[outEdge.target?.class_ref];
      if (!nxt || seen.has(nxt.id)) break;
      chain.push(nxt);
      seen.add(nxt.id);
      cur = nxt;
    }
    // Any participating classes not in the chain (dangling) — show them separately
    const chainIds = new Set(chain.map(c => c.id));
    const dangling = participating.filter(c => !chainIds.has(c.id));

    const chainHtml = chain.map((c, i) => {
      const levelName = levelNames[i] || `L${i + 1}`;
      const isRoot = i === 0;
      const isLeaf = i === chain.length - 1 && chain.length > 1;
      const roleCls = isRoot ? 'ms-role-root' : isLeaf ? 'ms-role-leaf' : 'ms-role-middle';
      return `
        <div class="ms-level-chip ${roleCls}" data-class-id="${c.id}" title="点击在树中定位">
          <span class="ms-level-tag">${escapeHtml(levelName)}</span>
          <span class="ms-level-cls">${escapeHtml(c.label || c.name)}</span>
          <span class="ms-level-attrs">${(c.attributes || []).length} 属性</span>
        </div>
      `;
    }).join('<span class="ms-level-sep">→</span>');

    const constraintsHtml = (sp.constraints || []).map(c => `<span class="ms-constraint-tag">${escapeHtml(c)}</span>`).join('');
    const danglingHtml = dangling.length
      ? `<div class="ms-dangling">⚠️ 未在链上的参与类: ${dangling.map(c => escapeHtml(c.label || c.name)).join(', ')}</div>`
      : '';

    return `
      <div class="ms-card" data-pattern-id="${sp.id}">
        <div class="ms-card-header">
          <span class="ms-card-icon">🏗️</span>
          <input class="ms-card-title" type="text" value="${escapeHtml(sp.label || sp.name)}" data-field="label" />
          <span class="ms-card-count">${participating.length} 个 MetaClass · ${hierarchyEdges.length} 条层级边</span>
          <button type="button" class="ms-card-save" data-action="save-pattern">💾 保存</button>
        </div>
        ${sp.description ? `<div class="ms-card-desc">${escapeHtml(sp.description)}</div>` : ''}
        <div class="ms-level-chain">${chainHtml || '<span class="ms-empty">链为空</span>'}</div>
        ${danglingHtml}
        <div class="ms-card-footer">
          <div class="ms-constraints">
            <span class="ms-footer-label">约束:</span>
            ${constraintsHtml || '<span class="ms-empty">无</span>'}
          </div>
          ${sp.recommended_assoc_type ? `<div class="ms-recommend">推荐关联类型: <code>${escapeHtml(sp.recommended_assoc_type)}</code></div>` : ''}
        </div>
      </div>
    `;
  }).join('');

  panel.innerHTML = `
    <div class="ms-panel-header">
      <span class="ms-panel-title">🏗️ V3.0 元结构 (Structural Patterns)</span>
      <span class="ms-panel-count">${patterns.length} 个元结构</span>
      <button type="button" class="ms-panel-toggle" data-action="toggle">收起 ▲</button>
    </div>
    <div class="ms-panel-body">
      <div class="ms-panel-hint">
        元结构 = 多个 MetaClass + N-1 条层级关联构成的可复用结构模板。
        可重命名元结构标签 (点击标题→修改→点击 💾 保存)。
      </div>
      <div class="ms-cards">${patternCards}</div>
    </div>
  `;

  // Toggle collapse
  const toggleBtn = panel.querySelector('[data-action="toggle"]');
  const body = panel.querySelector('.ms-panel-body');
  toggleBtn.addEventListener('click', () => {
    const collapsed = body.classList.toggle('collapsed');
    toggleBtn.textContent = collapsed ? '展开 ▼' : '收起 ▲';
  });

  // Click level chip → scroll to that class in the tree
  panel.querySelectorAll('.ms-level-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const cid = chip.dataset.classId;
      const target = document.querySelector(`#model-tree-m2 [data-id="${cid}"][data-type="class"]`);
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        target.classList.add('flash-highlight');
        setTimeout(() => target.classList.remove('flash-highlight'), 1400);
      } else {
        showToast('未在树中找到该类卡片', 'error');
      }
    });
  });

  // Save pattern label
  panel.querySelectorAll('[data-action="save-pattern"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const card = btn.closest('.ms-card');
      const patternId = card.dataset.patternId;
      const newLabel = card.querySelector('[data-field="label"]').value.trim();
      if (!newLabel) { showToast('名称不能为空', 'error'); return; }
      btn.disabled = true; btn.textContent = '保存中...';
      try {
        // Deep clone pkg, update pattern, send full package back via PUT /models/{id}
        const pkgCopy = JSON.parse(JSON.stringify(pkg));
        const sp = (pkgCopy.structural_patterns || []).find(p => p.id === patternId);
        if (sp) sp.label = newLabel;
        await API.updateModel(modelId, { package: pkgCopy });
        showToast('元结构名称已保存', 'success');
        // Reload to pick up persisted state
        if (typeof loadModel === 'function') {
          await loadModel(modelId, 'm2');
        }
      } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
        btn.disabled = false; btn.textContent = '💾 保存';
      }
    });
  });

  return panel;
}

/**
 * Build a map from M1 class id → {level, levelName, m2ClassId, m2ClassName, patternLabel}.
 * M1 class is considered "at level N" if its parent_class_name matches a MetaClass
 * that participates in any StructuralPattern. Returns empty map when M2 has no patterns.
 */
function computeM1LevelMap(m1Pkg, m2Pkg) {
  const result = {};
  if (!m2Pkg || !(m2Pkg.structural_patterns || []).length) return result;
  // Build M2 name → { level, levelName, id, patternLabel }
  const m2LevelByName = {};
  const m2ClassById = {};
  (m2Pkg.classes || []).forEach(c => { m2ClassById[c.id] = c; });
  for (const sp of m2Pkg.structural_patterns) {
    const levelNames = sp.level_names || [];
    for (const cid of (sp.participating_class_ids || [])) {
      const mc = m2ClassById[cid];
      if (!mc) continue;
      const lvl = mc.meta_structure_level || null;
      const levelName = (lvl && levelNames[lvl - 1]) || (lvl ? `L${lvl}` : '');
      m2LevelByName[mc.name] = {
        level: lvl,
        levelName,
        m2ClassId: mc.id,
        m2ClassName: mc.name,
        m2ClassLabel: mc.label || mc.name,
        patternLabel: sp.label || sp.name,
        role: mc.meta_structure_role || null,
      };
    }
  }
  for (const c of (m1Pkg.classes || [])) {
    const parent = c.parent_class_name;
    if (parent && m2LevelByName[parent]) {
      result[c.id] = m2LevelByName[parent];
    }
  }
  return result;
}

/**
 * Render the M1-side metastructure panel as a LAYERED TREE GRAPH.
 *
 * - Each column = one M2 metastructure level (L1, L2, …)
 * - Each node = one M1 class, placed in its parent-element's column
 * - Each edge = one M1 composition association (is_hierarchy=false, assoc_type=composition|aggregation)
 *     · cross-level edges: straight bezier from node-right to node-left
 *     · same-level edges:  curved bezier looping down within the same column
 * - If an M1 class has no level (parent not in metastructure), it goes to a
 *   trailing "游离" column — shown only when any exist.
 *
 * Layout algorithm (pumped-storage scale: ≤ ~40 nodes works fine):
 *   1. Build node list grouped by column (level).
 *   2. DFS from each root (nodes with no in-edges) depth-first;
 *      assign Y by `walkSubtree` pattern so children cluster under their parent.
 *   3. Nodes not reached by DFS get stacked at end of their column.
 *
 * Node dims fixed 160×56; column pitch 200px; vertical gap 14px.
 */
function renderM1MetaStructurePanel(m1Pkg, m2Pkg) {
  if (!m2Pkg || !(m2Pkg.structural_patterns || []).length) return null;
  const patterns = m2Pkg.structural_patterns;
  const m2ClassById = {};
  (m2Pkg.classes || []).forEach(c => { m2ClassById[c.id] = c; });

  // ---- 1. M2 levels ----
  const levelEntries = [];
  for (const sp of patterns) {
    const levelNames = sp.level_names || [];
    const participants = (sp.participating_class_ids || [])
      .map(id => m2ClassById[id]).filter(Boolean)
      .sort((a, b) => (a.meta_structure_level || 0) - (b.meta_structure_level || 0));
    for (const mc of participants) {
      const lvl = mc.meta_structure_level || levelEntries.length + 1;
      levelEntries.push({
        patternId: sp.id, patternLabel: sp.label || sp.name,
        level: lvl, levelName: levelNames[lvl - 1] || `L${lvl}`,
        m2Class: mc,
      });
    }
  }
  const levelByName = {};
  const m2LabelByName = {};
  for (const e of levelEntries) {
    levelByName[e.m2Class.name] = e.level;
    m2LabelByName[e.m2Class.name] = e.m2Class.label || e.m2Class.name;
  }

  // ---- 2. Build node + edge list ----
  const nodes = (m1Pkg.classes || []).map(c => ({
    id: c.id, name: c.name, label: c.label || c.name,
    description: c.description || '',
    level: levelByName[c.parent_class_name] || 0,   // 0 = floating
    metaLabel: m2LabelByName[c.parent_class_name] || '(未挂载)',
    parentClassName: c.parent_class_name || '',
    cls: c,
  }));
  const nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));

  const edges = [];
  for (const a of (m1Pkg.associations || [])) {
    if (a.is_hierarchy) continue;   // only composition-style M1 relations
    const src = nodeById[a.source?.class_ref];
    const tgt = nodeById[a.target?.class_ref];
    if (!src || !tgt) continue;
    edges.push({
      id: a.id, source: src, target: tgt,
      name: a.name, label: a.label || a.name,
    });
  }

  // ---- 3. Grouped by level ----
  const maxLevel = Math.max(0, ...levelEntries.map(e => e.level));
  const colsByLevel = {};
  for (let L = 1; L <= maxLevel; L++) colsByLevel[L] = [];
  colsByLevel[0] = [];   // floating
  for (const n of nodes) colsByLevel[n.level].push(n);

  // ---- 4. DFS-based Y assignment ----
  const NODE_W = 160, NODE_H = 56;
  const COL_GAP = 44;         // horizontal padding between columns
  const COL_PITCH = NODE_W + COL_GAP;
  const V_GAP = 14;
  const ROW_H = NODE_H + V_GAP;

  const outEdges = {};   // nodeId -> [target nodes]
  const inEdges = {};    // nodeId -> [source nodes]
  for (const n of nodes) { outEdges[n.id] = []; inEdges[n.id] = []; }
  for (const e of edges) { outEdges[e.source.id].push(e.target); inEdges[e.target.id].push(e.source); }

  const pos = {};   // nodeId -> {col, y}

  // Layered BFS with parent-Y pull.
  // Pass 1: For each column L=1..maxLevel, process all col=L nodes in an order
  //         that preserves parent-child adjacency, assigning each node
  //         y = max(cursor, average of already-placed parents' Y).
  // Pass 2: Same-column parent-child chains (e.g. L2 机组区域 → L2 抽蓄机组):
  //         handle as a second sub-pass within each column AFTER cross-level
  //         placement — insert the child immediately after its parent.
  //
  // This gives each child of a parent a Y close to the parent's Y, while
  // still preventing overlaps via `cursor`.

  function avgParentY(n, samColOnly) {
    const parents = inEdges[n.id].filter(p => samColOnly ? p.level === n.level : p.level < n.level);
    const ys = parents.map(p => pos[p.id]?.y).filter(v => v !== undefined);
    return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : -1;
  }

  // Place floating col (0) and col 1 straight top-down
  for (const col of [0, 1]) {
    let cursor = 0;
    for (const n of colsByLevel[col]) {
      pos[n.id] = { col, y: cursor };
      cursor += ROW_H;
    }
  }

  // For col 2..maxLevel, do two sub-passes:
  //   sub1: place nodes whose parents are all in earlier columns (cross-level children)
  //         ordered by parent's Y, then fall back to first-appearance.
  //   sub2: place nodes that have at least one same-column parent (nested siblings),
  //         inserting each immediately after its parent.
  for (let L = 2; L <= maxLevel; L++) {
    const list = colsByLevel[L] || [];

    // Categorize
    const crossOnly = list.filter(n => inEdges[n.id].every(p => p.level < L));
    const withSameCol = list.filter(n => inEdges[n.id].some(p => p.level === L));

    // Sub1: sort by parent Y (nodes with no parent land at top)
    crossOnly.sort((a, b) => {
      const ya = avgParentY(a, false);
      const yb = avgParentY(b, false);
      if (ya < 0 && yb < 0) return list.indexOf(a) - list.indexOf(b);
      if (ya < 0) return -1;
      if (yb < 0) return 1;
      return ya - yb;
    });
    let cursor = 0;
    for (const n of crossOnly) {
      const want = avgParentY(n, false);
      const y = Math.max(cursor, want < 0 ? 0 : want);
      pos[n.id] = { col: L, y };
      cursor = y + ROW_H;
    }

    // Sub2: insert same-col children right after their parent, shifting everyone
    //       after that parent down by ROW_H.
    for (const n of withSameCol) {
      if (pos[n.id]) continue; // safety
      const parents = inEdges[n.id].filter(p => p.level === L && pos[p.id]);
      if (!parents.length) {
        // no same-col parent placed yet — fallback to end of column
        pos[n.id] = { col: L, y: cursor };
        cursor += ROW_H;
        continue;
      }
      // Place right below the lowest of its same-col parents
      const parentY = Math.max(...parents.map(p => pos[p.id].y));
      const insertY = parentY + ROW_H;
      // Push down everyone in col L whose y >= insertY
      for (const other of list) {
        if (!pos[other.id]) continue;
        if (pos[other.id].y >= insertY) pos[other.id].y += ROW_H;
      }
      pos[n.id] = { col: L, y: insertY };
      cursor = Math.max(cursor, insertY + ROW_H);
    }
  }

  // Safety: anyone unplaced (cycles?) gets stacked at end of their column
  for (const n of nodes) {
    if (pos[n.id]) continue;
    const list = colsByLevel[n.level] || [];
    const maxY = list.reduce((m, o) => Math.max(m, (pos[o.id]?.y ?? -ROW_H) + ROW_H), 0);
    pos[n.id] = { col: n.level, y: maxY };
  }

  // ---- 5. Compute dimensions ----
  const hasFloating = (colsByLevel[0] || []).length > 0;
  const totalCols = maxLevel + (hasFloating ? 1 : 0);
  const HEADER_H = 46;
  const PAD = 20;
  let maxY = 0;
  for (const n of nodes) if (pos[n.id]) maxY = Math.max(maxY, pos[n.id].y + NODE_H);
  const canvasW = totalCols * COL_PITCH + PAD * 2 - COL_GAP;
  const canvasH = HEADER_H + maxY + PAD;

  // x() for a given col (col=0 => floating, render at end)
  function colX(col) {
    if (col === 0) return maxLevel * COL_PITCH + PAD;  // after last level
    return (col - 1) * COL_PITCH + PAD;
  }

  // ---- 6. Build SVG + node HTML ----
  const escapeAttr = s => (s == null ? '' : String(s)).replace(/"/g, '&quot;');
  const LEVEL_COLOR = { 1: 'var(--ms-l1)', 2: 'var(--ms-l2)', 3: 'var(--ms-l3)', 4: 'var(--ms-l4)', 5: 'var(--ms-l5)', 0: 'var(--ms-floating)' };

  const headerRects = [];
  for (const e of levelEntries) {
    headerRects.push(`
      <div class="ms-tree-colhead" style="left:${colX(e.level)}px; width:${NODE_W}px;" data-level="${e.level}">
        <div class="ms-tree-colhead-level" style="color:${LEVEL_COLOR[e.level]}">${escapeHtml(e.levelName)}</div>
        <div class="ms-tree-colhead-class" title="M2 MetaClass: ${escapeAttr(e.m2Class.name)}">
          ${escapeHtml(e.m2Class.label || e.m2Class.name)}
        </div>
      </div>`);
  }
  if (hasFloating) {
    headerRects.push(`
      <div class="ms-tree-colhead ms-tree-colhead-floating" style="left:${colX(0)}px; width:${NODE_W}px;">
        <div class="ms-tree-colhead-level" style="color:${LEVEL_COLOR[0]}">游离</div>
        <div class="ms-tree-colhead-class">未挂载 M2</div>
      </div>`);
  }

  const nodeCards = nodes.map(n => {
    const p = pos[n.id]; if (!p) return '';
    const x = colX(p.col);
    const y = HEADER_H + p.y;
    const lvl = n.level || 0;
    return `
      <div class="ms-tree-node" data-m1-id="${n.id}" data-level="${lvl}"
           style="left:${x}px; top:${y}px; width:${NODE_W}px; height:${NODE_H}px;"
           title="${escapeAttr(n.label + ' · ' + n.name + '\n元类: ' + n.metaLabel + (n.description ? '\n\n' + n.description.slice(0, 100) : ''))}">
        <div class="ms-tree-node-head">
          <span class="ms-tree-node-label">${escapeHtml(n.label)}</span>
          <span class="ms-tree-node-chip" style="background:${LEVEL_COLOR[lvl]}">L${lvl || '?'}</span>
        </div>
        <div class="ms-tree-node-code">${escapeHtml(n.name)}</div>
      </div>`;
  }).join('');

  // Edges: cross-level = forward bezier (right→left), same-level = vertical (bottom→top).
  const edgeSvg = edges.map(e => {
    const ps = pos[e.source.id]; const pt = pos[e.target.id];
    if (!ps || !pt) return '';
    const sameCol = ps.col === pt.col;
    // source column color for edge
    const color = LEVEL_COLOR[e.source.level] || 'var(--text-dim)';
    let path, x1, y1, x2, y2;
    if (sameCol) {
      // Top-to-bottom vertical: exit source bottom-center → enter target top-center.
      // Slight S-curve in case target isn't directly under source.
      x1 = colX(ps.col) + NODE_W / 2;
      y1 = HEADER_H + ps.y + NODE_H;       // source bottom
      x2 = colX(pt.col) + NODE_W / 2;
      y2 = HEADER_H + pt.y;                // target top
      const cpY = (y1 + y2) / 2;
      path = `M${x1},${y1} C${x1},${cpY} ${x2},${cpY} ${x2},${y2}`;
    } else {
      // Cross-level forward bezier: source right edge → target left edge.
      x1 = colX(ps.col) + NODE_W;
      y1 = HEADER_H + ps.y + NODE_H / 2;
      x2 = colX(pt.col);
      y2 = HEADER_H + pt.y + NODE_H / 2;
      const cpX = x1 + (x2 - x1) * 0.5;
      path = `M${x1},${y1} C${cpX},${y1} ${cpX},${y2} ${x2},${y2}`;
    }
    return `<path class="ms-tree-edge" d="${path}" stroke="${color}" data-edge-id="${e.id}"
             data-src-id="${e.source.id}" data-tgt-id="${e.target.id}"
             marker-end="url(#ms-arrow)">
              <title>${escapeAttr(e.label)}: ${escapeAttr(e.source.label)} → ${escapeAttr(e.target.label)}</title>
            </path>`;
  }).join('');

  // Summary
  const totalM1 = nodes.length;
  const bound = nodes.filter(n => n.level > 0).length;
  const floatingCount = nodes.length - bound;
  const coveredLevels = new Set(nodes.filter(n => n.level > 0).map(n => n.level)).size;

  const panel = document.createElement('div');
  panel.className = 'meta-structure-panel meta-structure-panel-m1';
  panel.innerHTML = `
    <div class="ms-panel-header">
      <span class="ms-panel-title">🏗️ M1 在元结构中的分布 · 组合关系树</span>
      <span class="ms-panel-count">
        ${bound}/${totalM1} 个类已挂载 · 覆盖 ${coveredLevels}/${maxLevel} 层级 · ${edges.length} 条 composition
        ${floatingCount ? ` · ⚠ ${floatingCount} 个游离` : ''}
        · 源自 <em>${escapeHtml(patterns[0].label || patterns[0].name)}</em>
      </span>
      <button type="button" class="ms-panel-toggle" data-action="toggle">收起 ▲</button>
    </div>
    <div class="ms-panel-body">
      <div class="ms-panel-hint">
        每列 = 一个 M2 元结构层级;每节点 = 一个 M1 类,右上角徽章显示层级;
        <b>箭头 = M1 compositon 关联</b>(<code>is_hierarchy=false</code>),跨列表示层级间包含,同列曲线表示同层级嵌套。点击节点闪烁定位下方卡片。
      </div>
      <div class="ms-tree-canvas" style="width:${canvasW}px; height:${canvasH}px;">
        <svg class="ms-tree-svg" width="${canvasW}" height="${canvasH}">
          <defs>
            <marker id="ms-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                    markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 Z" fill="context-stroke" />
            </marker>
          </defs>
          ${edgeSvg}
        </svg>
        ${headerRects.join('')}
        ${nodeCards}
      </div>
    </div>
  `;

  const toggleBtn = panel.querySelector('[data-action="toggle"]');
  const body = panel.querySelector('.ms-panel-body');
  toggleBtn.addEventListener('click', () => {
    const collapsed = body.classList.toggle('collapsed');
    toggleBtn.textContent = collapsed ? '展开 ▼' : '收起 ▲';
  });

  // Click node → flash corresponding M1 card
  panel.querySelectorAll('.ms-tree-node').forEach(nd => {
    nd.addEventListener('click', () => {
      const m1Id = nd.dataset.m1Id;
      const target = document.querySelector(`#model-tree-m1 [data-id="${m1Id}"][data-type="class"]`);
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        target.classList.add('flash-highlight');
        setTimeout(() => target.classList.remove('flash-highlight'), 1400);
        // Also highlight node + its edges
        panel.querySelectorAll('.ms-tree-node.active, .ms-tree-edge.active').forEach(el => el.classList.remove('active'));
        nd.classList.add('active');
        panel.querySelectorAll(`.ms-tree-edge[data-src-id="${m1Id}"], .ms-tree-edge[data-tgt-id="${m1Id}"]`)
          .forEach(edge => edge.classList.add('active'));
      } else {
        showToast('M1 类卡片未在当前视图', 'error');
      }
    });
  });

  return panel;
}

function showToast(msg, type = 'success') {
  let toast = document.getElementById('toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'toast';
    toast.className = 'toast';
    document.body.appendChild(toast);
  }
  toast.className = `toast toast-${type} visible`;
  toast.textContent = msg;
  setTimeout(() => toast.classList.remove('visible'), 3500);
}

function renderModelPicker() {
  const sel = document.getElementById('model-picker');
  const badge = document.getElementById('model-count-badge');
  if (!sel) return;
  const m1s = (state.allModels || []).filter(m => !m.id.startsWith('m2_'));

  if (badge) {
    if (m1s.length) {
      badge.textContent = m1s.length;
      badge.classList.remove('hidden');
      badge.title = `共 ${m1s.length} 个平行的M1模型`;
    } else {
      badge.classList.add('hidden');
    }
  }

  if (!m1s.length) { sel.innerHTML = '<option value="">(无模型)</option>'; return; }
  sel.innerHTML = '';
  for (const m of m1s) {
    const opt = document.createElement('option');
    opt.value = m.id;
    const date = m.mtime ? new Date(m.mtime * 1000).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : '';
    opt.textContent = `${m.label || m.name} [${m.id}] · ${date}`;
    if (m.id === state.m1ModelId) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function loadModel(modelId, layer) {
  try {
    const model = await API.getModel(modelId);
    if (layer === 'm2') {
      state.m2Model = model;
      state.m2ModelId = modelId;
      renderTree('m2');
      // If an M1 tree is already rendered, re-render it so the V3.0
      // metastructure panel (which depends on M2) appears / refreshes.
      if (state.m1Model) renderTree('m1');
    } else {
      state.m1Model = model;
      state.m1ModelId = modelId;
      renderTree('m1');
    }
    updateToolbarState();
    // If the user is currently viewing the graph tab, refresh it so new data appears
    const diagramTab = document.querySelector('#diagram-view.tab-content.active');
    if (diagramTab) renderDiagram();
  } catch (e) { console.error('Failed to load model:', e); }
}

// ============================================================
// Tree Rendering (shared for M1 and M2)
// ============================================================
const _treeViewMode = { m1: 'cards', m2: 'cards' };  // 'cards' | 'hierarchy' | 'flat'

// ============================================================
// Entity Detail Page — rich relationship view
// ============================================================

const _detailHistory = [];

function openEntityDetailPage(cls, allClasses, allEnums, allAssocs, layer, opts = {}) {
  // Stack-based navigation: push to history if not returning
  if (!opts.fromBack) {
    _detailHistory.push({ clsId: cls.id, layer });
  }

  const layerLabel = layer === 'm2' ? 'M2 元模型' : 'M1 领域层';
  document.getElementById('ed-layer').textContent = layerLabel;
  document.getElementById('ed-current-name').textContent = `${cls.name} ${cls.label ? '· ' + cls.label : ''}`;

  // ----- Cross-layer class index (critical for M2↔M1 parent/child resolution) -----
  // M2 classes' children live in the M1 package (M1 inherits from M2 via parent_class_name).
  // M1 classes' parent may be in M1 (intra-M1 inheritance) OR in M2 (cross-layer).
  // Build a single cross-layer index with layer info so we can resolve in either direction.
  const m1Pkg = state.m1Model?.versions?.slice(-1)[0]?.package;
  const m2Pkg = state.m2Model?.versions?.slice(-1)[0]?.package;
  const m1Classes = m1Pkg?.classes || [];
  const m2Classes = m2Pkg?.classes || [];

  // Prefer same-layer (passed `allClasses`) for name lookup, fall back cross-layer
  const byName = {}; for (const c of allClasses) byName[c.name] = c;
  const crossByName = {};  // name -> { cls, layer }
  for (const c of m1Classes) if (!crossByName[c.name]) crossByName[c.name] = { cls: c, layer: 'm1' };
  for (const c of m2Classes) if (!crossByName[c.name]) crossByName[c.name] = { cls: c, layer: 'm2' };

  const enumById = {}; for (const en of allEnums) enumById[en.id] = en;

  // Parent class (cross-layer resolve)
  let parentCls = cls.parent_class_name ? byName[cls.parent_class_name] : null;
  let parentLayer = layer;
  if (!parentCls && cls.parent_class_name) {
    const entry = crossByName[cls.parent_class_name];
    if (entry) { parentCls = entry.cls; parentLayer = entry.layer; }
  }

  // Children classes (cross-layer aware):
  //   - If viewing an M2 class: its children are M1 classes whose parent_class_name = this.name
  //   - If viewing an M1 class: its children are M1 classes same-layer (intra-M1 inheritance).
  //     Rare but possible: an M2 class whose parent is an M1 class (skip that direction).
  let childrenClasses, childrenLayer;
  if (layer === 'm2') {
    childrenClasses = m1Classes.filter(c => c.parent_class_name === cls.name);
    childrenLayer = 'm1';
  } else {
    childrenClasses = m1Classes.filter(c => c.parent_class_name === cls.name && c.id !== cls.id);
    childrenLayer = 'm1';
  }

  // Outgoing associations (this → others) — same layer only (associations are per-layer)
  const outgoing = allAssocs.filter(a => a.source?.class_name === cls.name);
  // Incoming associations (others → this)
  const incoming = allAssocs.filter(a => a.target?.class_name === cls.name);
  // Used enumerations
  const usedEnumIds = new Set();
  for (const a of (cls.attributes || [])) {
    if (a.data_type === 'Enum' && a.enum_ref) usedEnumIds.add(a.enum_ref);
  }
  const usedEnums = [...usedEnumIds].map(id => enumById[id]).filter(Boolean);

  // Split attributes
  const ownAttrs = (cls.attributes || []).filter(a => !a.is_inherited);
  const inhAttrs = (cls.attributes || []).filter(a => a.is_inherited);

  const body = document.getElementById('ed-body');
  body.innerHTML = `
    <!-- Hero section -->
    <div class="ed-hero">
      <div class="ed-hero-badge"><span class="tree-badge badge-class">C</span></div>
      <div class="ed-hero-info">
        <h1 class="ed-hero-name">${escapeHtml(cls.name)}</h1>
        <div class="ed-hero-label">${escapeHtml(cls.label || '(未命名)')}</div>
        ${cls.description ? `<p class="ed-hero-desc">${escapeHtml(cls.description)}</p>` : ''}
        ${cls.parent_class_name ? `<div class="ed-hero-extends">继承自 <a class="ed-link" data-class="${escapeHtml(cls.parent_class_name)}" data-layer="${parentLayer}">${escapeHtml(cls.parent_class_name)}${parentLayer !== layer ? ` <span class="ed-layer-tag">[${parentLayer.toUpperCase()}]</span>` : ''}</a></div>` : ''}
      </div>
      <div class="ed-hero-stats">
        <div class="ed-stat"><div class="ed-stat-num">${ownAttrs.length}</div><div class="ed-stat-label">自有属性</div></div>
        <div class="ed-stat"><div class="ed-stat-num">${inhAttrs.length}</div><div class="ed-stat-label">继承属性</div></div>
        <div class="ed-stat"><div class="ed-stat-num">${childrenClasses.length}</div><div class="ed-stat-label">${layer === 'm2' ? 'M1子类' : '子类'}</div></div>
        <div class="ed-stat"><div class="ed-stat-num">${outgoing.length + incoming.length}</div><div class="ed-stat-label">关联</div></div>
        <div class="ed-stat"><div class="ed-stat-num">${usedEnums.length}</div><div class="ed-stat-label">枚举</div></div>
      </div>
      <div class="ed-hero-actions">
        <button class="btn btn-primary btn-edit-entity" data-class-id="${cls.id}">&#9998; 编辑</button>
      </div>
    </div>

    <!-- Two-column: Attributes | Relationships -->
    <div class="ed-grid">
      <div class="ed-col-attrs">
        ${renderAttrsPanel(ownAttrs, inhAttrs, enumById, cls.id, layer)}
      </div>
      <div class="ed-col-rels">
        ${renderRelsPanel(cls, parentCls, parentLayer, childrenClasses, childrenLayer, outgoing, incoming, usedEnums, layer)}
      </div>
    </div>
  `;

  // Wire edit button
  body.querySelectorAll('.btn-edit-entity').forEach(btn => {
    btn.addEventListener('click', () => {
      EntityEdit.open(cls, layer, allClasses, allEnums, allAssocs);
    });
  });

  // Wire all entity-navigation links — respect explicit data-layer for cross-layer jumps
  body.querySelectorAll('.ed-link[data-class]').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const targetName = link.dataset.class;
      const targetLayer = link.dataset.layer || layer;

      // Resolve target in the correct package for its layer
      let targetCls = null, targetPkg = null;
      if (targetLayer === 'm2') {
        targetPkg = state.m2Model?.versions?.slice(-1)[0]?.package;
      } else {
        targetPkg = state.m1Model?.versions?.slice(-1)[0]?.package;
      }
      if (targetPkg) {
        targetCls = targetPkg.classes.find(c => c.name === targetName);
      }

      // Fallback: same-package lookup (handles cases with missing data-layer)
      if (!targetCls) {
        targetCls = byName[targetName];
        if (targetCls) {
          openEntityDetailPage(targetCls, allClasses, allEnums, allAssocs, layer);
          return;
        }
      }
      if (targetCls && targetPkg) {
        openEntityDetailPage(
          targetCls,
          targetPkg.classes,
          targetPkg.enumerations || [],
          targetPkg.associations || [],
          targetLayer,
        );
      }
    });
  });
  body.querySelectorAll('.ed-link[data-enum]').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      // Focus on enum but stay in detail page — show enum modal or highlight
      const en = enumById[link.dataset.enum];
      if (en) showDialog({
        type: 'info',
        title: `枚举: ${en.name}${en.label ? ' · ' + en.label : ''}`,
        message: (en.literals || []).map(l => `• ${l.label || l.name}${l.name !== l.label ? ` (${l.name})` : ''}`).join('\n') || '(无枚举值)',
      });
    });
  });

  // Show overlay
  document.getElementById('entity-detail-overlay').classList.remove('hidden');
}

function renderAttrsPanel(ownAttrs, inhAttrs, enumById, classId, layer) {
  const renderAttrRow = (a) => {
    const unit = a.unit ? ` <span class="ed-attr-unit">(${a.unit})</span>` : '';
    const enumName = a.enum_ref && enumById[a.enum_ref] ? enumById[a.enum_ref].name : '';
    const enumLink = enumName ? ` → <a class="ed-link" data-enum="${a.enum_ref}">${escapeHtml(enumName)}</a>` : '';
    const mult = a.multiplicity ? `[${a.multiplicity.lower}..${a.multiplicity.upper === -1 ? '*' : a.multiplicity.upper}]` : '';
    // Show default_value when present (typical case: M1 class's inherited `level` attribute
    // with the level auto-assigned by Phase 2.5 hierarchy detection).
    const defaultBadge = (a.default_value !== null && a.default_value !== undefined && a.default_value !== '')
      ? `<span class="ed-attr-default" title="默认值 (实例未指定时自动填入)">默认: ${escapeHtml(String(a.default_value))}</span>`
      : '';
    return `<div class="ed-attr-row">
      <span class="ed-attr-name">${escapeHtml(a.name)}</span>
      <span class="ed-attr-chinese">${escapeHtml(a.label || '')}</span>
      <span class="ed-attr-type">${a.data_type}${unit}${enumLink}</span>
      <span class="ed-attr-mult">${mult}</span>
      ${defaultBadge}
    </div>`;
  };
  let html = '<div class="ed-section"><div class="ed-section-title">📋 属性列表</div>';
  if (ownAttrs.length) {
    html += `<div class="ed-subsection">
      <div class="ed-subsection-title"><span class="ed-tag tag-own">自有 ${ownAttrs.length}</span></div>
      <div class="ed-attrs">${ownAttrs.map(renderAttrRow).join('')}</div>
    </div>`;
  }
  if (inhAttrs.length) {
    html += `<div class="ed-subsection">
      <div class="ed-subsection-title"><span class="ed-tag tag-inherited">继承 ${inhAttrs.length}</span></div>
      <div class="ed-attrs ed-attrs-inherited">${inhAttrs.map(renderAttrRow).join('')}</div>
    </div>`;
  }
  if (!ownAttrs.length && !inhAttrs.length) {
    html += '<div class="ed-empty">暂无属性</div>';
  }
  html += '</div>';
  return html;
}

function renderRelsPanel(cls, parentCls, parentLayer, children, childrenLayer, outgoing, incoming, usedEnums, currentLayer) {
  let html = '<div class="ed-section"><div class="ed-section-title">🌐 关联与继承关系</div>';

  const layerTag = (lyr) => lyr && lyr !== currentLayer
    ? ` <span class="ed-layer-tag">[${lyr.toUpperCase()}]</span>` : '';

  // Inheritance tree
  if (parentCls || children.length) {
    html += `<div class="ed-subsection">
      <div class="ed-subsection-title">继承关系</div>
      <div class="ed-tree-viz">`;
    if (parentCls) {
      html += `<div class="ed-tree-node ed-tree-parent">
        <span class="ed-tree-arrow">▲</span>
        <span class="ed-tree-label">父类</span>
        <a class="ed-tree-class ed-link" data-class="${escapeHtml(parentCls.name)}" data-layer="${parentLayer || currentLayer}">
          <span class="tree-badge badge-class">C</span> ${escapeHtml(parentCls.name)}${layerTag(parentLayer)}
          ${parentCls.label ? ` · ${escapeHtml(parentCls.label)}` : ''}
        </a>
      </div>`;
    }
    html += `<div class="ed-tree-node ed-tree-self">
      <span class="tree-badge badge-class">C</span>
      <strong>${escapeHtml(cls.name)}</strong>
    </div>`;
    if (children.length) {
      const childBadgeTag = childrenLayer && childrenLayer !== currentLayer
        ? ` <span class="ed-layer-tag">[${childrenLayer.toUpperCase()} 子类]</span>` : '';
      html += `<div class="ed-tree-children-wrap">
        <span class="ed-tree-arrow">▼</span>
        <span class="ed-tree-label">子类 (${children.length})${childBadgeTag}</span>
        <div class="ed-tree-children">`;
      for (const child of children) {
        html += `<a class="ed-tree-class ed-link" data-class="${escapeHtml(child.name)}" data-layer="${childrenLayer || currentLayer}">
          <span class="tree-badge badge-class">C</span> ${escapeHtml(child.name)}
          ${child.label ? ` · ${escapeHtml(child.label)}` : ''}
        </a>`;
      }
      html += `</div></div>`;
    }
    html += `</div></div>`;
  }

  // Outgoing associations
  if (outgoing.length) {
    html += `<div class="ed-subsection">
      <div class="ed-subsection-title">→ 关联出 (${outgoing.length})<span class="ed-hint">本类引用/包含其他类</span></div>
      <div class="ed-rel-list">`;
    for (const a of outgoing) {
      const mult = a.target?.multiplicity ? `[${a.target.multiplicity.lower}..${a.target.multiplicity.upper === -1 ? '*' : a.target.multiplicity.upper}]` : '';
      const typeTag = a.association_type === 'composition' ? '组合' : (a.association_type === 'aggregation' ? '聚合' : '引用');
      html += `<div class="ed-rel-row">
        <span class="ed-rel-type ed-rel-${a.association_type || 'association'}">${typeTag}</span>
        <span class="ed-rel-name">${escapeHtml(a.label || a.name)}</span>
        <span class="ed-rel-arrow">→</span>
        <a class="ed-link" data-class="${escapeHtml(a.target?.class_name || '')}">${escapeHtml(a.target?.class_name || '?')}</a>
        <span class="ed-rel-mult">${mult}</span>
      </div>`;
    }
    html += `</div></div>`;
  }

  // Incoming associations
  if (incoming.length) {
    html += `<div class="ed-subsection">
      <div class="ed-subsection-title">← 关联入 (${incoming.length})<span class="ed-hint">被其他类引用/包含</span></div>
      <div class="ed-rel-list">`;
    for (const a of incoming) {
      const mult = a.source?.multiplicity ? `[${a.source.multiplicity.lower}..${a.source.multiplicity.upper === -1 ? '*' : a.source.multiplicity.upper}]` : '';
      const typeTag = a.association_type === 'composition' ? '组合' : (a.association_type === 'aggregation' ? '聚合' : '引用');
      html += `<div class="ed-rel-row">
        <a class="ed-link" data-class="${escapeHtml(a.source?.class_name || '')}">${escapeHtml(a.source?.class_name || '?')}</a>
        <span class="ed-rel-mult">${mult}</span>
        <span class="ed-rel-arrow">→</span>
        <span class="ed-rel-type ed-rel-${a.association_type || 'association'}">${typeTag}</span>
        <span class="ed-rel-name">${escapeHtml(a.label || a.name)}</span>
      </div>`;
    }
    html += `</div></div>`;
  }

  // Used enumerations
  if (usedEnums.length) {
    html += `<div class="ed-subsection">
      <div class="ed-subsection-title">🟨 使用的枚举 (${usedEnums.length})</div>
      <div class="ed-enum-list">`;
    for (const en of usedEnums) {
      const litPreview = (en.literals || []).slice(0, 5).map(l => l.label || l.name).join(' · ');
      html += `<div class="ed-enum-row">
        <a class="ed-link" data-enum="${en.id}">
          <span class="tree-badge badge-enum">E</span>
          <strong>${escapeHtml(en.name)}</strong>
          ${en.label ? ` · ${escapeHtml(en.label)}` : ''}
        </a>
        <div class="ed-enum-values">${escapeHtml(litPreview)}${en.literals.length > 5 ? ' ...' : ''}</div>
      </div>`;
    }
    html += `</div></div>`;
  }

  if (!parentCls && !children.length && !outgoing.length && !incoming.length && !usedEnums.length) {
    html += '<div class="ed-empty">此类没有继承或关联关系（独立实体）</div>';
  }

  html += '</div>';
  return html;
}

// ============================================================
// Entity Edit Module — full CRUD with cascade + diff preview
// ============================================================
// Flow:
//   1. open(cls, layer, allClasses, allEnums, allAssocs)
//   2. User edits basic fields / attrs / outgoing assocs
//   3. Live change summary updates in sidebar
//   4. User clicks "预览变更" → diff + cascade shown
//   5. "确认保存" → compute new package → PUT /models/{id} → reload

const PRIMITIVE_TYPES = ['String', 'Integer', 'Float', 'Boolean', 'Date', 'Enum', 'Reference'];
const ASSOC_TYPES = [
  { value: 'association', label: '引用' },
  { value: 'aggregation', label: '聚合' },
  { value: 'composition', label: '组合' },
];

const EntityEdit = {
  // --- state (populated on open) ---
  state: null,

  setup() {
    document.getElementById('btn-ee-close').addEventListener('click', () => EntityEdit.close(false));
    document.getElementById('btn-ee-cancel').addEventListener('click', () => EntityEdit.cancel());
    document.getElementById('btn-ee-preview').addEventListener('click', () => EntityEdit.openPreview());

    document.getElementById('btn-ep-close').addEventListener('click', () => EntityEdit.closePreview());
    document.getElementById('btn-ep-back').addEventListener('click', () => EntityEdit.closePreview());
    document.getElementById('btn-ep-commit').addEventListener('click', () => EntityEdit.commit());
  },

  open(cls, layer, allClasses, allEnums, allAssocs) {
    const modelId = layer === 'm2' ? state.m2ModelId : state.m1ModelId;
    const model   = layer === 'm2' ? state.m2Model   : state.m1Model;
    if (!modelId || !model?.versions?.length) {
      appAlert('无法加载模型数据');
      return;
    }
    const pkg = model.versions[model.versions.length - 1].package;

    // Deep clone original class (so edits don't mutate source until commit)
    const origClass = JSON.parse(JSON.stringify(cls));
    const editingClass = JSON.parse(JSON.stringify(cls));
    // Ensure attributes + nested defaults exist
    editingClass.attributes = editingClass.attributes || [];
    for (const a of editingClass.attributes) {
      if (!a.multiplicity) a.multiplicity = { lower: 1, upper: 1 };
    }

    // Outgoing associations (source = this class)
    const origOut = (allAssocs || []).filter(a => a.source?.class_name === cls.name);
    const editingOut = JSON.parse(JSON.stringify(origOut));
    for (const a of editingOut) {
      if (!a.source?.multiplicity) a.source = { ...a.source, multiplicity: { lower: 1, upper: 1 } };
      if (!a.target?.multiplicity) a.target = { ...a.target, multiplicity: { lower: 0, upper: -1 } };
    }

    // Incoming (read-only)
    const incoming = (allAssocs || []).filter(a => a.target?.class_name === cls.name);

    // ---- Parent class candidates ----
    // M1 can inherit from other M1 OR from M2 classes (cross-layer).
    // M2 should only inherit from other M2.
    // We mark each candidate with _layer so the dropdown can display a prefix.
    const parentCandidates = [];
    parentCandidates.push(
      ...allClasses
        .filter(x => x.id !== cls.id)
        .map(x => ({ id: x.id, name: x.name, label: x.label || '', attributes: x.attributes || [], parent_class_name: x.parent_class_name || null, _layer: layer }))
    );
    if (layer === 'm1') {
      const m2Pkg = state.m2Model?.versions?.slice(-1)[0]?.package;
      for (const m2c of (m2Pkg?.classes || [])) {
        parentCandidates.push({
          id: m2c.id,
          name: m2c.name,
          label: m2c.label || '',
          attributes: m2c.attributes || [],
          parent_class_name: m2c.parent_class_name || null,
          _layer: 'm2',
        });
      }
    }

    EntityEdit.state = {
      layer,
      modelId,
      origPackage: JSON.parse(JSON.stringify(pkg)),
      origClass,
      editingClass,
      origOut,
      editingOut,
      incoming,
      allClasses,               // same-layer classes (for target dropdown)
      allEnums,
      parentCandidates,         // cross-layer classes (for parent dropdown + inheritance walk)
    };

    // Update header chrome
    const badgeEl = document.getElementById('ee-layer-badge');
    badgeEl.textContent = layer.toUpperCase();
    badgeEl.className = 'ee-badge ' + layer;
    document.getElementById('ee-subtitle').textContent = cls.name;

    EntityEdit.render();
    document.getElementById('entity-edit-overlay').classList.remove('hidden');
  },

  close(reloadDetail) {
    document.getElementById('entity-edit-overlay').classList.add('hidden');
    EntityEdit.closePreview();
    // If committed, refresh entity detail page with fresh data
    if (reloadDetail && EntityEdit.state) {
      const { layer } = EntityEdit.state;
      const model = layer === 'm2' ? state.m2Model : state.m1Model;
      const pkg = model?.versions?.slice(-1)[0]?.package;
      if (pkg) {
        // Find the (possibly renamed) class — use id
        const id = EntityEdit.state.editingClass.id;
        const cls = pkg.classes.find(c => c.id === id);
        if (cls) {
          openEntityDetailPage(cls, pkg.classes, pkg.enumerations || [], pkg.associations || [], layer, { fromBack: true });
        }
      }
    }
    EntityEdit.state = null;
  },

  async cancel() {
    if (EntityEdit.hasChanges()) {
      const ok = await showDialog({
        type: 'warning',
        title: '放弃修改?',
        message: '您有未保存的修改，确定放弃?',
        okText: '放弃', cancelText: '继续编辑', danger: true,
      });
      if (!ok) return;
    }
    EntityEdit.close(false);
  },

  // --- Dirty detection ---
  hasChanges() {
    if (!EntityEdit.state) return false;
    return JSON.stringify(EntityEdit.state.editingClass) !== JSON.stringify(EntityEdit.state.origClass)
      || JSON.stringify(EntityEdit.state.editingOut) !== JSON.stringify(EntityEdit.state.origOut);
  },

  // --- Render ---
  render() {
    const s = EntityEdit.state;
    if (!s) return;
    const c = s.editingClass;
    const otherClassNames = s.allClasses.filter(x => x.id !== c.id).map(x => x.name).sort();
    const enumsList = s.allEnums || [];

    // Parent candidates: cross-layer (M1 can inherit from M1 OR M2). Sort by layer then label.
    const parentOptions = (s.parentCandidates || [])
      .slice()
      .sort((a, b) => {
        // M2 first (more abstract, conceptually "upstream")
        if (a._layer !== b._layer) return a._layer === 'm2' ? -1 : 1;
        return (a.label || a.name).localeCompare(b.label || b.name, 'zh-Hans');
      });

    const ownAttrs = (c.attributes || []).filter(a => !a.is_inherited);
    const inhAttrs = (c.attributes || []).filter(a => a.is_inherited);

    const area = document.getElementById('ee-form-area');
    area.innerHTML = `
      <!-- Basic info -->
      <section class="ee-section">
        <div class="ee-section-header">
          <div class="ee-section-title">基本信息</div>
        </div>
        <div class="ee-basic-grid">
          <div class="ee-field">
            <label>名称 (English) <span class="ee-section-hint">改名会自动级联</span></label>
            <input type="text" data-field="name" value="${escapeAttr(c.name || '')}" placeholder="PascalCase, e.g. PumpedStorageUnit" />
          </div>
          <div class="ee-field">
            <label>标签 (中文)</label>
            <input type="text" data-field="label" value="${escapeAttr(c.label || '')}" placeholder="中文名称" />
          </div>
          <div class="ee-field full-width">
            <label>描述</label>
            <textarea data-field="description" placeholder="类的业务含义...">${escapeHtml(c.description || '')}</textarea>
          </div>
          <div class="ee-field">
            <label>父类 <span class="ee-section-hint">改父类会自动更新继承属性</span></label>
            <select data-field="parent_class_name">
              <option value="">(无父类)</option>
              ${parentOptions.map(p => {
                const prefix = p._layer === 'm2' ? '[M2] ' : '[M1] ';
                const display = p.label && p.label !== p.name
                  ? `${prefix}${escapeHtml(p.label)} (${escapeHtml(p.name)})`
                  : `${prefix}${escapeHtml(p.name)}`;
                return `<option value="${escapeAttr(p.name)}" ${c.parent_class_name === p.name ? 'selected' : ''}>${display}</option>`;
              }).join('')}
            </select>
          </div>
          <div class="ee-field-check">
            <input type="checkbox" id="ee-is-abstract" data-field="is_abstract" ${c.is_abstract ? 'checked' : ''}>
            <label for="ee-is-abstract">抽象类 (is_abstract)</label>
          </div>
        </div>
      </section>

      <!-- Attributes (editable) -->
      <section class="ee-section">
        <div class="ee-section-header">
          <div class="ee-section-title">属性
            <span class="ee-section-count">${ownAttrs.length} 自有 · ${inhAttrs.length} 继承</span>
          </div>
        </div>
        ${EntityEdit._renderAttrTable(ownAttrs, enumsList, false)}
        ${inhAttrs.length ? `
          <div class="ee-section-hint" style="margin-top:12px">继承属性 (只读 — 需到父类修改):</div>
          ${EntityEdit._renderAttrTable(inhAttrs, enumsList, true)}
        ` : ''}
        <button class="ee-add-row" id="ee-add-attr">+ 添加属性</button>
      </section>

      <!-- Outgoing associations -->
      <section class="ee-section">
        <div class="ee-section-header">
          <div class="ee-section-title">出向关联 (本类 → 其他类)
            <span class="ee-section-count">${s.editingOut.length}</span>
          </div>
        </div>
        ${EntityEdit._renderAssocTable(s.editingOut, otherClassNames)}
        <button class="ee-add-row" id="ee-add-assoc">+ 添加关联</button>
      </section>

      <!-- Incoming associations (read-only) -->
      ${s.incoming.length ? `
      <section class="ee-section">
        <div class="ee-section-header">
          <div class="ee-section-title">入向关联 (其他类 → 本类)
            <span class="ee-section-count">${s.incoming.length} 只读</span>
          </div>
        </div>
        <div class="ee-section-hint">以下关联由其他类定义。要修改请点源类名跳转过去编辑。</div>
        <div class="ee-incoming-list">
          ${s.incoming.map(a => `
            <div class="ee-incoming-item">
              <span class="ee-inc-source" data-goto="${escapeAttr(a.source?.class_name || '')}">${escapeHtml(a.source?.class_name || '?')}</span>
              <span class="ep-arrow">→</span>
              <span class="ee-inc-name">${escapeHtml(a.label || a.name)}</span>
              <span class="ee-inc-type">${EntityEdit._assocTypeLabel(a.association_type)}</span>
            </div>
          `).join('')}
        </div>
      </section>` : ''}
    `;

    // Wire basic field changes
    area.querySelectorAll('[data-field]').forEach(el => {
      const commit = () => {
        const field = el.dataset.field;
        if (field === 'is_abstract') c[field] = el.checked;
        else c[field] = el.value;
        // Parent change has cascading effect on this class's inherited attrs —
        // rebuild them from the new parent chain, then re-render so the
        // "继承属性" read-only table reflects the new state.
        if (field === 'parent_class_name') {
          EntityEdit._rebuildInheritedAttrs();
          EntityEdit.render();
          return;  // render() calls _updateSummary already
        }
        EntityEdit._updateSummary();
      };
      el.addEventListener('input', commit);
      el.addEventListener('change', commit);
    });

    // Wire attribute rows
    EntityEdit._wireAttrRows(area);
    // Wire assoc rows
    EntityEdit._wireAssocRows(area);

    // Add buttons
    document.getElementById('ee-add-attr').addEventListener('click', () => {
      c.attributes = c.attributes || [];
      c.attributes.push({
        id: 'new_' + Math.random().toString(36).slice(2, 10),
        name: 'newAttribute', label: '新属性',
        data_type: 'String',
        multiplicity: { lower: 1, upper: 1 },
        _isNew: true,
      });
      EntityEdit.render();
      EntityEdit._updateSummary();
    });
    document.getElementById('ee-add-assoc').addEventListener('click', () => {
      s.editingOut.push({
        id: 'new_' + Math.random().toString(36).slice(2, 10),
        name: 'newAssoc', label: '新关联',
        association_type: 'association',
        source: {
          class_ref: c.id, class_name: c.name,
          role_name: '', multiplicity: { lower: 1, upper: 1 },
        },
        target: {
          class_ref: '', class_name: otherClassNames[0] || '',
          role_name: '', multiplicity: { lower: 0, upper: -1 },
        },
        _isNew: true,
      });
      EntityEdit.render();
      EntityEdit._updateSummary();
    });

    // Wire incoming "goto" links
    area.querySelectorAll('[data-goto]').forEach(el => {
      el.addEventListener('click', () => {
        const targetName = el.dataset.goto;
        const targetCls = s.allClasses.find(x => x.name === targetName);
        if (!targetCls) return;
        EntityEdit.close(false);
        openEntityDetailPage(targetCls, s.allClasses, s.allEnums, s.origPackage.associations || [], s.layer);
      });
    });

    EntityEdit._updateSummary();
  },

  _renderAttrTable(attrs, enumsList, readonly) {
    if (!attrs.length) return '<div class="ee-empty-table">暂无属性</div>';
    const enumOptions = (current) => {
      const opts = ['<option value="">(无)</option>'];
      for (const en of enumsList) {
        opts.push(`<option value="${escapeAttr(en.id)}" ${en.id === current ? 'selected' : ''}>${escapeHtml(en.name)}</option>`);
      }
      return opts.join('');
    };
    const typeOptions = (current) => PRIMITIVE_TYPES.map(t =>
      `<option value="${t}" ${t === current ? 'selected' : ''}>${t}</option>`
    ).join('');

    return `<table class="ee-table">
      <thead><tr>
        <th style="width:18%">名称</th>
        <th style="width:18%">标签</th>
        <th style="width:13%">类型</th>
        <th style="width:10%">单位</th>
        <th style="width:16%">枚举</th>
        <th style="width:14%">多重性</th>
        ${readonly ? '' : '<th style="width:5%"></th>'}
      </tr></thead>
      <tbody>
        ${attrs.map(a => {
          const rowCls = a._isNew ? 'ee-row-new' : (readonly ? 'ee-row-inherited' : '');
          const ro = readonly ? 'readonly' : '';
          return `<tr class="${rowCls}" data-attr-id="${escapeAttr(a.id)}">
            <td><input class="ee-cell-input mono" data-attr-field="name" value="${escapeAttr(a.name || '')}" ${ro}></td>
            <td><input class="ee-cell-input" data-attr-field="label" value="${escapeAttr(a.label || '')}" ${ro}></td>
            <td>
              <select class="ee-cell-select" data-attr-field="data_type" ${ro ? 'disabled' : ''}>${typeOptions(a.data_type || 'String')}</select>
            </td>
            <td><input class="ee-cell-input" data-attr-field="unit" value="${escapeAttr(a.unit || '')}" ${ro} placeholder="MW"></td>
            <td>
              <select class="ee-cell-select" data-attr-field="enum_ref" ${ro ? 'disabled' : ''}>${enumOptions(a.enum_ref || '')}</select>
            </td>
            <td>
              <span class="ee-mult-pair">
                <input type="number" data-attr-field="mult_lower" value="${a.multiplicity?.lower ?? 1}" ${ro} min="0" title="lower">
                <span>..</span>
                <input type="number" data-attr-field="mult_upper" value="${a.multiplicity?.upper ?? 1}" ${ro} min="-1" title="upper (-1 = *)">
              </span>
            </td>
            ${readonly ? '' : `<td><button class="ee-row-delete" data-del-attr="${escapeAttr(a.id)}" title="删除">&times;</button></td>`}
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
  },

  _renderAssocTable(assocs, otherClassNames) {
    if (!assocs.length) return '<div class="ee-empty-table">暂无出向关联</div>';
    const typeOptions = (current) => ASSOC_TYPES.map(t =>
      `<option value="${t.value}" ${t.value === current ? 'selected' : ''}>${t.label}</option>`
    ).join('');
    const classOptions = (current) => otherClassNames.map(n =>
      `<option value="${escapeAttr(n)}" ${n === current ? 'selected' : ''}>${escapeHtml(n)}</option>`
    ).join('');

    return `<table class="ee-table">
      <thead><tr>
        <th style="width:18%">名称</th>
        <th style="width:22%">标签</th>
        <th style="width:12%">类型</th>
        <th style="width:22%">目标类</th>
        <th style="width:18%">目标多重性</th>
        <th style="width:5%"></th>
      </tr></thead>
      <tbody>
        ${assocs.map(a => {
          const rowCls = a._isNew ? 'ee-row-new' : '';
          return `<tr class="${rowCls}" data-assoc-id="${escapeAttr(a.id)}">
            <td><input class="ee-cell-input mono" data-assoc-field="name" value="${escapeAttr(a.name || '')}"></td>
            <td><input class="ee-cell-input" data-assoc-field="label" value="${escapeAttr(a.label || '')}"></td>
            <td><select class="ee-cell-select" data-assoc-field="association_type">${typeOptions(a.association_type || 'association')}</select></td>
            <td>
              <select class="ee-cell-select" data-assoc-field="target_class">
                ${classOptions(a.target?.class_name || '')}
              </select>
            </td>
            <td>
              <span class="ee-mult-pair">
                <input type="number" data-assoc-field="target_mult_lower" value="${a.target?.multiplicity?.lower ?? 0}" min="0" title="target lower">
                <span>..</span>
                <input type="number" data-assoc-field="target_mult_upper" value="${a.target?.multiplicity?.upper ?? -1}" min="-1" title="target upper (-1 = *)">
              </span>
            </td>
            <td><button class="ee-row-delete" data-del-assoc="${escapeAttr(a.id)}" title="删除">&times;</button></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
  },

  _wireAttrRows(area) {
    const s = EntityEdit.state;
    area.querySelectorAll('tr[data-attr-id]').forEach(tr => {
      const attrId = tr.dataset.attrId;
      const attr = s.editingClass.attributes.find(a => a.id === attrId);
      if (!attr) return;
      tr.querySelectorAll('[data-attr-field]').forEach(input => {
        input.addEventListener('input', () => {
          const f = input.dataset.attrField;
          if (f === 'mult_lower')      attr.multiplicity.lower = parseInt(input.value) || 0;
          else if (f === 'mult_upper') attr.multiplicity.upper = parseInt(input.value);
          else                          attr[f] = input.value;
          if (attr.data_type !== 'Enum') attr.enum_ref = '';
          EntityEdit._updateSummary();
        });
      });
    });
    area.querySelectorAll('[data-del-attr]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.delAttr;
        const attr = s.editingClass.attributes.find(a => a.id === id);
        if (!attr._isNew) {
          const ok = await showDialog({
            type: 'danger',
            title: '删除属性?',
            message: `确认删除属性「${attr.name}」?\n此修改需点击"预览变更"确认后才会持久化。`,
            okText: '删除', danger: true,
          });
          if (!ok) return;
        }
        s.editingClass.attributes = s.editingClass.attributes.filter(a => a.id !== id);
        EntityEdit.render();
        EntityEdit._updateSummary();
      });
    });
  },

  _wireAssocRows(area) {
    const s = EntityEdit.state;
    area.querySelectorAll('tr[data-assoc-id]').forEach(tr => {
      const aid = tr.dataset.assocId;
      const assoc = s.editingOut.find(a => a.id === aid);
      if (!assoc) return;
      tr.querySelectorAll('[data-assoc-field]').forEach(input => {
        input.addEventListener('input', () => {
          const f = input.dataset.assocField;
          if (f === 'target_class') {
            assoc.target.class_name = input.value;
            // Try to resolve class_ref
            const found = s.allClasses.find(x => x.name === input.value);
            if (found) assoc.target.class_ref = found.id;
          } else if (f === 'target_mult_lower') {
            assoc.target.multiplicity.lower = parseInt(input.value) || 0;
          } else if (f === 'target_mult_upper') {
            assoc.target.multiplicity.upper = parseInt(input.value);
          } else {
            assoc[f] = input.value;
          }
          EntityEdit._updateSummary();
        });
      });
    });
    area.querySelectorAll('[data-del-assoc]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.delAssoc;
        const assoc = s.editingOut.find(a => a.id === id);
        if (!assoc._isNew) {
          const ok = await showDialog({
            type: 'danger',
            title: '删除关联?',
            message: `确认删除关联「${assoc.label || assoc.name}」?`,
            okText: '删除', danger: true,
          });
          if (!ok) return;
        }
        s.editingOut = s.editingOut.filter(a => a.id !== id);
        EntityEdit.state.editingOut = s.editingOut;
        EntityEdit.render();
        EntityEdit._updateSummary();
      });
    });
  },

  _assocTypeLabel(t) {
    return ({ composition: '组合', aggregation: '聚合' })[t] || '引用';
  },

  // --- Rebuild inherited attributes from current parent chain ---
  // Called when user changes parent_class_name. Walks up the parent chain
  // (cross-layer: M1 → M2 allowed), collecting each ancestor's OWN (non-inherited)
  // attributes as is_inherited copies on this class. Skips duplicates and
  // names already present as own attrs on this class (child overrides).
  _rebuildInheritedAttrs() {
    const s = EntityEdit.state;
    if (!s) return;
    const c = s.editingClass;
    const byName = new Map();
    for (const p of (s.parentCandidates || [])) byName.set(p.name, p);

    // Keep only own attrs
    const ownAttrs = (c.attributes || []).filter(a => !a.is_inherited);
    const ownNames = new Set(ownAttrs.map(a => a.name));

    // Walk parent chain
    const collected = [];           // {name -> inherited copy}
    const seenNames = new Set(ownNames);
    const visited = new Set([c.name]);
    let pname = c.parent_class_name;

    while (pname && !visited.has(pname)) {
      visited.add(pname);
      const parent = byName.get(pname);
      if (!parent) break;
      for (const pa of (parent.attributes || [])) {
        if (pa.is_inherited) continue;         // we'll reach those via chain walk
        if (seenNames.has(pa.name)) continue;  // child (or closer ancestor) overrides
        const copy = JSON.parse(JSON.stringify(pa));
        copy.id = 'inh_' + Math.random().toString(36).slice(2, 10);
        copy.is_inherited = true;
        collected.push(copy);
        seenNames.add(pa.name);
      }
      pname = parent.parent_class_name || null;
    }

    c.attributes = [...ownAttrs, ...collected];
  },

  // --- Live change summary (sidebar) ---
  _updateSummary() {
    const diff = EntityEdit._computeDiff();
    const container = document.getElementById('ee-change-summary');
    const cascadeHint = document.getElementById('ee-cascade-hint');
    const previewBtn = document.getElementById('btn-ee-preview');

    const totalChanges =
      diff.classFields.length +
      diff.attrAdds.length + diff.attrUpdates.length + diff.attrDeletes.length +
      diff.assocAdds.length + diff.assocUpdates.length + diff.assocDeletes.length;

    previewBtn.disabled = totalChanges === 0;

    if (totalChanges === 0) {
      container.innerHTML = '<div class="ee-change-empty">暂无修改</div>';
      cascadeHint.innerHTML = '';
      return;
    }

    let html = '';
    if (diff.classFields.length) {
      html += `<div class="ee-change-group">
        <div class="ee-change-group-title">类字段 <span class="ch-count">${diff.classFields.length}</span></div>
        ${diff.classFields.map(f => `<div class="ee-change-item update">修改 <code>${f.field}</code></div>`).join('')}
      </div>`;
    }
    if (diff.attrAdds.length + diff.attrUpdates.length + diff.attrDeletes.length) {
      html += `<div class="ee-change-group">
        <div class="ee-change-group-title">属性 <span class="ch-count">${diff.attrAdds.length + diff.attrUpdates.length + diff.attrDeletes.length}</span></div>
        ${diff.attrAdds.map(a => `<div class="ee-change-item add">+ ${escapeHtml(a.name)}</div>`).join('')}
        ${diff.attrUpdates.map(u => `<div class="ee-change-item update">△ ${escapeHtml(u.after.name)}</div>`).join('')}
        ${diff.attrDeletes.map(a => `<div class="ee-change-item delete">− ${escapeHtml(a.name)}</div>`).join('')}
      </div>`;
    }
    if (diff.assocAdds.length + diff.assocUpdates.length + diff.assocDeletes.length) {
      html += `<div class="ee-change-group">
        <div class="ee-change-group-title">出向关联 <span class="ch-count">${diff.assocAdds.length + diff.assocUpdates.length + diff.assocDeletes.length}</span></div>
        ${diff.assocAdds.map(a => `<div class="ee-change-item add">+ ${escapeHtml(a.label || a.name)}</div>`).join('')}
        ${diff.assocUpdates.map(u => `<div class="ee-change-item update">△ ${escapeHtml(u.after.label || u.after.name)}</div>`).join('')}
        ${diff.assocDeletes.map(a => `<div class="ee-change-item delete">− ${escapeHtml(a.label || a.name)}</div>`).join('')}
      </div>`;
    }
    container.innerHTML = html;

    // Cascade hint
    const cascade = diff.cascade;
    if (cascade.childClasses.length || cascade.classAssocs.length) {
      cascadeHint.innerHTML = `<strong>🔗 级联影响</strong><br>
        ${cascade.childClasses.length ? `${cascade.childClasses.length} 个子类的 <code>parent_class_name</code> 将自动同步。<br>` : ''}
        ${cascade.classAssocs.length ? `${cascade.classAssocs.length} 个关联的类引用将自动更新。` : ''}`;
    } else {
      cascadeHint.innerHTML = '';
    }
  },

  // --- Diff computation ---
  _computeDiff() {
    const s = EntityEdit.state;
    if (!s) return { classFields: [], attrAdds: [], attrUpdates: [], attrDeletes: [], assocAdds: [], assocUpdates: [], assocDeletes: [], cascade: { childClasses: [], classAssocs: [] } };
    const o = s.origClass, e = s.editingClass;

    // Class-field diffs
    const classFields = [];
    for (const f of ['name', 'label', 'description', 'is_abstract', 'parent_class_name']) {
      if ((o[f] || '') !== (e[f] || '')) classFields.push({ field: f, before: o[f], after: e[f] });
    }

    // Attribute diffs (by id, ignore inherited — those can't change)
    const origAttrs = (o.attributes || []).filter(a => !a.is_inherited);
    const editAttrs = (e.attributes || []).filter(a => !a.is_inherited);
    const origAttrIds = new Set(origAttrs.map(a => a.id));
    const editAttrIds = new Set(editAttrs.map(a => a.id));
    const attrAdds = editAttrs.filter(a => !origAttrIds.has(a.id));
    const attrDeletes = origAttrs.filter(a => !editAttrIds.has(a.id));
    const attrUpdates = [];
    for (const ea of editAttrs) {
      const oa = origAttrs.find(x => x.id === ea.id);
      if (!oa) continue;
      if (JSON.stringify(oa) !== JSON.stringify(ea)) {
        attrUpdates.push({ before: oa, after: ea });
      }
    }

    // Outgoing association diffs (by id)
    const origIds = new Set(s.origOut.map(a => a.id));
    const editIds = new Set(s.editingOut.map(a => a.id));
    const assocAdds = s.editingOut.filter(a => !origIds.has(a.id));
    const assocDeletes = s.origOut.filter(a => !editIds.has(a.id));
    const assocUpdates = [];
    for (const ea of s.editingOut) {
      const oa = s.origOut.find(x => x.id === ea.id);
      if (!oa) continue;
      if (JSON.stringify(oa) !== JSON.stringify(ea)) {
        assocUpdates.push({ before: oa, after: ea });
      }
    }

    // Cascade: rename detection
    const cascade = { childClasses: [], classAssocs: [] };
    if (o.name !== e.name && o.name) {
      cascade.childClasses = s.origPackage.classes.filter(c =>
        c.parent_class_name === o.name && c.id !== o.id
      );
      cascade.classAssocs = s.origPackage.associations.filter(a =>
        (a.source?.class_name === o.name && a.source?.class_ref !== o.id) || // will update
        (a.target?.class_name === o.name) ||
        (a.source?.class_name === o.name)
      );
    }

    return { classFields, attrAdds, attrUpdates, attrDeletes, assocAdds, assocUpdates, assocDeletes, cascade };
  },

  // --- Compute the new Package with cascades applied ---
  _computeNewPackage() {
    const s = EntityEdit.state;
    const newPkg = JSON.parse(JSON.stringify(s.origPackage));
    const origName = s.origClass.name;
    const newName  = s.editingClass.name;
    const renamed = origName !== newName;

    // 1. Replace the class itself
    const ci = newPkg.classes.findIndex(c => c.id === s.editingClass.id);
    if (ci >= 0) {
      // Strip internal _isNew markers from attrs
      const cleaned = JSON.parse(JSON.stringify(s.editingClass));
      for (const a of (cleaned.attributes || [])) delete a._isNew;
      newPkg.classes[ci] = cleaned;
    }

    // 2. Cascade: rename → update other classes' parent_class_name
    if (renamed) {
      for (const c of newPkg.classes) {
        if (c.id !== s.editingClass.id && c.parent_class_name === origName) {
          c.parent_class_name = newName;
        }
      }
    }

    // 3. Cascade: rename → update association class_names (incoming assocs handled here)
    if (renamed) {
      for (const a of newPkg.associations) {
        if (a.source?.class_name === origName && a.source?.class_ref !== s.editingClass.id) {
          a.source.class_name = newName;  // foreign incoming ref updated
        } else if (a.source?.class_ref === s.editingClass.id) {
          a.source.class_name = newName;
        }
        if (a.target?.class_name === origName && a.target?.class_ref !== s.editingClass.id) {
          a.target.class_name = newName;
        } else if (a.target?.class_ref === s.editingClass.id) {
          a.target.class_name = newName;
        }
      }
    }

    // 4. Replace outgoing associations (delete old set, add edited set)
    // Any assoc whose source.class_ref == this class's id is "owned" by this class
    const thisId = s.editingClass.id;
    newPkg.associations = newPkg.associations.filter(a => a.source?.class_ref !== thisId);
    // Strip markers and add
    const cleanedOut = JSON.parse(JSON.stringify(s.editingOut));
    for (const a of cleanedOut) {
      delete a._isNew;
      // Ensure source reflects final class state
      if (a.source) {
        a.source.class_ref = thisId;
        a.source.class_name = newName;
      }
      // Resolve target class_ref if missing
      if (a.target && !a.target.class_ref && a.target.class_name) {
        const resolved = newPkg.classes.find(c => c.name === a.target.class_name);
        if (resolved) a.target.class_ref = resolved.id;
      }
    }
    newPkg.associations.push(...cleanedOut);

    return newPkg;
  },

  // --- Preview modal ---
  openPreview() {
    const diff = EntityEdit._computeDiff();
    EntityEdit._renderPreview(diff);
    document.getElementById('entity-preview-overlay').classList.remove('hidden');
  },

  closePreview() {
    document.getElementById('entity-preview-overlay').classList.add('hidden');
  },

  _renderPreview(diff) {
    const s = EntityEdit.state;
    const body = document.getElementById('ep-body');
    const total = diff.classFields.length + diff.attrAdds.length + diff.attrUpdates.length + diff.attrDeletes.length
      + diff.assocAdds.length + diff.assocUpdates.length + diff.assocDeletes.length;

    if (total === 0) {
      body.innerHTML = '<div class="ep-empty-preview">没有检测到变更</div>';
      return;
    }

    let html = '';

    // Cascade banner (upfront, for visibility)
    const renamed = s.origClass.name !== s.editingClass.name;
    if (renamed && (diff.cascade.childClasses.length || diff.cascade.classAssocs.length)) {
      html += `<div class="ep-banner cascade-info">
        <strong>🔗 类重命名级联:</strong>
        <code>${escapeHtml(s.origClass.name)}</code> → <code>${escapeHtml(s.editingClass.name)}</code>
        会自动同步 <strong>${diff.cascade.childClasses.length}</strong> 个子类的父类引用，
        <strong>${diff.cascade.classAssocs.length}</strong> 个关联的类名引用。
      </div>`;
    }

    // --- Class-level changes ---
    if (diff.classFields.length) {
      html += `<section class="ep-section">
        <div class="ep-section-title">类字段变更 <span class="ep-count">${diff.classFields.length}</span></div>
        <div class="ep-diff-item update">
          <div class="ep-diff-name">${escapeHtml(s.origClass.name)}</div>
          ${diff.classFields.map(f => `
            <div class="ep-field-diff">
              <span class="ep-field-name">${f.field}:</span>
              <span class="ep-field-before">${escapeHtml(String(f.before ?? '(空)'))}</span>
              <span class="ep-arrow">→</span>
              <span class="ep-field-after">${escapeHtml(String(f.after ?? '(空)'))}</span>
            </div>
          `).join('')}
        </div>
      </section>`;
    }

    // --- Attribute changes ---
    const attrTotal = diff.attrAdds.length + diff.attrUpdates.length + diff.attrDeletes.length;
    if (attrTotal) {
      html += `<section class="ep-section">
        <div class="ep-section-title">属性变更 <span class="ep-count">${attrTotal}</span></div>`;
      if (diff.attrAdds.length) {
        html += `<div class="ep-diff-group">
          <div class="ep-diff-label add">+ 新增 (${diff.attrAdds.length})</div>
          ${diff.attrAdds.map(a => `
            <div class="ep-diff-item add">
              <div class="ep-diff-name">${escapeHtml(a.name)} — ${escapeHtml(a.label || '')}</div>
              <div class="ep-field-diff"><span class="ep-field-name">类型:</span><span class="ep-field-after">${a.data_type}${a.unit ? ' (' + a.unit + ')' : ''}</span></div>
              <div class="ep-field-diff"><span class="ep-field-name">多重性:</span><span class="ep-field-after">[${a.multiplicity?.lower ?? 1}..${a.multiplicity?.upper === -1 ? '*' : (a.multiplicity?.upper ?? 1)}]</span></div>
            </div>
          `).join('')}
        </div>`;
      }
      if (diff.attrUpdates.length) {
        html += `<div class="ep-diff-group">
          <div class="ep-diff-label update">△ 修改 (${diff.attrUpdates.length})</div>
          ${diff.attrUpdates.map(u => `
            <div class="ep-diff-item update">
              <div class="ep-diff-name">${escapeHtml(u.after.name)}${u.before.name !== u.after.name ? ` <span style="color:var(--text-dim);font-size:10px;font-weight:normal">(原: ${escapeHtml(u.before.name)})</span>` : ''}</div>
              ${EntityEdit._attrFieldDiff(u.before, u.after)}
            </div>
          `).join('')}
        </div>`;
      }
      if (diff.attrDeletes.length) {
        html += `<div class="ep-diff-group">
          <div class="ep-diff-label delete">− 删除 (${diff.attrDeletes.length})</div>
          ${diff.attrDeletes.map(a => `
            <div class="ep-diff-item delete">
              <div class="ep-diff-name">${escapeHtml(a.name)} — ${escapeHtml(a.label || '')}</div>
            </div>
          `).join('')}
        </div>`;
      }
      html += '</section>';
    }

    // --- Association changes ---
    const assocTotal = diff.assocAdds.length + diff.assocUpdates.length + diff.assocDeletes.length;
    if (assocTotal) {
      html += `<section class="ep-section">
        <div class="ep-section-title">出向关联变更 <span class="ep-count">${assocTotal}</span></div>`;
      if (diff.assocAdds.length) {
        html += `<div class="ep-diff-group">
          <div class="ep-diff-label add">+ 新增 (${diff.assocAdds.length})</div>
          ${diff.assocAdds.map(a => `
            <div class="ep-diff-item add">
              <div class="ep-diff-name">${escapeHtml(a.label || a.name)}
                — <code>${escapeHtml(s.editingClass.name)}</code> ${EntityEdit._assocTypeLabel(a.association_type)} → <code>${escapeHtml(a.target?.class_name || '?')}</code>
              </div>
              <div class="ep-field-diff"><span class="ep-field-name">目标多重性:</span>
                <span class="ep-field-after">[${a.target?.multiplicity?.lower ?? 0}..${a.target?.multiplicity?.upper === -1 ? '*' : a.target?.multiplicity?.upper ?? 1}]</span>
              </div>
            </div>
          `).join('')}
        </div>`;
      }
      if (diff.assocUpdates.length) {
        html += `<div class="ep-diff-group">
          <div class="ep-diff-label update">△ 修改 (${diff.assocUpdates.length})</div>
          ${diff.assocUpdates.map(u => `
            <div class="ep-diff-item update">
              <div class="ep-diff-name">${escapeHtml(u.after.label || u.after.name)}</div>
              ${EntityEdit._assocFieldDiff(u.before, u.after)}
            </div>
          `).join('')}
        </div>`;
      }
      if (diff.assocDeletes.length) {
        html += `<div class="ep-diff-group">
          <div class="ep-diff-label delete">− 删除 (${diff.assocDeletes.length})</div>
          ${diff.assocDeletes.map(a => `
            <div class="ep-diff-item delete">
              <div class="ep-diff-name">${escapeHtml(a.label || a.name)} — → ${escapeHtml(a.target?.class_name || '?')}</div>
            </div>
          `).join('')}
        </div>`;
      }
      html += '</section>';
    }

    // --- Cascaded changes (details) ---
    if (renamed && (diff.cascade.childClasses.length || diff.cascade.classAssocs.length)) {
      html += `<section class="ep-section">
        <div class="ep-section-title">🔗 级联变更 <span class="ep-count">${diff.cascade.childClasses.length + diff.cascade.classAssocs.length}</span></div>
        <div class="ep-diff-group">
          <div class="ep-diff-label cascade">自动同步 (无需用户操作)</div>`;
      for (const c of diff.cascade.childClasses) {
        html += `<div class="ep-diff-item cascade">
          <div class="ep-diff-name">${escapeHtml(c.name)} (子类)</div>
          <div class="ep-field-diff"><span class="ep-field-name">父类:</span>
            <span class="ep-field-before">${escapeHtml(s.origClass.name)}</span>
            <span class="ep-arrow">→</span>
            <span class="ep-field-after">${escapeHtml(s.editingClass.name)}</span>
          </div>
        </div>`;
      }
      for (const a of diff.cascade.classAssocs) {
        html += `<div class="ep-diff-item cascade">
          <div class="ep-diff-name">关联: ${escapeHtml(a.label || a.name)}</div>
          <div class="ep-field-diff"><span class="ep-field-name">类名引用:</span>
            <span class="ep-field-before">${escapeHtml(s.origClass.name)}</span>
            <span class="ep-arrow">→</span>
            <span class="ep-field-after">${escapeHtml(s.editingClass.name)}</span>
          </div>
        </div>`;
      }
      html += '</div></section>';
    }

    body.innerHTML = html;
  },

  _attrFieldDiff(before, after) {
    const rows = [];
    for (const f of ['name', 'label', 'data_type', 'unit', 'enum_ref', 'description']) {
      if ((before[f] || '') !== (after[f] || '')) {
        rows.push(`<div class="ep-field-diff">
          <span class="ep-field-name">${f}:</span>
          <span class="ep-field-before">${escapeHtml(String(before[f] ?? '(空)'))}</span>
          <span class="ep-arrow">→</span>
          <span class="ep-field-after">${escapeHtml(String(after[f] ?? '(空)'))}</span>
        </div>`);
      }
    }
    const bm = before.multiplicity, am = after.multiplicity;
    if (bm && am && (bm.lower !== am.lower || bm.upper !== am.upper)) {
      const fmt = m => `[${m.lower}..${m.upper === -1 ? '*' : m.upper}]`;
      rows.push(`<div class="ep-field-diff">
        <span class="ep-field-name">多重性:</span>
        <span class="ep-field-before">${fmt(bm)}</span>
        <span class="ep-arrow">→</span>
        <span class="ep-field-after">${fmt(am)}</span>
      </div>`);
    }
    return rows.join('');
  },

  _assocFieldDiff(before, after) {
    const rows = [];
    for (const f of ['name', 'label', 'association_type']) {
      if ((before[f] || '') !== (after[f] || '')) {
        rows.push(`<div class="ep-field-diff">
          <span class="ep-field-name">${f}:</span>
          <span class="ep-field-before">${escapeHtml(String(before[f] ?? '(空)'))}</span>
          <span class="ep-arrow">→</span>
          <span class="ep-field-after">${escapeHtml(String(after[f] ?? '(空)'))}</span>
        </div>`);
      }
    }
    if (before.target?.class_name !== after.target?.class_name) {
      rows.push(`<div class="ep-field-diff">
        <span class="ep-field-name">目标类:</span>
        <span class="ep-field-before">${escapeHtml(before.target?.class_name || '(空)')}</span>
        <span class="ep-arrow">→</span>
        <span class="ep-field-after">${escapeHtml(after.target?.class_name || '(空)')}</span>
      </div>`);
    }
    const bm = before.target?.multiplicity, am = after.target?.multiplicity;
    if (bm && am && (bm.lower !== am.lower || bm.upper !== am.upper)) {
      const fmt = m => `[${m.lower}..${m.upper === -1 ? '*' : m.upper}]`;
      rows.push(`<div class="ep-field-diff">
        <span class="ep-field-name">目标多重性:</span>
        <span class="ep-field-before">${fmt(bm)}</span>
        <span class="ep-arrow">→</span>
        <span class="ep-field-after">${fmt(am)}</span>
      </div>`);
    }
    return rows.join('');
  },

  // --- Commit ---
  async commit() {
    const s = EntityEdit.state;
    if (!s) return;

    // Validate: require name, check uniqueness
    const e = s.editingClass;
    if (!e.name || !e.name.trim()) {
      await appAlert('类名称不能为空');
      return;
    }
    // Check name clash with other classes
    const nameClash = s.origPackage.classes.find(c => c.id !== e.id && c.name === e.name);
    if (nameClash) {
      await appAlert(`类名 "${e.name}" 已被其他类使用，请换一个`);
      return;
    }
    // Attribute name uniqueness within this class
    const attrNames = new Map();
    for (const a of (e.attributes || [])) {
      if (!a.name?.trim()) { await appAlert('存在名称为空的属性，请先修正'); return; }
      if (attrNames.has(a.name)) { await appAlert(`属性名重复: "${a.name}"`); return; }
      attrNames.set(a.name, true);
    }

    const commitBtn = document.getElementById('btn-ep-commit');
    commitBtn.disabled = true;
    commitBtn.textContent = '保存中...';

    try {
      const newPkg = EntityEdit._computeNewPackage();
      await API.updateModel(s.modelId, { package: newPkg });
      // Reload model into state
      await loadModel(s.modelId, s.layer);
      showToast('✓ 修改已保存');
      EntityEdit.close(true);
    } catch (e) {
      console.error(e);
      await appAlert('保存失败: ' + (e.message || String(e)));
      commitBtn.disabled = false;
      commitBtn.textContent = '✓ 确认保存';
    }
  },
};

// Helper: HTML-safe attribute value
function escapeAttr(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function filterTreeEntities(container, query) {
  if (!query) {
    container.querySelectorAll('.tree-card').forEach(c => c.style.display = '');
    container.querySelectorAll('.tree-cards-section').forEach(s => s.style.display = '');
    return;
  }
  container.querySelectorAll('.tree-cards-section').forEach(section => {
    const cards = section.querySelectorAll('.tree-card');
    let anyVisible = false;
    cards.forEach(card => {
      const text = card.dataset.searchText || '';
      if (text.includes(query)) {
        card.style.display = '';
        anyVisible = true;
      } else {
        card.style.display = 'none';
      }
    });
    section.style.display = anyVisible ? '' : 'none';
  });
}

function renderTree(layer) {
  const containerId = layer === 'm2' ? 'model-tree-m2' : 'model-tree-m1';
  const container = document.getElementById(containerId);
  const model = layer === 'm2' ? state.m2Model : state.m1Model;
  const modelId = layer === 'm2' ? state.m2ModelId : state.m1ModelId;

  if (!model || !model.versions || !model.versions.length) {
    const hint = layer === 'm2'
      ? '尚无M2元模型<br><span class="hint">M1模型生成后，点击 "反推M2"</span>'
      : '尚无M1模型<br><span class="hint">上传文档后，点击 "AI提取M1"</span>';
    container.innerHTML = `<div class="empty-state"><p>${hint}</p></div>`;
    return;
  }

  const pkg = model.versions[model.versions.length - 1].package;
  const classes = pkg.classes || [];
  const enums = pkg.enumerations || [];
  const assocs = pkg.associations || [];
  const totalAttrs = classes.reduce((sum, c) => sum + (c.attributes?.length || 0), 0);
  const viewMode = _treeViewMode[layer] || 'cards';  // Default to cards now

  container.innerHTML = '';

  // ---- Summary dashboard (always shown at top) ----
  const dashboard = document.createElement('div');
  dashboard.className = 'tree-dashboard';
  const pkgLabel = layer === 'm2' ? `${pkg.label || pkg.name}` : `${pkg.label || pkg.name}`;

  // V3.0: count structural patterns in this package
  const structuralPatterns = pkg.structural_patterns || [];
  const flatCount = classes.length - structuralPatterns.reduce(
    (sum, sp) => sum + (sp.participating_class_ids?.length || 0), 0
  );
  // Publish status (V3.0 § 2.4)
  const publishStatus = pkg.publish_status || 'draft';
  const publishLabel = {
    draft: '🟡 草稿', review: '🟠 评审中',
    published: '🟢 已发布', deprecated: '⚫ 已废弃',
  }[publishStatus] || publishStatus;

  dashboard.innerHTML = `
    <div class="tree-dashboard-header">
      <span class="tree-badge ${layer === 'm2' ? 'badge-m2' : 'badge-m1'}">${layer.toUpperCase()}</span>
      <span class="tree-dashboard-title">${escapeHtml(pkgLabel)}</span>
      <span class="tree-dashboard-ver" title="当前版本">v${model.current_version || '1.0'}</span>
      <button type="button" class="tree-dashboard-status status-${publishStatus}" data-action="change-publish" data-model-id="${modelId || ''}" data-current-status="${publishStatus}" title="点击切换发布状态 (V3.0 § 2.4)">${publishLabel} <span class="status-edit-icon">✎</span></button>
    </div>
    <div class="tree-dashboard-stats">
      <div class="tree-stat tree-stat-classes">
        <div class="tree-stat-num">${classes.length}</div>
        <div class="tree-stat-label">类</div>
      </div>
      ${layer === 'm2' ? `
      <div class="tree-stat tree-stat-structural">
        <div class="tree-stat-num">${structuralPatterns.length}</div>
        <div class="tree-stat-label" title="V3.0: 多 MetaClass + 层级关联">元结构</div>
      </div>
      <div class="tree-stat tree-stat-flat">
        <div class="tree-stat-num">${Math.max(0, flatCount)}</div>
        <div class="tree-stat-label" title="V3.0: 单 MetaClass">元类</div>
      </div>
      ` : `
      <div class="tree-stat tree-stat-attrs">
        <div class="tree-stat-num">${totalAttrs}</div>
        <div class="tree-stat-label">属性</div>
      </div>
      <div class="tree-stat tree-stat-assocs">
        <div class="tree-stat-num">${assocs.length}</div>
        <div class="tree-stat-label">关联</div>
      </div>
      `}
      <div class="tree-stat tree-stat-enums">
        <div class="tree-stat-num">${enums.length}</div>
        <div class="tree-stat-label">枚举</div>
      </div>
    </div>
    <div class="tree-dashboard-search">
      <input type="text" class="tree-search-input" id="tree-search-${layer}"
             placeholder="🔍 搜索类名、属性、描述..." />
    </div>
  `;
  container.appendChild(dashboard);

  // Wire search box
  const searchInput = dashboard.querySelector(`#tree-search-${layer}`);
  searchInput.addEventListener('input', e => {
    const q = e.target.value.toLowerCase().trim();
    filterTreeEntities(container, q);
  });

  // Wire publish-status button
  const statusBtn = dashboard.querySelector('[data-action="change-publish"]');
  if (statusBtn) {
    statusBtn.addEventListener('click', () => openPublishStatusDialog(layer, modelId, publishStatus));
  }

  // ---- V3.0 元结构面板 (仅 M2, 且存在 structural_patterns 时显示) ----
  if (layer === 'm2' && structuralPatterns.length) {
    const msPanel = renderMetaStructurePanel(modelId, pkg, structuralPatterns);
    container.appendChild(msPanel);
  }

  // ---- V3.0 M1 元结构分布面板 (仅 M1, 且关联的 M2 存在 structural_patterns) ----
  let _m1LevelMap = {};
  if (layer === 'm1') {
    const m2Pkg = state.m2Model?.versions?.slice(-1)[0]?.package;
    if (m2Pkg && (m2Pkg.structural_patterns || []).length) {
      const m1Panel = renderM1MetaStructurePanel(pkg, m2Pkg);
      if (m1Panel) container.appendChild(m1Panel);
      _m1LevelMap = computeM1LevelMap(pkg, m2Pkg);
    }
  }
  // expose to card-rendering code via closure variable
  const m1LevelMap = _m1LevelMap;

  const root = document.createElement('div');
  root.className = 'tree-node';

  if (viewMode === 'cards') {
    // Cards mode: compact browse — name + short description + attribute count
    if (classes.length) {
      const section = document.createElement('div');
      section.className = 'tree-cards-section';
      section.innerHTML = '<div class="tree-cards-section-title">🔷 类 Classes</div>';
      // Pre-compute relationship indices for fast lookup per class
      const assocsBySource = {};   // className -> [assoc]
      const assocsByTarget = {};
      for (const a of assocs) {
        const s = a.source?.class_name; const t = a.target?.class_name;
        if (s) (assocsBySource[s] = assocsBySource[s] || []).push(a);
        if (t) (assocsByTarget[t] = assocsByTarget[t] || []).push(a);
      }
      const enumIdToObj = {};
      for (const en of enums) enumIdToObj[en.id] = en;

      // ---- V3.0: group classes by metastructure level so browsing mirrors
      //      the 元结构 shape users just saw in the tree panel. ----
      // Shape of `groups`: [{ key, title, level, grid:HTMLElement, classes:[] }]
      // Fallback (no metastructure info): single group keyed 'all'.
      const LEVEL_COLOR_CSS = {
        1: 'var(--ms-l1)', 2: 'var(--ms-l2)', 3: 'var(--ms-l3)',
        4: 'var(--ms-l4)', 5: 'var(--ms-l5)', 0: 'var(--ms-floating)',
      };
      const groups = [];
      let groupByLevel = null;

      if (layer === 'm1' && Object.keys(m1LevelMap).length > 0) {
        // Build: { level -> { levelName, m2Label, classes: [] } }
        const m2Pkg = state.m2Model?.versions?.slice(-1)[0]?.package;
        const maxLvl = m2Pkg ? Math.max(0, ...(m2Pkg.structural_patterns || [])
          .flatMap(sp => (sp.participating_class_ids || [])
            .map(cid => m2Pkg.classes.find(c => c.id === cid)?.meta_structure_level || 0))) : 4;
        groupByLevel = {};
        for (const cls of classes) {
          const e = m1LevelMap[cls.id];
          const lvl = e ? e.level : 0;
          if (!groupByLevel[lvl]) groupByLevel[lvl] = { level: lvl, items: [], levelName: '', m2Label: '' };
          groupByLevel[lvl].items.push(cls);
          if (e) { groupByLevel[lvl].levelName = e.levelName; groupByLevel[lvl].m2Label = e.m2ClassLabel; }
        }
        // Order: 1..maxLvl then 0 (floating)
        for (let L = 1; L <= Math.max(maxLvl, 4); L++) {
          if (groupByLevel[L]) groups.push(groupByLevel[L]);
        }
        if (groupByLevel[0]) {
          groupByLevel[0].levelName = '游离';
          groupByLevel[0].m2Label = '未挂载 M2';
          groups.push(groupByLevel[0]);
        }
      } else if (layer === 'm2') {
        // M2: group by own meta_structure_level (so 元结构参与类 / 元类 are visually split)
        groupByLevel = {};
        for (const cls of classes) {
          const lvl = cls.meta_structure_level || 0;
          if (!groupByLevel[lvl]) groupByLevel[lvl] = { level: lvl, items: [], levelName: '', m2Label: '' };
          groupByLevel[lvl].items.push(cls);
          if (lvl) {
            groupByLevel[lvl].levelName = `L${lvl}`;
            groupByLevel[lvl].m2Label = cls.label || cls.name;
          }
        }
        // If everything is level 0 (no metastructure), degrade to single group
        const hasLeveled = Object.keys(groupByLevel).some(k => Number(k) > 0);
        if (hasLeveled) {
          for (let L = 1; L <= 5; L++) if (groupByLevel[L]) groups.push(groupByLevel[L]);
          if (groupByLevel[0]) {
            groupByLevel[0].levelName = '元类';
            groupByLevel[0].m2Label = '不属于任何元结构';
            groups.push(groupByLevel[0]);
          }
        } else {
          groups.push({ level: null, items: classes });
        }
      } else {
        groups.push({ level: null, items: classes });
      }

      // Create subsection + grid per group, append to section
      for (const g of groups) {
        if (g.level != null) {
          const sub = document.createElement('div');
          sub.className = `tree-cards-subsection tree-cards-sub-L${g.level}`;
          sub.innerHTML = `
            <div class="tree-cards-subsection-title" style="border-left-color:${LEVEL_COLOR_CSS[g.level]};">
              <span class="tree-cards-sub-level" style="color:${LEVEL_COLOR_CSS[g.level]};">${escapeHtml(g.levelName || '')}</span>
              <span class="tree-cards-sub-meta">${escapeHtml(g.m2Label || '')}</span>
              <span class="tree-cards-sub-count">${g.items.length} 个类</span>
            </div>
          `;
          const gGrid = document.createElement('div');
          gGrid.className = 'tree-cards-grid';
          g.grid = gGrid;
          sub.appendChild(gGrid);
          section.appendChild(sub);
        } else {
          // Ungrouped: put grid directly under section
          const gGrid = document.createElement('div');
          gGrid.className = 'tree-cards-grid';
          g.grid = gGrid;
          section.appendChild(gGrid);
        }
      }

      // Helper: get the right grid for a class
      function getGridForClass(cls) {
        for (const g of groups) {
          if (g.level == null) return g.grid;  // ungrouped
          const lvl = (layer === 'm1')
            ? (m1LevelMap[cls.id]?.level || 0)
            : (cls.meta_structure_level || 0);
          if (g.level === lvl) return g.grid;
        }
        return groups[0].grid;  // safety fallback
      }

      for (const cls of classes) {
        const grid = getGridForClass(cls);
        const attrCount = (cls.attributes || []).length;
        const inheritedCount = (cls.attributes || []).filter(a => a.is_inherited).length;
        const ownCount = attrCount - inheritedCount;
        const parentHint = cls.parent_class_name ? ` extends <span class="card-parent">${escapeHtml(cls.parent_class_name)}</span>` : '';
        const descShort = (cls.description || '').substring(0, 80) + ((cls.description || '').length > 80 ? '...' : '');
        // Compute relationships
        const outgoing = (assocsBySource[cls.name] || []).length;
        const incoming = (assocsByTarget[cls.name] || []).length;
        const totalAssocs = outgoing + incoming;
        // Enum usage: count distinct enums referenced by this class's attributes
        const usedEnumIds = new Set();
        for (const a of (cls.attributes || [])) {
          if (a.data_type === 'Enum' && a.enum_ref) usedEnumIds.add(a.enum_ref);
        }
        const enumCount = usedEnumIds.size;
        // Child classes (inheritance)
        const childCount = classes.filter(c => c.parent_class_name === cls.name).length;

        const card = document.createElement('div');
        card.className = 'tree-card tree-card-class';
        card.dataset.searchText = `${cls.name} ${cls.label || ''} ${cls.description || ''} ${(cls.attributes || []).map(a => a.name).join(' ')}`.toLowerCase();
        card.dataset.id = cls.id;
        card.dataset.type = 'class';
        // V3.0: tag cards with metastructure level for coloring (M1 via parent, M2 via own role)
        let _msLevel = null, _msLevelName = '', _msTooltip = '';
        if (layer === 'm1' && m1LevelMap[cls.id]) {
          const e = m1LevelMap[cls.id];
          _msLevel = e.level;
          _msLevelName = e.levelName;
          _msTooltip = `${e.levelName} · 继承自 ${e.m2ClassLabel}\n(来自元结构 "${e.patternLabel}")`;
        } else if (layer === 'm2' && cls.meta_structure_level) {
          _msLevel = cls.meta_structure_level;
          _msLevelName = `L${cls.meta_structure_level}`;
          _msTooltip = `M2 元结构层级 ${cls.meta_structure_level} (${cls.meta_structure_role || ''})`;
        }
        if (_msLevel) card.dataset.msLevel = String(_msLevel);
        const _chipHtml = _msLevel
          ? `<span class="tree-card-ms-chip ms-chip-L${_msLevel}" title="${escapeHtml(_msTooltip)}">L${_msLevel}</span>`
          : '';
        card.innerHTML = `
          <div class="tree-card-header">
            <span class="tree-badge badge-class">C</span>
            <span class="tree-card-name">${escapeHtml(cls.name)}</span>
            ${_chipHtml}
          </div>
          <div class="tree-card-label">${escapeHtml(cls.label || '')}${parentHint}</div>
          ${descShort ? `<div class="tree-card-desc">${escapeHtml(descShort)}</div>` : ''}
          <div class="tree-card-stats-row">
            <span class="card-stat card-stat-attrs" title="自有/继承属性">📋 ${ownCount}<span class="card-stat-sub">/${inheritedCount}</span></span>
            <span class="card-stat card-stat-assocs" title="出/入关联"${totalAssocs === 0 ? ' style="opacity:0.4"' : ''}>🔗 ${outgoing}<span class="card-stat-sub">/${incoming}</span></span>
            <span class="card-stat card-stat-enums" title="使用的枚举数"${enumCount === 0 ? ' style="opacity:0.4"' : ''}>🟨 ${enumCount}</span>
            ${childCount ? `<span class="card-stat card-stat-children" title="子类数量">👶 ${childCount}</span>` : ''}
          </div>
          <div class="tree-card-footer">
            <button class="tree-card-expand" title="展开属性列表">▼ 展开</button>
            <button class="tree-card-detail" title="查看详情页">🔍 详情</button>
          </div>
          <div class="tree-card-details hidden"></div>
        `;
        grid.appendChild(card);

        // Click card body (not buttons) → select in inspector
        card.addEventListener('click', e => {
          if (e.target.tagName === 'BUTTON') return;
          document.querySelectorAll('.tree-card.selected').forEach(c => c.classList.remove('selected'));
          card.classList.add('selected');
          selectElement('class', cls.id, null, layer);
        });

        // Expand button → inline attribute list
        const expandBtn = card.querySelector('.tree-card-expand');
        const details = card.querySelector('.tree-card-details');
        expandBtn.addEventListener('click', e => {
          e.stopPropagation();
          const open = details.classList.toggle('hidden');
          expandBtn.textContent = open ? '▼ 展开' : '▲ 收起';
          if (!open && !details.innerHTML) {
            let html = '<div class="card-attrs-list">';
            for (const a of (cls.attributes || [])) {
              const unit = a.unit ? ` (${a.unit})` : '';
              const inh = a.is_inherited ? ' <span class="attr-inh-tag">继承</span>' : '';
              html += `<div class="card-attr-row" data-attr-id="${a.id}">
                <span class="attr-name">${escapeHtml(a.name)}</span>
                <span class="attr-chinese">${escapeHtml(a.label || '')}</span>
                <span class="attr-type">${a.data_type}${unit}</span>${inh}
              </div>`;
            }
            html += '</div>';
            details.innerHTML = html;
            details.querySelectorAll('.card-attr-row').forEach(row => {
              row.addEventListener('click', ev => {
                ev.stopPropagation();
                selectElement('attribute', row.dataset.attrId, cls.id, layer);
              });
            });
          }
        });

        // Detail button → open entity detail page
        const detailBtn = card.querySelector('.tree-card-detail');
        detailBtn.addEventListener('click', e => {
          e.stopPropagation();
          openEntityDetailPage(cls, classes, enums, assocs, layer);
        });
      }
      // Grids were already attached to the section via groups above; just append section.
      root.appendChild(section);
    }

    // Enumerations in cards
    if (enums.length) {
      const section = document.createElement('div');
      section.className = 'tree-cards-section';
      section.innerHTML = '<div class="tree-cards-section-title">🟨 枚举 Enumerations</div>';
      const grid = document.createElement('div');
      grid.className = 'tree-cards-grid';
      for (const en of enums) {
        const lits = en.literals || [];
        const litPreview = lits.slice(0, 4).map(l => l.label || l.name).join(' · ');
        const card = document.createElement('div');
        card.className = 'tree-card tree-card-enum';
        card.dataset.searchText = `${en.name} ${en.label || ''} ${lits.map(l => (l.name+' '+(l.label||''))).join(' ')}`.toLowerCase();
        card.dataset.id = en.id;
        card.dataset.type = 'enumeration';
        card.innerHTML = `
          <div class="tree-card-header">
            <span class="tree-badge badge-enum">E</span>
            <span class="tree-card-name">${escapeHtml(en.name)}</span>
            <span class="tree-card-meta">${lits.length} 值</span>
          </div>
          <div class="tree-card-label">${escapeHtml(en.label || '')}</div>
          <div class="tree-card-desc">${escapeHtml(litPreview)}${lits.length > 4 ? ` ...+${lits.length - 4}` : ''}</div>
        `;
        card.addEventListener('click', () => {
          document.querySelectorAll('.tree-card.selected').forEach(c => c.classList.remove('selected'));
          card.classList.add('selected');
          selectElement('enumeration', en.id, null, layer);
        });
        grid.appendChild(card);
      }
      section.appendChild(grid);
      root.appendChild(section);
    }

    // Associations in cards
    if (assocs.length) {
      const section = document.createElement('div');
      section.className = 'tree-cards-section';
      section.innerHTML = '<div class="tree-cards-section-title">🟣 关联 Associations</div>';
      const grid = document.createElement('div');
      grid.className = 'tree-cards-grid tree-cards-grid-compact';
      for (const a of assocs) {
        const srcName = a.source?.class_name || '?';
        const tgtName = a.target?.class_name || '?';
        const multStr = a.target?.multiplicity ? `[${a.target.multiplicity.lower}..${a.target.multiplicity.upper === -1 ? '*' : a.target.multiplicity.upper}]` : '';
        const card = document.createElement('div');
        card.className = 'tree-card tree-card-assoc';
        card.dataset.searchText = `${a.name} ${a.label || ''} ${srcName} ${tgtName}`.toLowerCase();
        card.dataset.id = a.id;
        card.dataset.type = 'association';
        card.innerHTML = `
          <div class="tree-card-header">
            <span class="tree-badge badge-assoc">R</span>
            <span class="tree-card-name">${escapeHtml(a.name)}</span>
            <span class="tree-card-meta">${escapeHtml(a.association_type || 'association')}</span>
          </div>
          <div class="tree-card-label">${escapeHtml(a.label || '')}</div>
          <div class="tree-card-assoc-flow">
            <span class="assoc-endpoint">${escapeHtml(srcName)}</span>
            <span class="assoc-arrow">→</span>
            <span class="assoc-endpoint">${escapeHtml(tgtName)}</span>
            <span class="assoc-mult">${multStr}</span>
          </div>
        `;
        card.addEventListener('click', () => {
          document.querySelectorAll('.tree-card.selected').forEach(c => c.classList.remove('selected'));
          card.classList.add('selected');
          selectElement('association', a.id, null, layer);
        });
        grid.appendChild(card);
      }
      section.appendChild(grid);
      root.appendChild(section);
    }

    container.appendChild(root);
    return;
  }

  if (viewMode === 'hierarchy') {
    // Build hierarchy by inheritance + composition
    const classes = pkg.classes || [];
    const associations = pkg.associations || [];

    // Index helpers
    const byName = {};
    const byId = {};
    for (const c of classes) { byName[c.name] = c; byId[c.id] = c; }

    // Children via inheritance (C2.parent_class_name == C.name)
    const inheritChildren = {};
    for (const c of classes) {
      if (c.parent_class_name && byName[c.parent_class_name]) {
        (inheritChildren[c.parent_class_name] = inheritChildren[c.parent_class_name] || []).push(c);
      }
    }
    // Children via composition (association type==composition, source→target)
    const composChildren = {};
    for (const a of associations) {
      if (a.association_type === 'composition' || a.association_type === 'aggregation') {
        const srcName = a.source?.class_name;
        const tgtName = a.target?.class_name;
        if (srcName && tgtName && byName[srcName] && byName[tgtName]) {
          (composChildren[srcName] = composChildren[srcName] || []).push({cls: byName[tgtName], assoc: a});
        }
      }
    }

    // Find root classes: no inheritance parent AND not contained by any composition
    const containedNames = new Set();
    for (const kids of Object.values(composChildren)) {
      for (const k of kids) containedNames.add(k.cls.name);
    }
    const rootClasses = classes.filter(c =>
      (!c.parent_class_name || !byName[c.parent_class_name]) && !containedNames.has(c.name)
    );

    const visited = new Set();
    for (const cls of rootClasses) {
      root.appendChild(renderHierarchyClassNode(cls, modelId, layer, byName, inheritChildren, composChildren, visited, 0));
    }

    // Orphans (contained or inheriting but parent missing — show flat at bottom)
    const orphans = classes.filter(c => !visited.has(c.id));
    if (orphans.length) {
      for (const cls of orphans) {
        root.appendChild(renderHierarchyClassNode(cls, modelId, layer, byName, inheritChildren, composChildren, visited, 0));
      }
    }

    // Enumerations section
    if ((pkg.enumerations || []).length) {
      const sep = document.createElement('div');
      sep.className = 'tree-section-divider';
      sep.textContent = '── 枚举 ──';
      root.appendChild(sep);
      for (const en of pkg.enumerations) root.appendChild(renderEnumNode(en, modelId, layer));
    }
  } else {
    // Flat view (legacy)
    for (const cls of (pkg.classes || [])) root.appendChild(renderClassNode(cls, modelId, layer));
    for (const en of (pkg.enumerations || [])) root.appendChild(renderEnumNode(en, modelId, layer));
    for (const assoc of (pkg.associations || [])) root.appendChild(renderAssocNode(assoc, modelId, layer));
  }

  container.appendChild(root);
}

function renderHierarchyClassNode(cls, modelId, layer, byName, inheritChildren, composChildren, visited, depth) {
  if (visited.has(cls.id)) {
    // Already rendered — show a reference stub
    const stub = document.createElement('div');
    stub.className = 'tree-node';
    const row = document.createElement('div');
    row.className = 'tree-row';
    row.innerHTML = `<span class="tree-toggle">&#8635;</span><span class="tree-badge badge-class">C</span><span class="tree-label" style="color:var(--text-dim);font-style:italic">${cls.name} (已展开于上)</span>`;
    stub.appendChild(row);
    return stub;
  }
  visited.add(cls.id);

  const node = renderClassNode(cls, modelId, layer);

  // Find the .tree-children inside and append nested hierarchy
  const children = node.querySelector('.tree-children');
  if (!children) return node;

  // Inheritance children
  const inherits = inheritChildren[cls.name] || [];
  if (inherits.length) {
    const hdr = document.createElement('div');
    hdr.className = 'tree-section-subhead';
    hdr.innerHTML = `<span style="color:var(--yellow)">&#8659;</span> 子类 (继承)`;
    children.appendChild(hdr);
    for (const child of inherits) {
      children.appendChild(renderHierarchyClassNode(child, modelId, layer, byName, inheritChildren, composChildren, visited, depth + 1));
    }
  }

  // Composition children
  const composes = composChildren[cls.name] || [];
  if (composes.length) {
    const hdr = document.createElement('div');
    hdr.className = 'tree-section-subhead';
    hdr.innerHTML = `<span style="color:var(--purple)">&#9670;</span> 包含 (composition)`;
    children.appendChild(hdr);
    for (const {cls: child, assoc} of composes) {
      const mult = assoc.target?.multiplicity;
      const multStr = mult ? `[${mult.lower}..${mult.upper === -1 ? '*' : mult.upper}]` : '';
      const wrap = renderHierarchyClassNode(child, modelId, layer, byName, inheritChildren, composChildren, visited, depth + 1);
      // Annotate the row with multiplicity from association
      const firstRow = wrap.querySelector('.tree-row');
      if (firstRow) {
        const typeSpan = firstRow.querySelector('.tree-type');
        if (typeSpan) typeSpan.textContent = `${typeSpan.textContent} ${multStr} via ${assoc.name}`;
      }
      children.appendChild(wrap);
    }
  }

  return node;
}

function renderClassNode(cls, modelId, layer) {
  const node = document.createElement('div');
  node.className = 'tree-node';

  const parentInfo = cls.parent_class_name ? ` extends ${cls.parent_class_name}` : '';
  const row = createTreeRow('C', 'badge-class', cls.name, `${cls.label || ''}${parentInfo}`, cls.id, 'class', null, layer);

  addTreeAction(row, '+A', '', async () => {
    const name = await appPrompt('属性技术名 (camelCase，如 ratedCapacity):', '', '添加属性');
    if (!name) return;
    const label = await appPrompt('属性中文名 (如"额定容量"):', '', '属性中文名');
    await API.addAttribute(modelId, cls.id, { name, label: label || '' });
    await loadModel(modelId, layer);
  });
  addTreeAction(row, '\u{1F5D1}', 'del', async () => {
    const ok = await showDialog({
      type: 'danger', title: '删除类',
      message: `确定删除类 "${cls.name}" 吗？\n该类的所有属性也会一起删除。`,
      okText: '删除', cancelText: '取消', danger: true,
    });
    if (!ok) return;
    await API.deleteClass(modelId, cls.id);
    await loadModel(modelId, layer);
  });
  node.appendChild(row);

  const children = document.createElement('div');
  children.className = 'tree-children';
  for (const attr of (cls.attributes || [])) {
    children.appendChild(renderAttrNode(attr, cls.id, modelId, layer));
  }
  node.appendChild(children);

  row.querySelector('.tree-toggle').addEventListener('click', e => {
    e.stopPropagation();
    children.classList.toggle('collapsed');
    row.querySelector('.tree-toggle').textContent = children.classList.contains('collapsed') ? '\u25B6' : '\u25BC';
  });

  return node;
}

function renderAttrNode(attr, classId, modelId, layer) {
  const node = document.createElement('div');
  node.className = 'tree-node';
  const inherited = attr.is_inherited ? ' inherited' : '';
  const unitStr = attr.unit ? ` (${attr.unit})` : '';
  const row = createTreeRow('A', 'badge-attr',
    `<span class="${inherited}">${attr.name} ${attr.label || ''}</span>`,
    `${attr.data_type}${unitStr}`, attr.id, 'attribute', classId, layer);

  if (!attr.is_inherited) {
    addTreeAction(row, '\u{1F5D1}', 'del', async () => {
      await API.deleteAttribute(modelId, classId, attr.id);
      await loadModel(modelId, layer);
    });
  }
  node.appendChild(row);
  return node;
}

function renderEnumNode(en, modelId, layer) {
  const node = document.createElement('div');
  node.className = 'tree-node';
  const row = createTreeRow('E', 'badge-enum', en.name, en.label || '', en.id, 'enumeration', null, layer);
  addTreeAction(row, '\u{1F5D1}', 'del', async () => {
    await API.deleteEnumeration(modelId, en.id);
    await loadModel(modelId, layer);
  });
  node.appendChild(row);

  const children = document.createElement('div');
  children.className = 'tree-children';
  for (const lit of (en.literals || [])) {
    const litNode = document.createElement('div');
    litNode.className = 'tree-node';
    litNode.appendChild(createTreeRow('L', 'badge-lit', lit.name, lit.label || '', lit.id, 'literal', null, layer));
    children.appendChild(litNode);
  }
  node.appendChild(children);

  row.querySelector('.tree-toggle').addEventListener('click', e => {
    e.stopPropagation();
    children.classList.toggle('collapsed');
    row.querySelector('.tree-toggle').textContent = children.classList.contains('collapsed') ? '\u25B6' : '\u25BC';
  });
  return node;
}

function renderAssocNode(assoc, modelId, layer) {
  const node = document.createElement('div');
  node.className = 'tree-node';
  const srcName = assoc.source?.class_name || '?';
  const tgtName = assoc.target?.class_name || '?';
  const tgtMult = formatMult(assoc.target?.multiplicity);
  const row = createTreeRow('R', 'badge-assoc', assoc.name,
    `${srcName} \u2192 ${tgtName} ${tgtMult}`, assoc.id, 'association', null, layer);
  addTreeAction(row, '\u{1F5D1}', 'del', async () => {
    await API.deleteAssociation(modelId, assoc.id);
    await loadModel(modelId, layer);
  });
  node.appendChild(row);
  return node;
}

function formatMult(m) {
  if (!m) return '';
  const u = m.upper === -1 ? '*' : m.upper;
  return `[${m.lower}..${u}]`;
}

function createTreeRow(badge, badgeClass, label, typeInfo, elementId, elementType, classId, layer) {
  const row = document.createElement('div');
  row.className = 'tree-row';
  row.innerHTML = `
    <span class="tree-toggle">\u25BC</span>
    <span class="tree-badge ${badgeClass}">${badge}</span>
    <span class="tree-label">${label}</span>
    <span class="tree-type">${typeInfo}</span>
    <span class="tree-actions"></span>
  `;
  row.addEventListener('click', () => {
    document.querySelectorAll('.tree-row.selected').forEach(r => r.classList.remove('selected'));
    row.classList.add('selected');
    selectElement(elementType, elementId, classId, layer);
  });
  return row;
}

function addTreeAction(row, text, cls, handler) {
  const actions = row.querySelector('.tree-actions');
  const btn = document.createElement('button');
  btn.textContent = text;
  if (cls) btn.className = cls;
  btn.addEventListener('click', e => { e.stopPropagation(); handler(); });
  actions.appendChild(btn);
}

// ============================================================
// Property Inspector
// ============================================================
function selectElement(type, id, classId, layer) {
  const model = layer === 'm2' ? state.m2Model : state.m1Model;
  if (!model || !model.versions?.length) return;
  const pkg = model.versions[model.versions.length - 1].package;

  let data = null;
  if (type === 'class') data = pkg.classes.find(c => c.id === id);
  else if (type === 'attribute') {
    for (const c of pkg.classes) {
      if (c.id === classId) { data = c.attributes.find(a => a.id === id); break; }
    }
  }
  else if (type === 'enumeration') data = pkg.enumerations.find(e => e.id === id);
  else if (type === 'association') data = pkg.associations.find(a => a.id === id);

  state.selectedElement = { type, id, classId, data, layer };
  renderInspector();
}

function renderInspector() {
  const container = document.getElementById('inspector-content');
  const el = state.selectedElement;
  if (!el || !el.data) {
    container.innerHTML = '<div class="empty-state"><p>选择模型元素<br>查看/编辑属性</p></div>';
    return;
  }

  const modelId = el.layer === 'm2' ? state.m2ModelId : state.m1ModelId;
  const model = el.layer === 'm2' ? state.m2Model : state.m1Model;
  const layerLabel = el.layer === 'm2' ? 'M2' : 'M1';

  switch (el.type) {
    case 'class': renderClassInspector(container, el.data, modelId, el.layer); break;
    case 'attribute': renderAttrInspector(container, el.data, el.classId, modelId, el.layer); break;
    case 'enumeration': renderEnumInspector(container, el.data, modelId, el.layer); break;
    case 'association': renderAssocInspector(container, el.data); break;
    default: container.innerHTML = '<div class="empty-state"><p>不支持编辑此元素</p></div>';
  }
}

function renderClassInspector(container, cls, modelId, layer) {
  container.innerHTML = `
    <div class="insp-section"><h3>类 CLASS [${layer.toUpperCase()}]</h3>
      <div class="insp-field"><label>技术名 Name</label><input type="text" id="insp-name" value="${cls.name || ''}"></div>
      <div class="insp-field"><label>中文名 Label</label><input type="text" id="insp-label" value="${cls.label || ''}"></div>
      <div class="insp-field"><label>描述</label><textarea id="insp-desc">${cls.description || ''}</textarea></div>
      <div class="insp-field"><label>父类 Parent (M2)</label><input type="text" id="insp-parent" value="${cls.parent_class_name || ''}"></div>
      <div class="insp-field"><label><input type="checkbox" id="insp-abstract" ${cls.is_abstract ? 'checked' : ''}> 抽象类</label></div>
    </div>
    <div class="modal-actions"><button class="btn btn-primary" id="insp-save">保存</button></div>`;

  container.querySelector('#insp-save').addEventListener('click', async () => {
    await API.updateClass(modelId, cls.id, {
      name: container.querySelector('#insp-name').value,
      label: container.querySelector('#insp-label').value,
      description: container.querySelector('#insp-desc').value,
      parent_class_name: container.querySelector('#insp-parent').value,
      is_abstract: container.querySelector('#insp-abstract').checked,
    });
    await loadModel(modelId, layer);
  });
}

function renderAttrInspector(container, attr, classId, modelId, layer) {
  const model = layer === 'm2' ? state.m2Model : state.m1Model;
  const pkg = model.versions[model.versions.length - 1].package;
  const enumOptions = (pkg?.enumerations || []).map(e =>
    `<option value="${e.id}" ${attr.enum_ref === e.id ? 'selected' : ''}>${e.name} (${e.label || ''})</option>`).join('');

  container.innerHTML = `
    <div class="insp-section"><h3>属性 [${layer.toUpperCase()}]</h3>
      ${attr.is_inherited ? '<div class="insp-tag insp-tag-inherited">继承自M2</div>' : ''}
      <div class="insp-field"><label>技术名</label><input type="text" id="insp-name" value="${attr.name || ''}" ${attr.is_inherited ? 'disabled' : ''}></div>
      <div class="insp-field"><label>中文名</label><input type="text" id="insp-label" value="${attr.label || ''}"></div>
      <div class="insp-field"><label>数据类型</label>
        <select id="insp-dtype" ${attr.is_inherited ? 'disabled' : ''}>
          ${['String','Float','Integer','Date','Boolean','Enum'].map(t => `<option value="${t}" ${attr.data_type === t ? 'selected' : ''}>${t}</option>`).join('')}
        </select>
      </div>
      <div class="insp-field" id="enum-ref-field" ${attr.data_type !== 'Enum' ? 'style="display:none"' : ''}>
        <label>枚举引用</label><select id="insp-enum-ref"><option value="">--</option>${enumOptions}</select>
      </div>
      <div class="insp-field"><label>单位</label><input type="text" id="insp-unit" value="${attr.unit || ''}"></div>
      <div class="insp-row">
        <div class="insp-field"><label>Lower</label><input type="number" id="insp-mult-lower" value="${attr.multiplicity?.lower ?? 1}" min="0"></div>
        <div class="insp-field"><label>Upper (-1=*)</label><input type="number" id="insp-mult-upper" value="${attr.multiplicity?.upper ?? 1}" min="-1"></div>
      </div>
      <div class="insp-field"><label>描述</label><textarea id="insp-desc">${attr.description || ''}</textarea></div>
    </div>
    <div class="modal-actions"><button class="btn btn-primary" id="insp-save" ${attr.is_inherited ? 'disabled' : ''}>保存</button></div>`;

  container.querySelector('#insp-dtype').addEventListener('change', e => {
    container.querySelector('#enum-ref-field').style.display = e.target.value === 'Enum' ? '' : 'none';
  });

  if (!attr.is_inherited) {
    container.querySelector('#insp-save').addEventListener('click', async () => {
      await API.updateAttribute(modelId, classId, attr.id, {
        name: container.querySelector('#insp-name').value,
        label: container.querySelector('#insp-label').value,
        data_type: container.querySelector('#insp-dtype').value,
        enum_ref: container.querySelector('#insp-enum-ref')?.value || null,
        unit: container.querySelector('#insp-unit').value || null,
        multiplicity: {
          lower: parseInt(container.querySelector('#insp-mult-lower').value) || 0,
          upper: parseInt(container.querySelector('#insp-mult-upper').value) || 1,
        },
        description: container.querySelector('#insp-desc').value,
      });
      await loadModel(modelId, layer);
    });
  }
}

function renderEnumInspector(container, en, modelId, layer) {
  const litsHtml = (en.literals || []).map(l => `
    <div class="insp-row">
      <div class="insp-field"><input type="text" value="${l.name}" placeholder="name" class="lit-name"></div>
      <div class="insp-field"><input type="text" value="${l.label || ''}" placeholder="标签" class="lit-label"></div>
    </div>`).join('');

  container.innerHTML = `
    <div class="insp-section"><h3>枚举 [${layer.toUpperCase()}]</h3>
      <div class="insp-field"><label>技术名</label><input type="text" id="insp-name" value="${en.name || ''}"></div>
      <div class="insp-field"><label>中文名</label><input type="text" id="insp-label" value="${en.label || ''}"></div>
    </div>
    <div class="insp-section"><h3>枚举值</h3>
      <div id="lit-list">${litsHtml}</div>
      <button class="btn-sm" id="btn-add-lit">+ 添加</button>
    </div>
    <div class="modal-actions"><button class="btn btn-primary" id="insp-save">保存</button></div>`;

  container.querySelector('#btn-add-lit').addEventListener('click', () => {
    const div = document.createElement('div');
    div.className = 'insp-row';
    div.innerHTML = `<div class="insp-field"><input type="text" placeholder="name" class="lit-name"></div>
      <div class="insp-field"><input type="text" placeholder="标签" class="lit-label"></div>`;
    container.querySelector('#lit-list').appendChild(div);
  });

  container.querySelector('#insp-save').addEventListener('click', async () => {
    const literals = Array.from(container.querySelectorAll('#lit-list .insp-row')).map(row => ({
      name: row.querySelector('.lit-name').value,
      label: row.querySelector('.lit-label').value,
    }));
    const model = layer === 'm2' ? state.m2Model : state.m1Model;
    const pkg = model.versions[model.versions.length - 1].package;
    const enumObj = pkg.enumerations.find(e => e.id === en.id);
    if (enumObj) {
      enumObj.name = container.querySelector('#insp-name').value;
      enumObj.label = container.querySelector('#insp-label').value;
      enumObj.literals = literals.map(l => ({ id: crypto.randomUUID?.() || Math.random().toString(36).slice(2), name: l.name, label: l.label }));
    }
    await API.updateModel(modelId, { package: pkg });
    await loadModel(modelId, layer);
  });
}

function renderAssocInspector(container, assoc) {
  const sm = assoc.source?.multiplicity || {};
  const tm = assoc.target?.multiplicity || {};
  container.innerHTML = `
    <div class="insp-section"><h3>关联</h3>
      <div class="insp-field"><label>名称</label><input value="${assoc.name || ''}" disabled></div>
      <div class="insp-field"><label>中文名</label><input value="${assoc.label || ''}" disabled></div>
      <div class="insp-field"><label>类型</label><input value="${assoc.association_type || ''}" disabled></div>
    </div>
    <div class="insp-section"><h3>源端</h3>
      <div class="insp-field"><label>类</label><input value="${assoc.source?.class_name || ''}" disabled></div>
      <div class="insp-row">
        <div class="insp-field"><label>Lower</label><input value="${sm.lower ?? ''}" disabled></div>
        <div class="insp-field"><label>Upper</label><input value="${sm.upper ?? ''}" disabled></div>
      </div>
    </div>
    <div class="insp-section"><h3>目标端</h3>
      <div class="insp-field"><label>类</label><input value="${assoc.target?.class_name || ''}" disabled></div>
      <div class="insp-row">
        <div class="insp-field"><label>Lower</label><input value="${tm.lower ?? ''}" disabled></div>
        <div class="insp-field"><label>Upper</label><input value="${tm.upper ?? ''}" disabled></div>
      </div>
    </div>`;
}

// ============================================================
// Editor Actions
// ============================================================
function setupEditorActions() {
  document.getElementById('btn-add-class').addEventListener('click', async () => {
    const mid = activeModelId(); if (!mid) return;
    const name = await appPrompt('类技术名 (PascalCase，如 PumpedStorageUnit):', '', '添加新类');
    if (!name) return;
    const label = await appPrompt('类中文名 (如"抽水蓄能机组"):', '', '类中文名');
    await API.addClass(mid, { name, label: label || '' });
    await loadModel(mid, state.activeTab);
  });
  document.getElementById('btn-add-enum').addEventListener('click', async () => {
    const mid = activeModelId(); if (!mid) return;
    const name = await appPrompt('枚举技术名 (PascalCase，如 OperatingMode):', '', '添加枚举');
    if (!name) return;
    const label = await appPrompt('枚举中文名 (如"运行模式"):', '', '枚举中文名');
    await API.addEnumeration(mid, { name, label: label || '', literals: [] });
    await loadModel(mid, state.activeTab);
  });
  document.getElementById('btn-add-assoc').addEventListener('click', async () => {
    const mid = activeModelId(); if (!mid) return;
    const model = activeModel();
    const pkg = model.versions[model.versions.length - 1].package;
    if (!pkg?.classes?.length) {
      await showDialog({ type: 'warning', title: '无法添加关联', message: '请先添加至少两个类，才能创建它们之间的关联。' });
      return;
    }
    const name = await appPrompt('关联名称 (如 unitContainsTurbine):', '', '添加关联');
    if (!name) return;
    // Use a structured dialog to pick source/target instead of unwieldy prompts
    const classInfo = pkg.classes.map(c => `${c.label || c.name} [${c.id}]`).join('\n');
    const srcId = await appPrompt(`请复制下面的 源类ID (完整字符串):\n\n${classInfo}`, '', '源类');
    if (!srcId) return;
    const tgtId = await appPrompt(`请复制下面的 目标类ID:\n\n${classInfo}`, '', '目标类');
    if (!tgtId) return;
    await API.addAssociation(mid, { name, source_class_id: srcId.trim(), target_class_id: tgtId.trim() });
    await loadModel(mid, state.activeTab);
  });
}

// ============================================================
// Validation / Export / Version
// ============================================================
function validateModel() {
  const modal = document.getElementById('validation-modal');
  document.getElementById('validation-title').textContent = '✓ MOF规范验证';
  document.getElementById('validation-content').innerHTML = '';
  document.getElementById('validation-mode').value = 'local';
  document.getElementById('validation-llm-pickers').classList.add('hidden');
  populateValidatePickers();
  modal.classList.remove('hidden');
  // Auto-run local validation on open
  runLocalValidate();
}

function populateValidatePickers() {
  const m1Sel = document.getElementById('val-m1-picker');
  const m2Sel = document.getElementById('val-m2-picker');
  const m1s = (state.allModels || []).filter(m => !m.id.startsWith('m2_'));
  const m2s = (state.allModels || []).filter(m => m.id.startsWith('m2_'));

  m1Sel.innerHTML = m1s.map(m => `<option value="${m.id}" ${m.id === state.m1ModelId ? 'selected' : ''}>${escapeHtml(m.label || m.name)} [${m.id}]</option>`).join('') || '<option value="">无M1模型</option>';
  m2Sel.innerHTML = m2s.map(m => `<option value="${m.id}" ${m.id === state.m2ModelId ? 'selected' : ''}>${escapeHtml(m.label || m.name)} [${m.id}]</option>`).join('') || '<option value="">无M2模型（需先反推M2）</option>';
}

async function runLocalValidate() {
  const mid = activeModelId();
  if (!mid) { showToast('请先选择模型', 'error'); return; }
  const content = document.getElementById('validation-content');
  content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim)">检查中...</div>';
  try {
    const result = await API.validateModel(mid);
    if (result.is_valid && !result.warnings?.length) {
      content.innerHTML = `
        <div class="val-section">
          <div class="val-section-title val-success-h">✓ 本地M3规范检查通过</div>
          <div class="val-section-items">模型符合M3元元模型规范，类型/多重性/引用均有效。</div>
        </div>`;
    } else {
      let html = '';
      if ((result.errors || []).length) {
        html += `
          <div class="val-section">
            <div class="val-section-title val-issue-h">❌ 错误 (${result.errors.length})</div>
            <div class="val-section-items">
              ${result.errors.map(e => `<div>${escapeHtml(e.message)}</div>`).join('')}
            </div>
          </div>`;
      }
      if ((result.warnings || []).length) {
        html += `
          <div class="val-section">
            <div class="val-section-title val-rec-h">⚠ 警告 (${result.warnings.length})</div>
            <div class="val-section-items">
              ${result.warnings.map(w => `<div>${escapeHtml(w.message)}</div>`).join('')}
            </div>
          </div>`;
      }
      content.innerHTML = html;
    }
  } catch (e) {
    content.innerHTML = `<div style="padding:20px;color:var(--red)">验证失败: ${escapeHtml(e.message)}</div>`;
  }
}

async function runLLMValidate() {
  const m1Id = document.getElementById('val-m1-picker').value;
  const m2Id = document.getElementById('val-m2-picker').value;
  if (!m1Id || !m2Id) { showToast('请选择M1和M2模型', 'error'); return; }

  const content = document.getElementById('validation-content');
  content.innerHTML = '<div style="padding:30px;text-align:center;color:var(--text-dim)">🧠 AI正在分析MOF继承关系，约需30-60秒...</div>';

  const btn = document.getElementById('btn-run-llm-validate');
  btn.disabled = true; btn.textContent = '⏳ 分析中...';

  try {
    const r = await API.validateMOF(m1Id, m2Id);
    const score = r.overall_score || 0;
    const scoreClass = score >= 80 ? 'good' : (score >= 60 ? 'ok' : 'bad');

    let html = `
      <div class="val-score-card">
        <div class="val-score-num ${scoreClass}">${score}</div>
        <div class="val-score-summary">${escapeHtml(r.summary || '')}</div>
      </div>`;

    if ((r.compliant || []).length) {
      html += `
        <div class="val-section">
          <div class="val-section-title val-success-h">✓ 合规项 (${r.compliant.length})</div>
          <div class="val-section-items">
            ${r.compliant.map(c => `<div>${escapeHtml(c)}</div>`).join('')}
          </div>
        </div>`;
    }

    if ((r.issues || []).length) {
      html += `
        <div class="val-section">
          <div class="val-section-title val-issue-h">❌ 违规项 (${r.issues.length})</div>
          <div class="val-section-items">
            ${r.issues.map(i => `<div>
              <span class="val-severity-${i.severity || 'medium'}">[${i.severity || 'medium'}]</span>
              <strong>${escapeHtml(i.target || '')}</strong>: ${escapeHtml(i.problem || '')}
            </div>`).join('')}
          </div>
        </div>`;
    }

    if ((r.recommendations || []).length) {
      html += `
        <div class="val-section">
          <div class="val-section-title val-rec-h">💡 改进建议 (${r.recommendations.length})</div>
          <div class="val-section-items">
            ${r.recommendations.map(rec => `<div>
              <strong>${escapeHtml(rec.target || '')}</strong>: ${escapeHtml(rec.suggestion || '')}
            </div>`).join('')}
          </div>
        </div>`;
    }

    content.innerHTML = html;
  } catch (e) {
    content.innerHTML = `<div style="padding:20px;color:var(--red)">AI校验失败: ${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 开始AI校验';
  }
}

let _exportMode = 'raw';  // 'raw' | 'review'

function showExportModal() {
  document.getElementById('export-preview').classList.add('hidden');
  // Pre-select the active layer
  const layerRadio = document.querySelector(`input[name="export-layer"][value="${state.activeTab}"]`);
  if (layerRadio) layerRadio.checked = true;

  // Populate the review-mode sidebar info
  const m1 = state.m1Model;
  const m2 = state.m2Model;
  document.getElementById('review-m1-name').textContent =
    m1 ? `${m1.label || m1.name} [${m1.id}]` : '(未选择)';
  document.getElementById('review-m2-name').textContent =
    m2 ? `${m2.label || m2.name} [${m2.id}]` : '(未生成)';
  const m2Check = document.getElementById('review-include-m2');
  m2Check.checked = !!m2;
  m2Check.disabled = !m2;

  const statusEl = document.getElementById('review-status');
  statusEl.textContent = '';
  statusEl.className = 'export-review-status';

  // Populate the "完整包" tab summary (current M1/M2)
  const pkgM1 = document.getElementById('pkg-m1-name');
  const pkgM2 = document.getElementById('pkg-m2-name');
  if (pkgM1) pkgM1.textContent = m1 ? `${m1.label || m1.name} · ${m1.id}` : '(未选择)';
  if (pkgM2) pkgM2.textContent = m2 ? `${m2.label || m2.name} · ${m2.id}` : '(无关联 M2)';
  const pkgInclM2 = document.getElementById('pkg-include-m2');
  if (pkgInclM2) { pkgInclM2.checked = !!m2; pkgInclM2.disabled = !m2; }
  // Doc count hint
  const docIds = (m1 && m1.source_document_ids) || [];
  const docsHint = document.getElementById('pkg-docs-hint');
  if (docsHint) docsHint.textContent = docIds.length ? `(此模型关联 ${docIds.length} 个文档)` : '(此模型未关联文档)';
  const pkgNote = document.getElementById('pkg-note');
  if (pkgNote) pkgNote.value = '';
  const pkgSize = document.getElementById('pkg-size-hint');
  if (pkgSize) pkgSize.textContent = '';

  // Default to "raw" tab
  switchExportMode('raw');

  document.getElementById('export-modal').classList.remove('hidden');
}

function switchExportMode(mode) {
  _exportMode = mode;
  document.querySelectorAll('.export-mode-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.mode === mode));
  document.querySelectorAll('.export-mode-section').forEach(s =>
    s.classList.toggle('hidden', s.dataset.mode !== mode));
  // Update primary button text
  const btn = document.getElementById('btn-export-confirm');
  if (btn) {
    btn.textContent = mode === 'review' ? '📦 生成审查包 (.zip)'
                    : mode === 'package' ? '📦 导出完整包 (.mofpkg.zip)'
                    : '导出';
  }
}

async function doExport() {
  if (_exportMode === 'review') return doExportReviewPackage();
  if (_exportMode === 'package') return doExportCompletePackage();
  return doExportRaw();
}

async function doExportCompletePackage() {
  const m1Id = state.m1ModelId;
  if (!m1Id) {
    await showDialog({ type: 'warning', title: '无法导出', message: '当前未选择 M1 模型。' });
    return;
  }
  const options = {
    includeM2: document.getElementById('pkg-include-m2')?.checked !== false,
    includeAllVersions: !!document.getElementById('pkg-include-versions')?.checked,
    includeDocuments: !!document.getElementById('pkg-include-documents')?.checked,
    includeLLM: !!document.getElementById('pkg-include-llm')?.checked,
    note: (document.getElementById('pkg-note')?.value || '').trim(),
  };
  const confirmBtn = document.getElementById('btn-export-confirm');
  const origText = confirmBtn.textContent;
  confirmBtn.disabled = true;
  confirmBtn.textContent = '📦 打包中...';
  try {
    const { blob, filename } = await API.exportPackage(m1Id, options);
    // Trigger download
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
    // Update size hint + close modal after a short pause
    const sizeEl = document.getElementById('pkg-size-hint');
    if (sizeEl) sizeEl.textContent = `✓ 已下载: ${filename} (${(blob.size / 1024).toFixed(1)} KB)`;
    showToast(`完整包已下载 (${(blob.size / 1024).toFixed(1)} KB)`, 'success');
    setTimeout(() => document.getElementById('export-modal').classList.add('hidden'), 1200);
  } catch (e) {
    showDialog({ type: 'error', title: '完整包导出失败', message: e.message });
  } finally {
    confirmBtn.disabled = false;
    confirmBtn.textContent = origText;
  }
}

// ============================================================================
//                          Complete Package — IMPORT
// ============================================================================

let _importPkgFile = null;       // currently selected File
let _importPkgPreview = null;    // cached preview response

function showImportPackageModal() {
  _importPkgFile = null;
  _importPkgPreview = null;
  const m = document.getElementById('import-package-modal');
  // Reset state
  document.getElementById('import-pkg-filename').classList.add('hidden');
  document.getElementById('import-pkg-filename').textContent = '';
  document.getElementById('import-pkg-preview').classList.add('hidden');
  document.getElementById('btn-import-pkg-confirm').disabled = true;
  const fileInput = document.getElementById('import-pkg-file');
  if (fileInput) fileInput.value = '';
  // Default strategy
  const renameRadio = document.querySelector('input[name="import-strat"][value="rename"]');
  if (renameRadio) renameRadio.checked = true;
  m.classList.remove('hidden');
}

async function handleImportPackageFile(file) {
  if (!file) return;
  _importPkgFile = file;
  const fnEl = document.getElementById('import-pkg-filename');
  fnEl.textContent = `📦 ${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
  fnEl.classList.remove('hidden');
  const previewEl = document.getElementById('import-pkg-preview');
  const manifestEl = document.getElementById('import-pkg-manifest');
  const conflictsEl = document.getElementById('import-pkg-conflicts');
  manifestEl.innerHTML = '<div class="import-loading">正在解析包...</div>';
  conflictsEl.innerHTML = '';
  previewEl.classList.remove('hidden');
  try {
    const preview = await API.previewImportPackage(file);
    _importPkgPreview = preview;
    renderImportPreview(preview);
    document.getElementById('btn-import-pkg-confirm').disabled = false;
  } catch (e) {
    manifestEl.innerHTML = `<div class="import-err">❌ 包解析失败: ${escapeHtml(e.message)}</div>`;
    document.getElementById('btn-import-pkg-confirm').disabled = true;
  }
}

function renderImportPreview(p) {
  const m = p.manifest || {};
  const cs = m.contents || { models: [], documents: [], llm_providers: [] };
  const dep = p.dependency || {};
  const manifestEl = document.getElementById('import-pkg-manifest');
  const rows = [];
  rows.push(`<div class="import-pkg-meta"><b>标题:</b> ${escapeHtml(m.title || '(无)')}</div>`);
  rows.push(`<div class="import-pkg-meta"><b>导出时间:</b> ${escapeHtml(m.exported_at || '')}  ·  <b>格式版本:</b> ${escapeHtml(m.format_version || '')}  ·  <b>系统版本:</b> ${escapeHtml(m.mof_system_version || '')}</div>`);
  if (m.note) rows.push(`<div class="import-pkg-meta"><b>备注:</b> ${escapeHtml(m.note)}</div>`);
  // Models
  if (cs.models.length) {
    rows.push('<div class="import-pkg-meta"><b>模型:</b></div>');
    for (const md of cs.models) {
      const extra = [];
      if (md.class_count != null) extra.push(`${md.class_count} 类`);
      if (md.assoc_count) extra.push(`${md.assoc_count} 关联`);
      if (md.pattern_count) extra.push(`${md.pattern_count} 元结构`);
      rows.push(`<div class="import-pkg-line">${md.role === 'm1' ? '🏷️' : '🧬'} <b>${md.role.toUpperCase()}</b> <code>${escapeHtml(md.id)}</code> · ${escapeHtml(md.label || '')}${extra.length ? ' · (' + extra.join(', ') + ')' : ''}</div>`);
    }
  }
  if (cs.documents && cs.documents.length) {
    rows.push(`<div class="import-pkg-meta"><b>源文档:</b> ${cs.documents.length} 个</div>`);
  }
  if (cs.llm_providers && cs.llm_providers.length) {
    rows.push(`<div class="import-pkg-meta"><b>LLM Provider:</b> ${cs.llm_providers.length} 个 (API Key 未包含,导入后需要重新配置)</div>`);
  }
  // Dependency status
  if (dep.m2_template_id) {
    const st = dep.status;
    const label = st === 'bundled' ? '✓ M2 已随包' : st === 'local' ? '✓ M2 本地已有' : '⚠ M2 缺失';
    const cls = st === 'missing' ? 'import-dep-bad' : 'import-dep-ok';
    rows.push(`<div class="import-pkg-meta ${cls}"><b>依赖:</b> m2_template_id = <code>${escapeHtml(dep.m2_template_id)}</code> · ${label}</div>`);
  }
  // Warnings
  if (p.warnings && p.warnings.length) {
    for (const w of p.warnings) rows.push(`<div class="import-pkg-warn">⚠ ${escapeHtml(w)}</div>`);
  }
  manifestEl.innerHTML = rows.join('');

  // Conflicts
  const confEl = document.getElementById('import-pkg-conflicts');
  const conflicts = p.conflicts || {};
  const modelConf = (conflicts.models || []).filter(m => m.conflict);
  const docConf = (conflicts.documents || []).filter(d => d.conflict);
  if (!modelConf.length && !docConf.length) {
    confEl.innerHTML = '<div class="import-pkg-conf-ok">✓ 无冲突,所有 ID 本地均未存在</div>';
  } else {
    const lines = ['<div class="import-pkg-conf-warn">⚠ 以下 ID 本地已存在,请选择冲突策略:</div>'];
    for (const m of modelConf) lines.push(`<div class="import-pkg-line">• ${m.role.toUpperCase()} <code>${escapeHtml(m.id)}</code> · ${escapeHtml(m.label || '')}</div>`);
    for (const d of docConf) lines.push(`<div class="import-pkg-line">• DOC <code>${escapeHtml(d.id)}</code> · ${escapeHtml(d.filename || '')}</div>`);
    confEl.innerHTML = lines.join('');
  }
}

async function doImportPackage() {
  if (!_importPkgFile) return;
  const strategy = document.querySelector('input[name="import-strat"]:checked')?.value || 'rename';
  const autoLoad = !!document.getElementById('import-auto-load')?.checked;
  const btn = document.getElementById('btn-import-pkg-confirm');
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '📥 导入中...';
  try {
    const result = await API.importPackage(_importPkgFile, {
      strategy,
      importDocuments: true,
      importLLM: false,
    });
    // Summary
    const imported = (result.imported || []).filter(x => x.role || x.type !== 'document');
    const msg = `导入完成: ${imported.length} 项成功`
      + (result.skipped?.length ? ` · ${result.skipped.length} 跳过` : '')
      + (result.failed?.length ? ` · ${result.failed.length} 失败` : '');
    showToast(msg, result.failed?.length ? 'error' : 'success');
    // Close modal
    document.getElementById('import-package-modal').classList.add('hidden');
    // Refresh model list
    if (typeof loadExistingModels === 'function') {
      await loadExistingModels();
    }
    // Auto-load if desired
    if (autoLoad && result.primary_m1_id) {
      const picker = document.getElementById('model-picker');
      if (picker) {
        picker.value = result.primary_m1_id;
        picker.dispatchEvent(new Event('change'));
      } else if (typeof loadModel === 'function') {
        await loadModel(result.primary_m1_id, 'm1');
      }
    }
    // Surface any warnings/failures
    if (result.failed?.length) {
      const lines = result.failed.map(f => `• ${f.id || f.type || ''}: ${f.error || f.reason || ''}`).join('\n');
      showDialog({ type: 'warning', title: '部分导入失败', message: lines });
    } else if (result.warnings?.length) {
      showDialog({ type: 'info', title: '导入提示', message: result.warnings.join('\n') });
    }
  } catch (e) {
    showDialog({ type: 'error', title: '导入失败', message: e.message });
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

function wireImportPackageModal() {
  const btnOpen = document.getElementById('btn-import-package');
  const btnCancel = document.getElementById('btn-import-pkg-cancel');
  const btnConfirm = document.getElementById('btn-import-pkg-confirm');
  const zone = document.getElementById('import-pkg-zone');
  const fileInput = document.getElementById('import-pkg-file');
  const pickBtn = document.getElementById('import-pkg-pick-btn');
  if (!btnOpen) return;
  btnOpen.addEventListener('click', showImportPackageModal);
  btnCancel.addEventListener('click', () => document.getElementById('import-package-modal').classList.add('hidden'));
  btnConfirm.addEventListener('click', doImportPackage);
  pickBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => {
    const f = e.target.files?.[0]; if (f) handleImportPackageFile(f);
  });
  // Drag-drop
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('dragover');
    const f = e.dataTransfer?.files?.[0]; if (f) handleImportPackageFile(f);
  });
}

async function doExportRaw() {
  const layer = document.querySelector('input[name="export-layer"]:checked').value;
  const mid = layer === 'm2' ? state.m2ModelId : state.m1ModelId;
  if (!mid) { await showDialog({ type: 'warning', title: '无法导出', message: `当前没有 ${layer.toUpperCase()} 模型可导出。` }); return; }
  const fmt = document.querySelector('input[name="export-fmt"]:checked').value;
  try {
    const content = await API.exportModel(mid, fmt);
    const preview = document.getElementById('export-preview');
    preview.textContent = content;
    preview.classList.remove('hidden');
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ext = { json: 'json', yaml: 'yaml', mof_text: 'mof' }[fmt] || 'txt';
    a.download = `${layer}_model.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) { showDialog({ type: 'error', title: '导出失败', message: e.message }); }
}

// ==========================================================================
// Review-package generation — with staged progress UI
// ==========================================================================
// Strategy: client-side asymptotic "fake" progress that stops at 95%, then
// snaps to 100% when the real response arrives. Stage messages rotate based
// on current progress to mirror the server's actual work (data fetch → Word →
// Excel → diagrams → PDF → zip). Real backend timing varies 5-30 seconds;
// the animator uses an exponential decay so the bar keeps moving either way.

// Stage definitions: [thresholdPct, label, icon]. When progress crosses the
// threshold, we mark that stage "active" and previous ones "done".
const REVIEW_EXPORT_STAGES = [
  { pct: 8,  label: '准备数据',      icon: '📝' },
  { pct: 28, label: '生成 Word 报告',  icon: '📄' },
  { pct: 48, label: '构建 Excel 意见表', icon: '📊' },
  { pct: 68, label: '渲染关系图集',     icon: '🎨' },
  { pct: 85, label: '生成 PDF + 打包', icon: '📦' },
];

function createReviewExportProgress() {
  const overlay  = document.getElementById('export-progress-overlay');
  const card     = overlay.querySelector('.export-progress-card');
  const fillEl   = document.getElementById('export-progress-fill');
  const pctEl    = document.getElementById('export-progress-pct');
  const msgEl    = document.getElementById('export-progress-message');
  const titleEl  = document.getElementById('export-progress-title');
  const elapsedEl = document.getElementById('export-progress-elapsed');
  const stagesEl = document.getElementById('export-progress-stages');

  // Render stage chips
  stagesEl.innerHTML = '';
  for (const s of REVIEW_EXPORT_STAGES) {
    const chip = document.createElement('span');
    chip.className = 'export-progress-stage';
    chip.dataset.threshold = s.pct;
    chip.textContent = `${s.icon} ${s.label}`;
    stagesEl.appendChild(chip);
  }

  const startMs = Date.now();
  let rafId = null;
  let done = false;
  let lastPct = 0;
  // Estimated total ms (rough). Actual time varies; the bar uses asymptotic
  // curve so if real time is longer, bar just creeps more slowly toward 95%.
  const estMs = 12000;

  card.classList.remove('done', 'error');
  titleEl.textContent = '正在生成审查包';

  function updateStages(pct) {
    const chips = stagesEl.querySelectorAll('.export-progress-stage');
    // Find current stage = last chip whose threshold <= pct
    let activeIdx = -1;
    for (let i = 0; i < REVIEW_EXPORT_STAGES.length; i++) {
      if (pct >= REVIEW_EXPORT_STAGES[i].pct) activeIdx = i;
    }
    chips.forEach((chip, i) => {
      chip.classList.remove('active', 'done');
      if (i < activeIdx) chip.classList.add('done');
      else if (i === activeIdx) chip.classList.add('active');
    });
    // Update message to current stage
    if (activeIdx >= 0) {
      const s = REVIEW_EXPORT_STAGES[activeIdx];
      msgEl.textContent = `${s.icon} ${s.label}...`;
    } else {
      msgEl.textContent = '📤 发送请求到服务器...';
    }
  }

  function tick() {
    if (done) return;
    const elapsed = Date.now() - startMs;
    // Asymptotic curve: pct = 95 * (1 - e^(-2.3 * t/est))
    // Reaches ~80% at t=est, ~95% at t=2*est, never actually 100%
    const t = elapsed / estMs;
    const pct = Math.min(95, 95 * (1 - Math.exp(-2.3 * t)));
    lastPct = pct;
    fillEl.style.width = pct + '%';
    pctEl.textContent = Math.floor(pct) + '%';
    // Elapsed timer
    const s = Math.floor(elapsed / 1000);
    elapsedEl.textContent =
      `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
    updateStages(pct);
    rafId = requestAnimationFrame(tick);
  }

  overlay.classList.remove('hidden');
  tick();

  return {
    complete: () => {
      done = true;
      if (rafId) cancelAnimationFrame(rafId);
      fillEl.style.width = '100%';
      pctEl.textContent = '100%';
      msgEl.textContent = '✓ 完成！正在触发下载...';
      card.classList.add('done');
      // Mark all stages done
      stagesEl.querySelectorAll('.export-progress-stage').forEach(c => {
        c.classList.remove('active');
        c.classList.add('done');
      });
    },
    error: (msg) => {
      done = true;
      if (rafId) cancelAnimationFrame(rafId);
      titleEl.textContent = '生成失败';
      msgEl.textContent = '✗ ' + (msg || '未知错误');
      card.classList.add('error');
    },
    hide: () => {
      done = true;
      if (rafId) cancelAnimationFrame(rafId);
      overlay.classList.add('hidden');
    },
  };
}

async function doExportReviewPackage() {
  if (!state.m1ModelId) {
    await showDialog({ type: 'warning', title: '无法生成审查包', message: '请先选择一个 M1 模型。' });
    return;
  }
  const includeM2 = document.getElementById('review-include-m2').checked;
  const m2Id = includeM2 && state.m2ModelId ? state.m2ModelId : null;

  // Close the export modal — the progress modal takes over
  document.getElementById('export-modal').classList.add('hidden');

  const statusEl = document.getElementById('review-status');
  statusEl.textContent = '';
  statusEl.className = 'export-review-status';

  const progress = createReviewExportProgress();

  try {
    const { blob, filename } = await API.exportReviewPackage(state.m1ModelId, m2Id);
    progress.complete();
    // Small delay so the user visually sees the 100% before the download dialog pops
    await new Promise(r => setTimeout(r, 400));

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);

    showToast(`✓ 审查包已生成 (${(blob.size / 1024).toFixed(0)} KB)`);
    // Auto-hide progress after brief success display
    setTimeout(() => progress.hide(), 1000);
  } catch (e) {
    console.error(e);
    progress.error(e.message || String(e));
    // Keep error visible briefly, then hide + also show dialog for the full error
    setTimeout(() => progress.hide(), 2000);
    showDialog({ type: 'error', title: '审查包生成失败', message: e.message || String(e) });
  }
}

async function createVersion() {
  const mid = activeModelId();
  if (!mid) return;
  const model = activeModel();
  if (!model) return;

  // Show version modal and populate
  document.getElementById('version-model-name').textContent = model.label || model.name;
  document.getElementById('version-current').textContent = 'v' + (model.current_version || '1.0');
  document.getElementById('version-changelog').value = '';
  await renderVersionList(mid);
  document.getElementById('version-modal').classList.remove('hidden');
}

async function renderVersionList(modelId) {
  const listEl = document.getElementById('version-list');
  try {
    const res = await API.listVersions(modelId);
    const current = res.current_version;
    const versions = (res.versions || []).slice().reverse();
    if (!versions.length) {
      listEl.innerHTML = '<div class="empty-state" style="padding:20px">暂无版本</div>';
      return;
    }
    listEl.innerHTML = '';
    for (const v of versions) {
      const isCurrent = v.version === current;
      const date = v.created_at ? new Date(v.created_at).toLocaleString('zh-CN') : '';
      const item = document.createElement('div');
      item.className = `version-item${isCurrent ? ' current' : ''}`;
      item.innerHTML = `
        <span class="version-tag">v${v.version}</span>
        <div class="version-info-col">
          <div class="version-changelog">${escapeHtml(v.changelog || '')}</div>
          <div class="version-date">${date}${isCurrent ? ' · 当前' : ''}</div>
        </div>
        <div class="version-actions">
          ${!isCurrent ? `<button class="btn-sm ver-switch" data-ver="${v.version}">切换到此版本</button>` : ''}
        </div>
      `;
      listEl.appendChild(item);
    }
    listEl.querySelectorAll('.ver-switch').forEach(btn => {
      btn.addEventListener('click', async () => {
        {
          const ok = await showDialog({
            type: 'warning',
            title: '切换版本',
            message: `切换到 v${btn.dataset.ver} 吗？\n\n当前未保存的更改将丢失（除非先创建快照）。`,
            okText: '确认切换', cancelText: '取消',
          });
          if (!ok) return;
        }
        try {
          await API.switchVersion(modelId, btn.dataset.ver);
          showToast(`已切换到 v${btn.dataset.ver}`);
          await loadModel(modelId, state.activeTab);
          document.getElementById('version-modal').classList.add('hidden');
        } catch (e) { showToast('切换失败: ' + e.message, 'error'); }
      });
    });
  } catch (e) {
    listEl.innerHTML = `<div style="padding:20px;color:var(--red)">加载失败: ${escapeHtml(e.message)}</div>`;
  }
}

async function doCreateVersionSnapshot() {
  const mid = activeModelId(); if (!mid) return;
  const changelog = document.getElementById('version-changelog').value.trim();
  const btn = document.getElementById('btn-version-create');
  btn.disabled = true; btn.textContent = '创建中...';
  try {
    const result = await API.createVersion(mid, changelog || '手动创建的版本快照');
    showToast(`✓ 新版本 v${result.version} 已创建`);
    await loadModel(mid, state.activeTab);
    await renderVersionList(mid);
    document.getElementById('version-current').textContent = 'v' + result.version;
    document.getElementById('version-changelog').value = '';
  } catch (e) {
    showToast('创建失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '💾 创建新版本快照';
  }
}

// ============================================================
// Extraction Review Panel
// ============================================================

let _reviewData = null;
let _reviewTaskId = null;  // task_id for retry API

function setupReviewPanel() {
  document.getElementById('btn-review-cancel').addEventListener('click', closeReviewPanel);
  document.getElementById('btn-review-confirm').addEventListener('click', confirmReviewImport);
  document.getElementById('btn-review-all').addEventListener('click', () => toggleAllReview(true));
  document.getElementById('btn-review-none').addEventListener('click', () => toggleAllReview(false));
  document.getElementById('btn-retry-all').addEventListener('click', retryAllFailed);
}

async function retryAllFailed(selectedIds = null) {
  if (!_reviewTaskId) return;
  const btn = document.getElementById('btn-retry-all');
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '正在重试...';

  try {
    const res = await API.retryFailed(_reviewTaskId, selectedIds);
    if (res.status === 'no_failures') {
      showToast('没有需要重试的批次', 'error');
      btn.disabled = false;
      btn.textContent = origText;
      return;
    }
    showToast(`正在重试 ${res.count} 个批次，请稍候...`);

    // Poll task until retry completes
    let tries = 0;
    while (tries < 300) {
      await new Promise(r => setTimeout(r, 1500));
      const s = await API.pollTask(_reviewTaskId);
      if (s.status === 'completed' && s.step === 'completed') {
        // Retry done — refresh review panel with new result
        showReviewPanel(s.result);
        showToast(`重试完成: ${s.message}`);
        return;
      }
      if (s.status === 'failed' || s.status === 'cancelled') {
        throw new Error(s.error || '重试失败');
      }
      tries++;
    }
    throw new Error('重试超时');
  } catch (e) {
    showToast('重试失败: ' + e.message, 'error');
    btn.disabled = false;
    btn.textContent = origText;
  }
}

/**
 * Update the workbench right-zone (results) with current package state.
 * Safe to call repeatedly as extraction progresses.
 * Preserves user's checkbox selections between calls.
 */
function updateWorkbenchResults(result, failedBatches = [], isFinal = false) {
  if (!result || !result.package) return;

  _reviewData = result;
  const pkg = result.package;
  const classes = pkg.classes || [];
  const enums = pkg.enumerations || [];
  const assocs = pkg.associations || [];

  // Stats (live-updating)
  document.getElementById('wb-stat-classes').textContent = classes.length;
  document.getElementById('wb-stat-attrs').textContent = result.attributes_found || 0;
  document.getElementById('wb-stat-assocs').textContent = assocs.length;
  document.getElementById('wb-stat-enums').textContent = enums.length;

  // LIVE badge
  const liveBadge = document.getElementById('wb-live-badge');
  if (liveBadge) {
    if (isFinal) {
      liveBadge.textContent = '✓ 完成';
      liveBadge.style.color = 'var(--green)';
      liveBadge.style.animation = 'none';
    } else {
      liveBadge.textContent = '● LIVE';
      liveBadge.style.color = 'var(--red)';
      liveBadge.style.animation = '';
    }
  }

  // Detect M1 vs M2 pipeline from result
  const isM2 = !!result.is_m2;

  // Adapt labels for M1 vs M2 context
  const labelField = labelInput => {
    if (!labelInput) return;
    if (!labelInput.value) {
      const timestamp = new Date().toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'});
      const m1Src = state.m1Model?.label || 'M1';
      labelInput.value = isM2 ? `${m1Src} 的M2元模型 - ${timestamp}` : `M1模型 - ${timestamp}`;
    }
  };
  labelField(document.getElementById('wb-model-label'));

  // Update header title if M2
  const title = document.getElementById('progress-title');
  if (title && isM2 && !title.textContent.includes('M2')) {
    title.textContent = 'AI 推导 M2 元模型';
  }

  // Stats labels
  const classLabel = document.querySelector('.stat-classes .wb-stat-label');
  if (classLabel) classLabel.textContent = isM2 ? '抽象基类' : '类';

  // Failed batches
  renderFailedBatches(failedBatches);

  // Notes (compact)
  const notesEl = document.getElementById('review-notes');
  const notes = (result.confidence_notes || []).filter(n => n && !n.includes('失败'));
  if (notes.length) {
    notesEl.innerHTML = '<strong>⚠ AI注意:</strong> ' + notes.slice(0, 3).join(' | ') + (notes.length > 3 ? ` …+${notes.length-3}` : '');
    notesEl.classList.remove('hidden');
  } else {
    notesEl.classList.add('hidden');
  }

  // Preserve user's checkbox state when re-rendering
  const prevChecked = new Set();
  const prevUnchecked = new Set();
  document.querySelectorAll('.review-entity-check').forEach(cb => {
    if (cb.checked) prevChecked.add(cb.dataset.id);
    else prevUnchecked.add(cb.dataset.id);
  });

  // Render entities
  renderReviewEntities(classes, enums, assocs);

  // Restore checkbox state (new entities default to checked)
  document.querySelectorAll('.review-entity-check').forEach(cb => {
    if (prevUnchecked.has(cb.dataset.id)) {
      cb.checked = false;
      cb.closest('.review-entity').classList.add('unchecked');
    }
  });

  updateReviewConfirmBtn();
}

// Legacy alias — now opens/updates the workbench directly
function showReviewPanel(result, failedBatches = []) {
  document.getElementById('progress-overlay').classList.remove('hidden');
  updateWorkbenchResults(result, failedBatches, true);
}

function renderFailedBatches(failedBatches) {
  const panel = document.getElementById('wb-failed-section');
  const list = document.getElementById('review-failed-list');
  const count = document.getElementById('failed-count');
  const retryAllBtn = document.getElementById('btn-retry-all');

  if (!panel) return;

  // Filter to only un-retried or retry-failed batches
  const retriable = (failedBatches || []).filter(fb => !fb.retried || !fb.retry_success);

  if (!retriable.length) {
    panel.classList.add('hidden');
    return;
  }

  panel.classList.remove('hidden');
  count.textContent = failedBatches.length;
  retryAllBtn.disabled = false;
  retryAllBtn.textContent = `↻ 重试全部 (${retriable.length})`;

  const typeMap = {
    // New combined (entity + attributes) batch type from the single-pass extractor
    combined_extraction: '实体+属性',
    // Legacy (kept for in-flight tasks from older server builds)
    entity_discovery: '实体',
    attribute_extraction: '属性',
    association_extraction: '关联',
    // M2 derivation phases
    m2_clustering: 'M2聚类',
    m2_synthesis: 'M2抽象',
    m2_hierarchy: 'M2层级探测',
    m2_consolidation: 'M2合并',
  };

  list.innerHTML = '';
  for (const fb of failedBatches) {
    const retriedOk = fb.retried && fb.retry_success;
    const retriedFail = fb.retried && !fb.retry_success;
    const cls = retriedOk ? 'retried-ok' : (retriedFail ? 'retried-fail' : '');
    const statusIcon = retriedOk ? '✓' : (retriedFail ? '✗' : '');
    const errorText = retriedFail ? (fb.retry_error || '') : (fb.error || '').substring(0, 80);

    const item = document.createElement('div');
    item.className = `failed-item ${cls}`;
    item.innerHTML = `
      <span class="failed-item-type">${typeMap[fb.type] || fb.type}</span>
      <span class="failed-item-label">${statusIcon} ${escapeHtml(fb.label || fb.id)}</span>
      <span class="failed-item-error" title="${escapeHtml(errorText)}">${escapeHtml(errorText)}</span>
      ${!retriedOk ? `<button class="failed-item-retry" data-id="${fb.id}">${retriedFail ? '再试' : '重试'}</button>` : ''}
    `;
    list.appendChild(item);
  }

  // Wire individual retry buttons
  list.querySelectorAll('.failed-item-retry').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = '...';
      await retryAllFailed([btn.dataset.id]);
    });
  });
}

function renderReviewEntities(classes, enums, assocs) {
  const container = document.getElementById('review-entities');
  let html = '';

  // Classes
  if (classes.length) {
    html += '<div class="review-section-title">类 Classes</div>';
    for (const cls of classes) {
      const attrs = cls.attributes || [];
      const attrLines = attrs.map(a =>
        `<div class="review-attr-line"><span class="review-attr-name">${a.name} ${a.label || ''}</span><span class="review-attr-type">${a.data_type}${a.unit ? ' ('+a.unit+')' : ''}</span></div>`
      ).join('');
      html += `
        <div class="review-entity" data-type="class" data-id="${cls.id}">
          <div class="review-entity-header">
            <input type="checkbox" class="review-entity-check" data-id="${cls.id}" data-type="class" checked>
            <span class="review-entity-badge badge-class">C</span>
            <span class="review-entity-name">${cls.name} <span style="color:var(--text-dim);font-weight:400">${cls.label || ''}</span></span>
            <span class="review-entity-meta">${attrs.length} 个属性</span>
            <button class="review-entity-toggle" data-target="details-${cls.id}">${attrs.length > 0 ? '▶ 展开' : ''}</button>
          </div>
          <div class="review-entity-details" id="details-${cls.id}">${attrLines}</div>
        </div>`;
    }
  }

  // Enumerations
  if (enums.length) {
    html += '<div class="review-section-title">枚举 Enumerations</div>';
    for (const en of enums) {
      const litText = (en.literals || []).map(l => l.label || l.name).join(', ');
      html += `
        <div class="review-entity" data-type="enumeration" data-id="${en.id}">
          <div class="review-entity-header">
            <input type="checkbox" class="review-entity-check" data-id="${en.id}" data-type="enumeration" checked>
            <span class="review-entity-badge badge-enum">E</span>
            <span class="review-entity-name">${en.name} <span style="color:var(--text-dim);font-weight:400">${en.label || ''}</span></span>
            <span class="review-entity-meta">${(en.literals||[]).length} 个值</span>
          </div>
          <div class="review-assoc-desc" style="padding-left:44px;font-size:11px;color:var(--text-dim)">${litText}</div>
        </div>`;
    }
  }

  // Associations
  if (assocs.length) {
    html += '<div class="review-section-title">关联 Associations</div>';
    for (const a of assocs) {
      const src = a.source?.class_name || '?';
      const tgt = a.target?.class_name || '?';
      const tMult = a.target?.multiplicity;
      const multStr = tMult ? `[${tMult.lower}..${tMult.upper === -1 ? '*' : tMult.upper}]` : '';
      html += `
        <div class="review-entity" data-type="association" data-id="${a.id}">
          <div class="review-entity-header">
            <input type="checkbox" class="review-entity-check" data-id="${a.id}" data-type="association" checked>
            <span class="review-entity-badge badge-assoc">R</span>
            <span class="review-entity-name">${a.name}</span>
            <span class="review-entity-meta">${src} → ${tgt} ${multStr}</span>
          </div>
        </div>`;
    }
  }

  container.innerHTML = html;

  // Wire up toggle expand/collapse
  container.querySelectorAll('.review-entity-toggle').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const target = document.getElementById(btn.dataset.target);
      if (!target) return;
      const open = target.classList.toggle('open');
      btn.textContent = open ? '▼ 收起' : '▶ 展开';
    });
  });

  // Wire up checkboxes
  container.querySelectorAll('.review-entity-check').forEach(cb => {
    cb.addEventListener('change', () => {
      const entity = cb.closest('.review-entity');
      entity.classList.toggle('unchecked', !cb.checked);
      updateReviewConfirmBtn();
    });
  });

  // Click header to toggle checkbox
  container.querySelectorAll('.review-entity-header').forEach(header => {
    header.addEventListener('click', e => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
      const cb = header.querySelector('.review-entity-check');
      cb.checked = !cb.checked;
      cb.dispatchEvent(new Event('change'));
    });
  });
}

function toggleAllReview(checked) {
  document.querySelectorAll('.review-entity-check').forEach(cb => {
    cb.checked = checked;
    cb.closest('.review-entity').classList.toggle('unchecked', !checked);
  });
  updateReviewConfirmBtn();
}

function updateReviewConfirmBtn() {
  const checked = document.querySelectorAll('.review-entity-check:checked').length;
  const total = document.querySelectorAll('.review-entity-check').length;
  const btn = document.getElementById('btn-review-confirm');
  if (!btn) return;
  if (total === 0) {
    btn.textContent = '尚无可保存的内容';
    btn.disabled = true;
  } else {
    const modelType = (_progMode === 'm2') ? 'M2元模型' : 'M1模型';
    btn.textContent = `💾 保存选中的 ${checked} / ${total} 项为${modelType}`;
    btn.disabled = checked === 0;
  }
}

async function closeReviewPanel() {
  // If extraction still running, require confirmation (only once)
  if (_currentTaskId) {
    const ok = await showDialog({
      type: 'warning',
      title: '中止并关闭',
      message: '提取仍在进行中，关闭将丢弃所有当前成果。确定继续？',
      okText: '中止并关闭',
      cancelText: '继续等待',
      danger: true,
    });
    if (!ok) return;
    // Skip cancelExtraction's own confirm — use internal cancel directly
    await cancelExtractionNoConfirm();
  }
  document.getElementById('progress-overlay').classList.add('hidden');
  _reviewData = null;
  hideProgress();
}

async function confirmReviewImport() {
  if (!_reviewData) return;
  const pkg = _reviewData.package;
  const isM2 = !!_reviewData.is_m2;

  // Collect selected IDs
  const selectedIds = new Set();
  document.querySelectorAll('.review-entity-check:checked').forEach(cb => {
    selectedIds.add(cb.dataset.id);
  });

  // Filter package to only selected entities
  const filteredPkg = {
    ...pkg,
    classes: (pkg.classes || []).filter(c => selectedIds.has(c.id)),
    enumerations: (pkg.enumerations || []).filter(e => selectedIds.has(e.id)),
    associations: (pkg.associations || []).filter(a => selectedIds.has(a.id)),
  };

  const btn = document.getElementById('btn-review-confirm');
  btn.disabled = true;
  btn.textContent = '正在保存...';

  const userLabel = (document.getElementById('wb-model-label')?.value || '').trim();
  const defaultLabel = isM2 ? `M2_${Date.now()}` : `M1_${Date.now()}`;
  const finalLabel = userLabel || filteredPkg.label || defaultLabel;

  try {
    let res;
    if (isM2) {
      // M2 branch — use dedicated M2 save endpoint
      res = await API.saveFromM2Review({
        package: { ...filteredPkg, label: finalLabel },
        name: filteredPkg.name || 'M2MetaModel',
        label: finalLabel,
        source_m1_id: _reviewData.source_m1_id,
        m1_class_mappings: _reviewData.m1_class_mappings || [],
      });
    } else {
      res = await API.saveFromExtraction({
        package: { ...filteredPkg, label: finalLabel },
        name: filteredPkg.name || `M1_Model`,
        label: finalLabel,
        source_document_ids: _reviewData.source_document_ids || [],
      });
    }

    // Refresh model list
    const listRes = await API.listModels();
    state.allModels = listRes.models;

    // Load the new model
    if (isM2) {
      state.m2ModelId = res.model_id;
      await loadModel(res.model_id, 'm2');
      // Also reload M1 (now has parent_class_name refs)
      if (_reviewData.source_m1_id) {
        await loadModel(_reviewData.source_m1_id, 'm1');
      }
    } else {
      state.m1ModelId = res.model_id;
      await loadModel(res.model_id, 'm1');
    }
    renderModelPicker();
    closeReviewPanel();

    showToast(`✓ ${isM2 ? 'M2元模型' : 'M1模型'}已保存: ${finalLabel}`);

    // Switch to appropriate tab
    document.querySelector(`.tab[data-tab="${isM2 ? 'm2' : 'm1'}"]`).click();
  } catch (e) {
    await showDialog({ type: 'error', title: '保存失败', message: e.message });
    btn.disabled = false;
    updateReviewConfirmBtn();
  }
}

// ============================================================
// M3 Display (fixed, read-only)
// ============================================================

let m3Data = null;

async function loadM3() {
  try {
    m3Data = await API.getM3();
    renderM3();
  } catch (e) { console.error('Failed to load M3:', e); }
}

function renderM3() {
  const container = document.getElementById('m3-content');
  if (!m3Data) {
    container.innerHTML = '<div class="empty-state"><p>M3数据加载失败</p></div>';
    return;
  }

  let html = `
    <div class="m3-banner">
      <span class="m3-banner-icon">\u{1F512}</span>
      <div class="m3-banner-text">
        <strong>M3 元元模型（固定层）</strong><br>
        ${m3Data.description}
      </div>
    </div>
    <div class="m3-grid">`;

  for (const c of m3Data.concepts) {
    const subtypes = (c.subtypes || []).map(s => `<span class="m3-subtype-tag">${s}</span>`).join('');
    html += `
      <div class="m3-card" style="border-left-color:${c.color}">
        <div class="m3-card-name" style="color:${c.color}">${c.name}</div>
        <div class="m3-card-label">${c.label}</div>
        <div class="m3-card-desc">${c.description}</div>
        ${subtypes ? `<div class="m3-card-subtypes">${subtypes}</div>` : ''}
      </div>`;
  }
  html += '</div>';
  container.innerHTML = html;
}

// ============================================================
// Relationship Graph (interactive canvas with pan/zoom/drag)
// ============================================================
// Replaces the old static top-down diagram. Key features:
//   - Infinite virtual canvas via SVG viewport transform
//   - Circular nodes (sphere gradient) sized by attribute count
//   - Force-directed auto-layout with parent-pull for M1→M2
//   - Mouse: drag empty space = pan, wheel = zoom at cursor, drag node = reposition
//   - Hover node = rich tooltip with navigable parent/children links
//   - Click node = select + highlight neighborhood (others dim)
//   - Double-click node = open entity detail page (reuses existing flow)
//   - Keyboard: F fit, 0 reset zoom, L re-layout, Esc clear selection, / focus search
//   - Filter chips to toggle M1/M2/inherit/assoc visibility
//   - Live search to center on matching node
//   - Minimap with draggable viewport rect

const Graph = {
  // --- state ---
  nodes: [],              // {id, layer, data, r, x, y, vx, vy, pinned, fixed}
  edges: [],              // {id, kind, source, target, label, ...}
  nodesById: new Map(),
  selectedId: null,
  hoveredId: null,
  visible: { m2: true, m1: true, inherit: true, assoc: true },
  searchQuery: '',
  transform: { x: 0, y: 0, k: 1 },      // viewport: pan + scale
  positionsByModel: {},                   // persist layouts per m1+m2 model id pair
  layoutMode: 'hierarchical',             // 'hierarchical' | 'force'
  _animRAF: null,
  _drag: null,                            // active node-drag state
  _pan: null,                             // active pan state
  _el: {},                                // cached DOM handles
  _layoutRunning: false,
};

// Layer colors
const LAYER_COLORS = {
  m2: '#a78bfa',
  m1: '#5b8af5',
};

// -------- Entry point (called when user switches to diagram tab) --------
function renderDiagram() {
  Graph._el.root      = document.getElementById('graph-root');
  Graph._el.svg       = document.getElementById('graph-svg');
  Graph._el.viewport  = document.getElementById('graph-viewport');
  Graph._el.edgesG    = document.getElementById('graph-edges');
  Graph._el.nodesG    = document.getElementById('graph-nodes');
  Graph._el.tooltip   = document.getElementById('graph-tooltip');
  Graph._el.empty     = document.getElementById('graph-empty');
  Graph._el.minimap   = document.getElementById('graph-minimap-svg');
  Graph._el.miniNodes = document.getElementById('graph-minimap-nodes');
  Graph._el.miniView  = document.getElementById('graph-minimap-viewport');
  Graph._el.selinfo   = document.getElementById('graph-selinfo');
  Graph._el.search    = document.getElementById('graph-search');
  Graph._el.zoomPct   = document.getElementById('graph-zoom-pct');

  if (!Graph._initialized) {
    graphSetupInteractions();
    graphSetupToolbar();
    graphSetupKeyboard();
    Graph._initialized = true;
  }

  graphBuildFromState();

  if (Graph.nodes.length === 0) {
    graphShowEmpty();
    return;
  }
  graphHideEmpty();

  // Restore saved positions or run fresh layout
  const key = graphModelKey();
  const saved = Graph.positionsByModel[key];
  if (saved && saved.length === Graph.nodes.length) {
    for (const n of Graph.nodes) {
      const p = saved.find(s => s.id === n.id);
      if (p) { n.x = p.x; n.y = p.y; }
    }
    graphRender();
    graphFitToView(true);
  } else {
    graphAutoLayout();
  }
}

// -------- Build nodes/edges from current M1 + M2 state --------
function graphBuildFromState() {
  const m1Pkg = state.m1Model?.versions?.slice(-1)[0]?.package;
  const m2Pkg = state.m2Model?.versions?.slice(-1)[0]?.package;
  const m1Classes = m1Pkg?.classes || [];
  const m2Classes = m2Pkg?.classes || [];

  Graph.nodes = [];
  Graph.edges = [];
  Graph.nodesById = new Map();

  // --- Nodes ---
  const nodeSize = c => {
    const n = (c.attributes?.length || 0);
    // Base radius 18 + 1.2px per attribute, capped at 36
    return Math.min(36, 18 + n * 1.2);
  };

  // V3.0 metastructure patterns defined on the M2 Package — used to group
  // M2 MetaClasses that belong to the same 元结构 in the visualization.
  const m2StructuralPatterns = m2Pkg?.structural_patterns || [];
  const m2PatternByClassId = new Map();  // class_id → pattern object
  for (const sp of m2StructuralPatterns) {
    for (const cid of (sp.participating_class_ids || [])) {
      m2PatternByClassId.set(cid, sp);
    }
  }

  for (const c of m2Classes) {
    const pattern = m2PatternByClassId.get(c.id);
    const node = {
      id: 'm2:' + c.id,
      layer: 'm2',
      data: c,
      r: nodeSize(c) + 4,  // M2 slightly larger for visual hierarchy
      x: 0, y: 0, vx: 0, vy: 0,
      pinned: false,
      abstract: !!c.is_abstract,
      // Metastructure metadata for rendering grouped boxes / coloring
      metaStructureId: pattern?.id || c.meta_structure_id || null,
      metaStructureName: pattern?.label || pattern?.name || null,
      metaStructureRole: c.meta_structure_role || null,
      metaStructureLevel: c.meta_structure_level || null,
    };
    Graph.nodes.push(node);
    Graph.nodesById.set(node.id, node);
  }
  for (const c of m1Classes) {
    const node = {
      id: 'm1:' + c.id,
      layer: 'm1',
      data: c,
      r: nodeSize(c),
      x: 0, y: 0, vx: 0, vy: 0,
      pinned: false,
      abstract: !!c.is_abstract,
    };
    Graph.nodes.push(node);
    Graph.nodesById.set(node.id, node);
  }

  // --- Edges: M1 → M2 inheritance (match by parent_class_name) ---
  const m2ByName = new Map(m2Classes.map(c => ['m2:' + c.id, c]));
  const m2NameToId = new Map(m2Classes.map(c => [c.name, 'm2:' + c.id]));
  for (const c of m1Classes) {
    if (c.parent_class_name) {
      const tgtId = m2NameToId.get(c.parent_class_name);
      if (tgtId) {
        Graph.edges.push({
          id: `ih:m1:${c.id}->${tgtId}`,
          kind: 'inherit',
          source: 'm1:' + c.id,
          target: tgtId,
          label: '',
        });
      }
    }
  }

  // --- Edges: M1 intra-layer associations ---
  const m1NameToId = new Map(m1Classes.map(c => [c.name, 'm1:' + c.id]));
  for (const a of (m1Pkg?.associations || [])) {
    const srcName = a.source?.class_name;
    const tgtName = a.target?.class_name;
    if (!srcName || !tgtName) continue;
    const srcId = m1NameToId.get(srcName);
    const tgtId = m1NameToId.get(tgtName);
    if (!srcId || !tgtId || srcId === tgtId) continue;
    Graph.edges.push({
      id: `as:m1:${a.id || srcId + '>' + tgtId}`,
      kind: a.association_type === 'composition' ? 'composition' : 'assoc',
      source: srcId,
      target: tgtId,
      label: a.label || a.name || '',
    });
  }

  // --- Edges: M2 intra-layer associations ---
  // V3.0: metastructure hierarchy associations get a distinct "m2-hierarchy" kind
  // so they can be rendered more prominently (colored, arrow style).
  for (const a of (m2Pkg?.associations || [])) {
    const srcName = a.source?.class_name;
    const tgtName = a.target?.class_name;
    if (!srcName || !tgtName) continue;
    const srcId = Graph.nodesById.has('m2:' + (a.source?.class_ref || ''))
      ? 'm2:' + a.source.class_ref
      : [...m2NameToId.entries()].find(([n]) => n === srcName)?.[1];
    const tgtId = Graph.nodesById.has('m2:' + (a.target?.class_ref || ''))
      ? 'm2:' + a.target.class_ref
      : [...m2NameToId.entries()].find(([n]) => n === tgtName)?.[1];
    if (!srcId || !tgtId || srcId === tgtId) continue;
    const isHierarchy = !!a.is_hierarchy;
    Graph.edges.push({
      id: `as:m2:${a.id || srcId + '>' + tgtId}`,
      kind: isHierarchy ? 'm2-hierarchy' : 'm2-assoc',
      source: srcId,
      target: tgtId,
      label: a.label || a.name || '',
      hierarchyOrder: a.hierarchy_order || null,
    });
  }
}

function graphModelKey() {
  return `${state.m1ModelId || ''}::${state.m2ModelId || ''}`;
}

function graphSavePositions() {
  Graph.positionsByModel[graphModelKey()] =
    Graph.nodes.map(n => ({ id: n.id, x: n.x, y: n.y }));
}

// -------- Layout dispatcher --------
function graphAutoLayout() {
  if (Graph.nodes.length === 0) return;
  cancelAnimationFrame(Graph._animRAF);
  Graph._layoutRunning = false;
  if (Graph.layoutMode === 'hierarchical') {
    graphHierarchicalLayout();
  } else {
    graphForceLayout();
  }
}

// -------- Hierarchical (layered tree + grid packing) --------
// Design:
//   Row 0: all M2 classes in a horizontal row, each allocated width proportional
//          to its subtree's horizontal footprint (so wide subtrees don't overflow).
//   Row 1+: each M2's direct M1 children packed in a GRID below it (not a single
//          row — wraps at `maxColsPerRow` to keep canvas aspect reasonable).
//   Recursive: any class that has descendants (M1→M1) drills down another layer.
//   Orphan M1 (no parent, or parent not in graph): packed into a dense grid to the
//          right of all M2 subtrees.
function graphHierarchicalLayout() {
  const H = 110;                  // horizontal slot spacing
  const V = 150;                  // vertical layer spacing
  const maxColsPerRow = 8;        // cap how wide any single parent's child-band gets
  const orphanMaxCols = 14;       // orphan grid width
  const subtreeGap = 2;           // extra horizontal gap (in slots) between sibling subtrees

  // Build adjacency (parent → children) by class name, then convert to node ids
  const nameToNode = new Map();
  for (const n of Graph.nodes) {
    if (n.data?.name) nameToNode.set(n.data.name, n);
  }

  const childMap = new Map();            // parentId → childNode[]
  const parentId = new Map();            // childId → parentId
  for (const n of Graph.nodes) childMap.set(n.id, []);

  for (const n of Graph.nodes) {
    const pname = n.data?.parent_class_name;
    if (!pname) continue;
    const p = nameToNode.get(pname);
    if (p && p.id !== n.id) {
      childMap.get(p.id).push(n);
      parentId.set(n.id, p.id);
    }
  }

  // Compute slot dimensions for each subtree
  // node._slotW: horizontal width in "slots" (1 slot = H pixels)
  // node._slotH: vertical height in layers (used mainly for orphan spacing)
  // node._rows:  2D array — each row is an array of child nodes (for rendering pass)
  function computeSize(node) {
    const kids = childMap.get(node.id);
    if (!kids.length) { node._slotW = 1; node._slotH = 1; node._rows = []; return; }
    for (const k of kids) computeSize(k);

    // Decide grid width for this parent
    const cols = Math.min(maxColsPerRow, Math.max(1, Math.ceil(Math.sqrt(kids.length * 1.5))));

    // Pack kids into rows, row-by-row
    const rows = [];
    for (let i = 0; i < kids.length; i += cols) rows.push(kids.slice(i, i + cols));

    // Subtree width = widest row (sum of child slotW + gaps for visual separation)
    const rowWidths = rows.map(row => {
      let w = 0;
      for (const k of row) w += k._slotW;
      // small visual gap between children in a row
      w += Math.max(0, row.length - 1) * 0.3;
      return w;
    });
    const subtreeWidth = Math.max(1, ...rowWidths);

    // Subtree height: 1 (for this node) + each row contributes its max child slotH
    let subtreeHeight = 1;
    for (const row of rows) subtreeHeight += Math.max(...row.map(k => k._slotH));

    node._slotW = subtreeWidth;
    node._slotH = subtreeHeight;
    node._rows = rows;
  }

  // Roots for hierarchical tree: everyone without a matched parent
  const rootNodes = Graph.nodes.filter(n => !parentId.has(n.id));
  const m2Roots = rootNodes.filter(n => n.layer === 'm2');
  const orphanM1All = rootNodes.filter(n => n.layer === 'm1');

  for (const r of rootNodes) computeSize(r);

  // Split orphans: those with descendants become independent top-level subtrees
  // (placed alongside M2 roots); pure leaves go into a dense grid on the right.
  const orphanWithKids = orphanM1All.filter(n => (n._rows?.length || 0) > 0);
  const orphanLeaves   = orphanM1All.filter(n => (n._rows?.length || 0) === 0);

  // Top-level subtree roots (M2 + orphan-with-kids), M2 first for visual grouping
  const topRoots = [...m2Roots, ...orphanWithKids];

  // ---- Place top-level subtrees in a single horizontal band ----
  let totalSlots = 0;
  for (const r of topRoots) totalSlots += r._slotW + subtreeGap;
  totalSlots = Math.max(0, totalSlots - subtreeGap);

  let cursorSlot = -totalSlots / 2;
  for (const r of topRoots) {
    placeSubtree(r, (cursorSlot + (r._slotW - 1) / 2) * H, 0);
    cursorSlot += r._slotW + subtreeGap;
  }

  // ---- Place orphan leaves in a dense grid to the right ----
  if (orphanLeaves.length) {
    // Choose column count: aim for a pleasant aspect ratio (slightly landscape)
    const cols = Math.min(orphanMaxCols, Math.max(1, Math.ceil(Math.sqrt(orphanLeaves.length * 1.3))));
    const startX = topRoots.length ? (cursorSlot + 2) * H : 0;  // gap after topRoots
    const startY = 0;
    orphanLeaves.forEach((n, i) => {
      const row = Math.floor(i / cols);
      const col = i % cols;
      n.x = startX + col * H;
      n.y = startY + row * V;
    });
  }

  // Unpin all nodes (user drags are reset by caller when pressing re-layout button)
  graphSavePositions();
  graphRender();
  graphFitToView(true);

  // ---- Recursive tree placement ----
  function placeSubtree(node, x, y) {
    node.x = x;
    node.y = y;
    const rows = node._rows;
    if (!rows || !rows.length) return;

    let rowY = y + V;
    for (const row of rows) {
      // Row total width in slots
      let rowSlotW = 0;
      for (const k of row) rowSlotW += k._slotW;
      rowSlotW += Math.max(0, row.length - 1) * 0.3;

      // Starting X so row is centered under `node`
      let cursorX = x - (rowSlotW - 1) * H / 2;
      for (const child of row) {
        const childCenterX = cursorX + (child._slotW - 1) * H / 2;
        placeSubtree(child, childCenterX, rowY);
        cursorX += (child._slotW + 0.3) * H;
      }
      // Row vertical size = max child subtree height
      const rowHeight = Math.max(...row.map(k => k._slotH));
      rowY += rowHeight * V;
    }
  }
}

// -------- Force-directed layout (kept as alternative) --------
function graphForceLayout() {
  const n = Graph.nodes.length;
  if (n === 0) return;

  // Initial positions: M2 in inner ring, M1 in outer ring, grouped by parent.
  // Ring radius derived from "how much arc each node needs" so we don't start
  // 170 nodes packed on top of each other.
  const m2Nodes = Graph.nodes.filter(x => x.layer === 'm2');
  const m1Nodes = Graph.nodes.filter(x => x.layer === 'm1');

  // avg arc per node = avg diameter + pad
  const avgR = Graph.nodes.length
    ? Graph.nodes.reduce((s, n) => s + n.r, 0) / Graph.nodes.length
    : 25;
  const arcPerNode = 2 * avgR + COLLISION_PAD;

  const m2Arc = m2Nodes.length * arcPerNode;
  const m1Arc = m1Nodes.length * arcPerNode;
  const baseR2 = Math.max(120, m2Arc / (2 * Math.PI) * 1.2);
  const baseR1 = Math.max(baseR2 + 2 * arcPerNode + 40, m1Arc / (2 * Math.PI) * 1.2);

  m2Nodes.forEach((n2, i) => {
    const a = (i / Math.max(m2Nodes.length, 1)) * Math.PI * 2;
    n2.x = Math.cos(a) * baseR2;
    n2.y = Math.sin(a) * baseR2;
  });
  // M1: group near parent if it has one, else distribute on outer ring
  const m1ByParent = new Map();
  for (const n1 of m1Nodes) {
    const p = n1.data.parent_class_name || '_orphan';
    if (!m1ByParent.has(p)) m1ByParent.set(p, []);
    m1ByParent.get(p).push(n1);
  }
  let angleOffset = 0;
  const totalM1 = m1Nodes.length || 1;
  for (const [parentName, group] of m1ByParent.entries()) {
    const parentNode = m2Nodes.find(m => m.data.name === parentName);
    const centerA = parentNode
      ? Math.atan2(parentNode.y, parentNode.x)
      : (angleOffset / totalM1) * Math.PI * 2;
    const spread = (Math.PI * 2) * (group.length / totalM1) * 0.8;
    group.forEach((n1, i) => {
      const a = centerA + (i - (group.length - 1) / 2) / Math.max(group.length, 1) * spread;
      const jitter = (Math.random() - 0.5) * 30;
      n1.x = Math.cos(a) * (baseR1 + jitter);
      n1.y = Math.sin(a) * (baseR1 + jitter);
      angleOffset++;
    });
  }

  // Simulation: repulsion + edge attraction + parent pull + center gravity
  Graph._layoutRunning = true;
  const ITER = 280;
  const repulse = 9000;      // node-node repulsion strength
  const attract = 0.025;     // edge attraction (Hooke)
  const parentPull = 0.008;  // soft pull of M1 toward M2 parent
  const gravity = 0.002;     // soft pull toward origin (prevents drift)
  const damping = 0.85;

  // Adjacency for faster edge iteration
  const adjM2 = new Map();  // m1 node id → parent m2 node
  for (const e of Graph.edges) {
    if (e.kind === 'inherit') adjM2.set(e.source, Graph.nodesById.get(e.target));
  }

  function step() {
    // Repulsion (O(n²) — fine for <500 nodes)
    for (let i = 0; i < n; i++) {
      const a = Graph.nodes[i];
      for (let j = i + 1; j < n; j++) {
        const b = Graph.nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { d2 = 1; dx = Math.random() - 0.5; dy = Math.random() - 0.5; }
        const d = Math.sqrt(d2);
        // Soft-collision: strong push if within pad; hard separation happens
        // in graphResolveCollisions(). The soft part gives smoother dynamics.
        const minDist = a.r + b.r + COLLISION_PAD;
        const f = (d < minDist ? repulse * 3 : repulse) / d2;
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }
    // Edge attraction
    for (const e of Graph.edges) {
      const a = Graph.nodesById.get(e.source);
      const b = Graph.nodesById.get(e.target);
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const f = attract;
      a.vx += dx * f; a.vy += dy * f;
      b.vx -= dx * f; b.vy -= dy * f;
    }
    // Parent pull (M1 toward M2 parent, softer than explicit edge)
    for (const [childId, parentNode] of adjM2) {
      const child = Graph.nodesById.get(childId);
      if (!child) continue;
      child.vx += (parentNode.x - child.x) * parentPull;
      child.vy += (parentNode.y - child.y) * parentPull;
    }
    // Gravity + apply velocity
    for (const node of Graph.nodes) {
      if (node.pinned) { node.vx = node.vy = 0; continue; }
      node.vx -= node.x * gravity;
      node.vy -= node.y * gravity;
      node.vx *= damping;
      node.vy *= damping;
      // Velocity clamp (prevents runaway)
      const speed = Math.sqrt(node.vx * node.vx + node.vy * node.vy);
      if (speed > 40) { node.vx = node.vx / speed * 40; node.vy = node.vy / speed * 40; }
      node.x += node.vx;
      node.y += node.vy;
    }
    // Hard collision resolution — guarantees no overlap at rest.
    // After force integration, any two nodes closer than r_a+r_b+pad are pushed
    // apart along their connecting vector. Multiple passes resolve chain collisions.
    graphResolveCollisions(2);
  }

  let iter = 0;
  const tick = () => {
    for (let s = 0; s < 4; s++) { step(); iter++; if (iter >= ITER) break; }
    graphRender();
    if (iter < ITER && Graph._layoutRunning) {
      Graph._animRAF = requestAnimationFrame(tick);
    } else {
      // Final polish: additional collision-only passes to guarantee clean result
      // (by this point forces are tiny, but leftover overlaps can still exist at
      // densely-connected hubs). Zero velocities so next frame doesn't re-overlap.
      for (const node of Graph.nodes) { node.vx = 0; node.vy = 0; }
      for (let p = 0; p < 12; p++) graphResolveCollisions(2);
      Graph._layoutRunning = false;
      graphRender();
      graphSavePositions();
      graphFitToView(true);
    }
  };
  cancelAnimationFrame(Graph._animRAF);
  tick();
}

// -------- Collision pad (desired gap between node EDGES) --------
// Increase for more breathing room; decrease for denser packing.
// Used by both hard collision resolution AND soft repulsion threshold.
const COLLISION_PAD = 30;

// -------- Hard collision resolution (position-based, spatial hashing) --------
// O(n) via uniform spatial hash grid:
//   - Cell size = 2*maxR + pad  (guarantees any colliding pair is in same or
//     one of 8 adjacent cells)
//   - Each node probes itself + 8 neighbor cells only
//   - Dedup pairs via string id ordering (a.id < b.id)
// `passes` resolves chain collisions where fixing A↔B creates A↔C.
// Pinned nodes never move; free nodes absorb the full separation.
function graphResolveCollisions(passes = 1) {
  const nodes = Graph.nodes;
  const n = nodes.length;
  if (n < 2) return;

  // Size each cell to cover the largest possible collision distance.
  // Any two nodes that collide have centers within maxR_a + maxR_b + pad of each other,
  // so they are guaranteed to share a cell or be in adjacent (±1) cells.
  let maxR = 0;
  for (const node of nodes) if (node.r > maxR) maxR = node.r;
  const cellSize = 2 * maxR + COLLISION_PAD + 1;  // +1 safety margin for float rounding

  for (let pass = 0; pass < passes; pass++) {
    // Rebuild hash each pass (positions shifted between passes).
    // Key is "cx,cy" as a string — simple and correct for any coordinate range
    // (bitwise tricks break because JS ^ truncates to int32).
    const hash = new Map();
    for (const node of nodes) {
      const cx = Math.floor(node.x / cellSize);
      const cy = Math.floor(node.y / cellSize);
      const key = cx + ',' + cy;
      let list = hash.get(key);
      if (!list) { list = []; hash.set(key, list); }
      list.push(node);
    }

    // For each node, probe its cell + 8 neighbors
    for (const a of nodes) {
      const cx = Math.floor(a.x / cellSize);
      const cy = Math.floor(a.y / cellSize);
      for (let ox = -1; ox <= 1; ox++) {
        for (let oy = -1; oy <= 1; oy++) {
          const key = (cx + ox) + ',' + (cy + oy);
          const bucket = hash.get(key);
          if (!bucket) continue;
          for (const b of bucket) {
            // Dedupe: process each unordered pair once
            if (a === b || a.id > b.id) continue;
            let dx = b.x - a.x;
            let dy = b.y - a.y;
            const minDist = a.r + b.r + COLLISION_PAD;
            const d2 = dx * dx + dy * dy;
            if (d2 >= minDist * minDist) continue;
            let d = Math.sqrt(d2);
            if (d < 0.01) {
              dx = Math.random() - 0.5;
              dy = Math.random() - 0.5;
              d = Math.sqrt(dx * dx + dy * dy) || 1;
            }
            const overlap = minDist - d;
            const nx = dx / d, ny = dy / d;
            if (a.pinned && b.pinned) continue;
            if (a.pinned) {
              b.x += nx * overlap; b.y += ny * overlap;
            } else if (b.pinned) {
              a.x -= nx * overlap; a.y -= ny * overlap;
            } else {
              const half = overlap / 2;
              a.x -= nx * half; a.y -= ny * half;
              b.x += nx * half; b.y += ny * half;
            }
          }
        }
      }
    }
  }
}

// -------- Render SVG (nodes + edges) --------
function graphRender() {
  const edgesG = Graph._el.edgesG;
  const nodesG = Graph._el.nodesG;
  edgesG.innerHTML = '';
  nodesG.innerHTML = '';

  // --- edges first (behind nodes) ---
  for (const e of Graph.edges) {
    const a = Graph.nodesById.get(e.source);
    const b = Graph.nodesById.get(e.target);
    if (!a || !b) continue;
    if (!graphEdgeVisible(e, a, b)) continue;

    const { x1, y1, x2, y2 } = graphEdgeEndpoints(a, b);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    // Curved a little for visual clarity
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.max(1, Math.sqrt(dx * dx + dy * dy));
    const curve = Math.min(40, len * 0.12);
    const nx = -dy / len * curve, ny = dx / len * curve;
    path.setAttribute('d', `M ${x1} ${y1} Q ${mx + nx} ${my + ny}, ${x2} ${y2}`);
    path.setAttribute('class', `g-edge ${e.kind}`);
    path.dataset.edgeId = e.id;
    const markerId = {
      inherit: 'gm-arrow-inherit',
      assoc: 'gm-arrow-assoc',
      composition: 'gm-diamond',
      'm2-assoc': 'gm-arrow-m2assoc',
    }[e.kind];
    if (markerId) path.setAttribute('marker-end', `url(#${markerId})`);
    if (graphEdgeHighlighted(e)) path.classList.add('highlighted');
    if (graphEdgeDimmed(e)) path.classList.add('dimmed');
    edgesG.appendChild(path);

    // label on edges (hover-revealed)
    if (e.label) {
      const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      t.setAttribute('x', mx + nx);
      t.setAttribute('y', my + ny - 4);
      t.setAttribute('class', 'g-edge-label');
      t.textContent = e.label;
      edgesG.appendChild(t);
    }
  }

  // --- nodes ---
  for (const n of Graph.nodes) {
    if (!graphNodeVisible(n)) continue;
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    const msCls = n.metaStructureRole ? ` ms-${n.metaStructureRole}` : '';
    g.setAttribute('class', `g-node layer-${n.layer}${n.abstract ? ' is-abstract' : ''}${msCls}`);
    g.setAttribute('transform', `translate(${n.x}, ${n.y})`);
    g.dataset.nodeId = n.id;
    if (n.id === Graph.selectedId) g.classList.add('selected');
    if (graphNodeDimmed(n)) g.classList.add('dimmed');
    if (Graph._drag && Graph._drag.node === n) g.classList.add('dragging');

    // Circle
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('r', n.r);
    circle.setAttribute('class', 'g-node-circle');
    g.appendChild(circle);

    // Attribute-count badge (top-right)
    const attrCount = n.data.attributes?.length || 0;
    if (attrCount > 0) {
      const bx = n.r * 0.7, by = -n.r * 0.7;
      const br = 9;
      const bb = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      bb.setAttribute('cx', bx); bb.setAttribute('cy', by);
      bb.setAttribute('r', br);
      bb.setAttribute('class', 'g-node-badge-bg');
      g.appendChild(bb);
      const bt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      bt.setAttribute('x', bx); bt.setAttribute('y', by);
      bt.setAttribute('class', 'g-node-badge-text');
      bt.textContent = attrCount > 99 ? '99+' : String(attrCount);
      g.appendChild(bt);
    }

    // Label below
    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('y', n.r + 14);
    label.setAttribute('class', 'g-node-label');
    const txt = n.data.label || n.data.name || '';
    label.textContent = txt.length > 18 ? txt.substring(0, 17) + '…' : txt;
    g.appendChild(label);

    nodesG.appendChild(g);
  }

  graphUpdateTransform();
  graphRenderMinimap();
}

// -------- Viewport transform --------
function graphUpdateTransform() {
  const { x, y, k } = Graph.transform;
  Graph._el.viewport.setAttribute('transform', `translate(${x},${y}) scale(${k})`);
  if (Graph._el.zoomPct) Graph._el.zoomPct.textContent = Math.round(k * 100) + '%';
  graphRenderMinimapViewport();
}

// -------- Edge helpers --------
function graphEdgeEndpoints(a, b) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
  const ux = dx / d, uy = dy / d;
  return {
    x1: a.x + ux * a.r,
    y1: a.y + uy * a.r,
    x2: b.x - ux * b.r,
    y2: b.y - uy * b.r,
  };
}

function graphEdgeVisible(e, a, b) {
  if (!graphNodeVisible(a) || !graphNodeVisible(b)) return false;
  if (e.kind === 'inherit' && !Graph.visible.inherit) return false;
  if ((e.kind === 'assoc' || e.kind === 'composition' || e.kind === 'm2-assoc') && !Graph.visible.assoc) return false;
  return true;
}

function graphEdgeHighlighted(e) {
  const sel = Graph.selectedId || Graph.hoveredId;
  if (!sel) return false;
  return e.source === sel || e.target === sel;
}

function graphEdgeDimmed(e) {
  const sel = Graph.selectedId;
  if (!sel) return false;
  return !(e.source === sel || e.target === sel);
}

function graphNodeVisible(n) {
  if (n.layer === 'm2' && !Graph.visible.m2) return false;
  if (n.layer === 'm1' && !Graph.visible.m1) return false;
  if (Graph.searchQuery) {
    const q = Graph.searchQuery.toLowerCase();
    const hay = ((n.data.name || '') + ' ' + (n.data.label || '') + ' ' + (n.data.description || '')).toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

function graphNodeDimmed(n) {
  const sel = Graph.selectedId;
  if (!sel) return false;
  if (n.id === sel) return false;
  // Highlight direct neighbors
  for (const e of Graph.edges) {
    if (e.source === sel && e.target === n.id) return false;
    if (e.target === sel && e.source === n.id) return false;
  }
  return true;
}

// -------- Empty state --------
function graphShowEmpty() {
  const e = Graph._el.empty;
  const m1Count = state.m1Model?.versions?.slice(-1)[0]?.package?.classes?.length || 0;
  const m2Count = state.m2Model?.versions?.slice(-1)[0]?.package?.classes?.length || 0;
  let content;
  if (m1Count === 0 && m2Count === 0) {
    content = `<div class="ge-icon">🔭</div>
      <div class="ge-title">还没有可视化的模型</div>
      <div class="ge-hint">先上传业务文档 → 点击「AI提取M1」生成 M1 领域模型；之后可选择反推 M2 元模型。节点和关系会在这里交互展示。</div>`;
  } else {
    content = `<div class="ge-icon">📦</div>
      <div class="ge-title">模型已加载，但暂无可见节点</div>
      <div class="ge-hint">顶部筛选条可能全部关闭，或搜索没有匹配结果。试试点击 M1/M2 芯片重新开启。</div>`;
  }
  e.innerHTML = content;
  e.classList.remove('hidden');
}
function graphHideEmpty() { Graph._el.empty.classList.add('hidden'); }

// -------- Interactions: pan / zoom / drag --------
function graphSetupInteractions() {
  const svg = Graph._el.svg;

  // Wheel zoom (center on cursor)
  svg.addEventListener('wheel', ev => {
    ev.preventDefault();
    const rect = svg.getBoundingClientRect();
    const sx = ev.clientX - rect.left;
    const sy = ev.clientY - rect.top;
    const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    graphZoomAtScreen(sx, sy, factor);
  }, { passive: false });

  // Mouse-down: start pan or node drag
  svg.addEventListener('mousedown', ev => {
    if (ev.button !== 0) return;
    const nodeEl = ev.target.closest('.g-node');
    if (nodeEl) {
      const n = Graph.nodesById.get(nodeEl.dataset.nodeId);
      if (!n) return;
      Graph._drag = {
        node: n,
        startX: ev.clientX,
        startY: ev.clientY,
        origX: n.x,
        origY: n.y,
        moved: false,
      };
      n.pinned = true;
      svg.classList.add('dragging');
      graphHideTooltip();
    } else {
      // Pan
      Graph._pan = {
        startX: ev.clientX,
        startY: ev.clientY,
        origX: Graph.transform.x,
        origY: Graph.transform.y,
      };
      svg.classList.add('panning');
    }
  });

  // Mouse-move: update pan/drag
  window.addEventListener('mousemove', ev => {
    if (Graph._drag) {
      const d = Graph._drag;
      const dx = (ev.clientX - d.startX) / Graph.transform.k;
      const dy = (ev.clientY - d.startY) / Graph.transform.k;
      d.node.x = d.origX + dx;
      d.node.y = d.origY + dy;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) d.moved = true;
      // Push stationary nodes out of the way as the dragged node sweeps through.
      // Dragged node is pinned (set on mousedown), so resolution only moves others.
      if (d.moved) graphResolveCollisions(1);
      graphRender();
    } else if (Graph._pan) {
      const p = Graph._pan;
      Graph.transform.x = p.origX + (ev.clientX - p.startX);
      Graph.transform.y = p.origY + (ev.clientY - p.startY);
      graphUpdateTransform();
    } else {
      // Hover tooltip
      const nodeEl = ev.target.closest?.('.g-node');
      if (nodeEl) {
        const id = nodeEl.dataset.nodeId;
        if (Graph.hoveredId !== id) {
          Graph.hoveredId = id;
          graphShowTooltip(id, ev.clientX, ev.clientY);
        } else {
          graphPositionTooltip(ev.clientX, ev.clientY);
        }
      } else {
        if (Graph.hoveredId) {
          Graph.hoveredId = null;
          graphHideTooltip();
        }
      }
    }
  });

  // Mouse-up: end pan/drag + click detection
  window.addEventListener('mouseup', ev => {
    if (Graph._drag) {
      const d = Graph._drag;
      Graph._el.svg.classList.remove('dragging');
      if (!d.moved) {
        // Treat as click on node
        graphSelectNode(d.node.id === Graph.selectedId ? null : d.node.id);
      } else {
        // If dropped onto other nodes, push them away (drag target stays put —
        // keep it pinned temporarily so resolution pushes only neighbors, then unpin)
        const wasPinned = d.node.pinned;
        d.node.pinned = true;
        for (let p = 0; p < 6; p++) graphResolveCollisions(2);
        d.node.pinned = wasPinned;
        graphRender();
        graphSavePositions();
      }
      Graph._drag = null;
    }
    if (Graph._pan) {
      Graph._el.svg.classList.remove('panning');
      Graph._pan = null;
    }
  });

  // Double-click: on node = open detail; on empty = fit to view
  svg.addEventListener('dblclick', ev => {
    const nodeEl = ev.target.closest?.('.g-node');
    if (nodeEl) {
      const n = Graph.nodesById.get(nodeEl.dataset.nodeId);
      if (n) graphOpenDetail(n);
    } else {
      graphFitToView(false);
    }
  });

  // Click empty space deselects
  svg.addEventListener('click', ev => {
    const nodeEl = ev.target.closest?.('.g-node');
    if (!nodeEl && Graph.selectedId) {
      graphSelectNode(null);
    }
  });

  // Minimap: click/drag to pan view
  const mini = Graph._el.minimap;
  mini.addEventListener('mousedown', ev => {
    const rect = mini.getBoundingClientRect();
    const onMove = e2 => {
      const mx = e2.clientX - rect.left;
      const my = e2.clientY - rect.top;
      graphPanToMiniCoord(mx, my);
    };
    onMove(ev);
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });
}

function graphZoomAtScreen(sx, sy, factor) {
  const newK = Math.max(0.15, Math.min(4, Graph.transform.k * factor));
  // Translate so point under cursor stays under cursor
  const wx = (sx - Graph.transform.x) / Graph.transform.k;
  const wy = (sy - Graph.transform.y) / Graph.transform.k;
  Graph.transform.k = newK;
  Graph.transform.x = sx - wx * newK;
  Graph.transform.y = sy - wy * newK;
  graphUpdateTransform();
}

function graphZoomCenter(factor) {
  const rect = Graph._el.svg.getBoundingClientRect();
  graphZoomAtScreen(rect.width / 2, rect.height / 2, factor);
}

function graphFitToView(instant = false) {
  if (!Graph.nodes.length) return;
  const visibleNodes = Graph.nodes.filter(graphNodeVisible);
  if (!visibleNodes.length) return;

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of visibleNodes) {
    minX = Math.min(minX, n.x - n.r - 30);
    minY = Math.min(minY, n.y - n.r - 30);
    maxX = Math.max(maxX, n.x + n.r + 30);
    maxY = Math.max(maxY, n.y + n.r + 30);
  }
  const rect = Graph._el.svg.getBoundingClientRect();
  const bw = maxX - minX;
  const bh = maxY - minY;
  const k = Math.min(rect.width / bw, rect.height / bh, 1.8);
  Graph.transform.k = Math.max(0.2, k * 0.9);
  Graph.transform.x = rect.width / 2 - (minX + bw / 2) * Graph.transform.k;
  Graph.transform.y = rect.height / 2 - (minY + bh / 2) * Graph.transform.k;
  graphUpdateTransform();
}

function graphResetZoom() {
  const rect = Graph._el.svg.getBoundingClientRect();
  Graph.transform.k = 1;
  Graph.transform.x = rect.width / 2;
  Graph.transform.y = rect.height / 2;
  graphUpdateTransform();
}

// -------- Selection / neighborhood highlight --------
function graphSelectNode(id) {
  Graph.selectedId = id;
  graphRender();
  if (id) {
    const n = Graph.nodesById.get(id);
    const neigh = graphNeighbors(id);
    Graph._el.selinfo.classList.remove('hidden');
    Graph._el.selinfo.innerHTML = `
      <strong>${escapeHtml(n.data.name || '')}</strong>
      · ${n.layer.toUpperCase()}
      · ${neigh.length} 个相邻节点
      <button class="gs-clear" id="gs-clear-btn">✕ 取消</button>`;
    Graph._el.selinfo.querySelector('#gs-clear-btn').onclick = () => graphSelectNode(null);
  } else {
    Graph._el.selinfo.classList.add('hidden');
  }
}

function graphNeighbors(id) {
  const set = new Set();
  for (const e of Graph.edges) {
    if (e.source === id) set.add(e.target);
    if (e.target === id) set.add(e.source);
  }
  return [...set];
}

// -------- Tooltip --------
function graphShowTooltip(id, screenX, screenY) {
  const n = Graph.nodesById.get(id);
  if (!n) return;
  const tip = Graph._el.tooltip;
  const cls = n.data;

  const attrs = cls.attributes || [];
  const ownAttrs = attrs.filter(a => !a.is_inherited).length;
  const inherited = attrs.length - ownAttrs;

  // Parent chain
  const parentName = cls.parent_class_name;
  let parentHtml = '';
  if (parentName) {
    const parentNode = [...Graph.nodesById.values()].find(x => x.data.name === parentName);
    parentHtml = parentNode
      ? `<span class="gt-link gt-link-m2" data-jump="${parentNode.id}">↑ ${escapeHtml(parentName)}</span>`
      : `<span class="gt-link">↑ ${escapeHtml(parentName)}</span>`;
  }

  // Children (classes that extend this one)
  const children = [...Graph.nodesById.values()].filter(x =>
    x.data.parent_class_name === cls.name);

  // Associations touching this node
  const relatedEdges = Graph.edges.filter(e => e.source === id || e.target === id);
  const inheritEdges = relatedEdges.filter(e => e.kind === 'inherit');
  const assocEdges = relatedEdges.filter(e => e.kind !== 'inherit');

  tip.innerHTML = `
    <div class="gt-header">
      <span class="gt-layer-badge ${n.layer}">${n.layer.toUpperCase()}</span>
      <span class="gt-name">${escapeHtml(cls.name || '')}</span>
    </div>
    ${cls.label ? `<div class="gt-label">${escapeHtml(cls.label)}${cls.is_abstract ? ' (abstract)' : ''}</div>` : ''}
    ${cls.description ? `<div class="gt-desc">${escapeHtml(cls.description)}</div>` : ''}
    <div class="gt-stats">
      <div class="gt-stat"><div class="gt-stat-num">${ownAttrs}</div><div class="gt-stat-label">自有属性</div></div>
      ${inherited > 0 ? `<div class="gt-stat"><div class="gt-stat-num">${inherited}</div><div class="gt-stat-label">继承属性</div></div>` : ''}
      <div class="gt-stat"><div class="gt-stat-num">${assocEdges.length}</div><div class="gt-stat-label">关联</div></div>
      <div class="gt-stat"><div class="gt-stat-num">${children.length}</div><div class="gt-stat-label">子类</div></div>
    </div>
    ${parentHtml ? `<div class="gt-section"><div class="gt-section-title">父类</div>${parentHtml}</div>` : ''}
    ${children.length ? `<div class="gt-section"><div class="gt-section-title">子类 (${children.length})</div><div class="gt-list">${
      children.slice(0, 8).map(c => `<span class="gt-list-item" data-jump="${c.id}">${escapeHtml(c.data.name)}</span>`).join('')
    }${children.length > 8 ? `<span class="gt-list-item" style="cursor:default">+${children.length - 8}</span>` : ''}</div></div>` : ''}
    <div class="gt-footer">
      <span class="gt-footer-hint">双击节点查看详情</span>
      <span class="gt-footer-action" data-open="${id}">详情 →</span>
    </div>`;

  // Wire jump links
  tip.querySelectorAll('[data-jump]').forEach(el => {
    el.addEventListener('click', ev => {
      ev.stopPropagation();
      const targetId = el.dataset.jump;
      graphCenterOn(targetId);
      graphSelectNode(targetId);
      graphHideTooltip();
    });
  });
  tip.querySelectorAll('[data-open]').forEach(el => {
    el.addEventListener('click', ev => {
      ev.stopPropagation();
      graphOpenDetail(Graph.nodesById.get(el.dataset.open));
    });
  });

  tip.classList.remove('hidden');
  graphPositionTooltip(screenX, screenY);
}

function graphPositionTooltip(x, y) {
  const tip = Graph._el.tooltip;
  const rect = Graph._el.root.getBoundingClientRect();
  const tipW = tip.offsetWidth;
  const tipH = tip.offsetHeight;
  let px = x - rect.left + 16;
  let py = y - rect.top + 16;
  if (px + tipW > rect.width - 10) px = x - rect.left - tipW - 16;
  if (py + tipH > rect.height - 10) py = y - rect.top - tipH - 16;
  if (px < 8) px = 8;
  if (py < 8) py = 8;
  tip.style.left = px + 'px';
  tip.style.top  = py + 'px';
}

function graphHideTooltip() {
  Graph._el.tooltip.classList.add('hidden');
}

function graphCenterOn(id) {
  const n = Graph.nodesById.get(id);
  if (!n) return;
  const rect = Graph._el.svg.getBoundingClientRect();
  Graph.transform.x = rect.width / 2 - n.x * Graph.transform.k;
  Graph.transform.y = rect.height / 2 - n.y * Graph.transform.k;
  graphUpdateTransform();
}

function graphOpenDetail(n) {
  if (!n) return;
  const layer = n.layer;
  const model = layer === 'm2' ? state.m2Model : state.m1Model;
  const pkg = model?.versions?.slice(-1)[0]?.package;
  if (!pkg) return;
  const cls = pkg.classes.find(c => c.id === n.data.id);
  if (!cls) return;
  openEntityDetailPage(cls, pkg.classes, pkg.enumerations || [], pkg.associations || [], layer);
}

// -------- Toolbar --------
function graphSetupToolbar() {
  document.getElementById('btn-graph-layout').addEventListener('click', () => {
    // Unpin all, clear cached positions for this model, then re-layout from scratch
    for (const n of Graph.nodes) n.pinned = false;
    delete Graph.positionsByModel[graphModelKey()];
    graphAutoLayout();
  });

  // Layout mode toggle buttons
  document.querySelectorAll('.graph-layout-mode').forEach(btn => {
    btn.addEventListener('click', () => {
      const mode = btn.dataset.mode;
      if (Graph.layoutMode === mode) return;
      Graph.layoutMode = mode;
      document.querySelectorAll('.graph-layout-mode').forEach(b =>
        b.classList.toggle('active', b.dataset.mode === mode));
      // Clear cache and re-layout
      for (const n of Graph.nodes) n.pinned = false;
      delete Graph.positionsByModel[graphModelKey()];
      graphAutoLayout();
    });
  });
  document.getElementById('btn-graph-fit').addEventListener('click', () => graphFitToView(false));
  document.getElementById('btn-graph-reset').addEventListener('click', () => graphResetZoom());
  document.getElementById('btn-graph-zoom-in').addEventListener('click', () => graphZoomCenter(1.2));
  document.getElementById('btn-graph-zoom-out').addEventListener('click', () => graphZoomCenter(1 / 1.2));

  Graph._el.search.addEventListener('input', ev => {
    Graph.searchQuery = ev.target.value.trim();
    graphRender();
  });
  Graph._el.search.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') {
      const match = Graph.nodes.find(graphNodeVisible);
      if (match) graphCenterOn(match.id);
    } else if (ev.key === 'Escape') {
      ev.target.value = '';
      Graph.searchQuery = '';
      graphRender();
    }
  });

  document.querySelectorAll('.graph-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const k = chip.dataset.layer || chip.dataset.edge;
      Graph.visible[k] = !Graph.visible[k];
      chip.classList.toggle('active', Graph.visible[k]);
      graphRender();
    });
  });
}

// -------- Keyboard shortcuts --------
function graphSetupKeyboard() {
  document.addEventListener('keydown', ev => {
    // Only when diagram tab is active and no input is focused
    const diagramActive = document.querySelector('#diagram-view.active, #diagram-view.tab-content.active');
    if (!diagramActive || document.activeElement?.tagName === 'INPUT') return;
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;

    switch (ev.key.toLowerCase()) {
      case 'f':  ev.preventDefault(); graphFitToView(false); break;
      case '0':  ev.preventDefault(); graphResetZoom(); break;
      case 'l':  ev.preventDefault(); for (const n of Graph.nodes) n.pinned = false; graphAutoLayout(); break;
      case '/':  ev.preventDefault(); Graph._el.search?.focus(); break;
      case 'escape': ev.preventDefault(); graphSelectNode(null); break;
    }
  });
}

// -------- Minimap --------
function graphRenderMinimap() {
  const mini = Graph._el.minimap;
  const miniNodes = Graph._el.miniNodes;
  miniNodes.innerHTML = '';
  if (!Graph.nodes.length) return;

  // Compute bounds of all nodes
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of Graph.nodes) {
    minX = Math.min(minX, n.x - n.r); minY = Math.min(minY, n.y - n.r);
    maxX = Math.max(maxX, n.x + n.r); maxY = Math.max(maxY, n.y + n.r);
  }
  const pad = 20;
  minX -= pad; minY -= pad; maxX += pad; maxY += pad;
  const bw = Math.max(1, maxX - minX);
  const bh = Math.max(1, maxY - minY);
  const miniRect = mini.getBoundingClientRect();
  const scale = Math.min(miniRect.width / bw, miniRect.height / bh);
  const offsetX = (miniRect.width - bw * scale) / 2 - minX * scale;
  const offsetY = (miniRect.height - bh * scale) / 2 - minY * scale;

  // Store for minimap interactions
  Graph._miniGeom = { scale, offsetX, offsetY, minX, minY, bw, bh };

  for (const n of Graph.nodes) {
    if (!graphNodeVisible(n)) continue;
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', n.x * scale + offsetX);
    c.setAttribute('cy', n.y * scale + offsetY);
    c.setAttribute('r', Math.max(1.5, n.r * scale));
    c.setAttribute('fill', LAYER_COLORS[n.layer]);
    c.setAttribute('opacity', '0.7');
    c.setAttribute('class', 'g-mini-node');
    miniNodes.appendChild(c);
  }
  graphRenderMinimapViewport();
}

function graphRenderMinimapViewport() {
  if (!Graph._miniGeom) return;
  const { scale, offsetX, offsetY } = Graph._miniGeom;
  const svgRect = Graph._el.svg.getBoundingClientRect();
  // World-space visible region = inverse of current transform applied to (0,0) and (w,h)
  const k = Graph.transform.k;
  const wx1 = (0 - Graph.transform.x) / k;
  const wy1 = (0 - Graph.transform.y) / k;
  const wx2 = (svgRect.width - Graph.transform.x) / k;
  const wy2 = (svgRect.height - Graph.transform.y) / k;
  const r = Graph._el.miniView;
  r.setAttribute('x', wx1 * scale + offsetX);
  r.setAttribute('y', wy1 * scale + offsetY);
  r.setAttribute('width',  (wx2 - wx1) * scale);
  r.setAttribute('height', (wy2 - wy1) * scale);
}

function graphPanToMiniCoord(mx, my) {
  if (!Graph._miniGeom) return;
  const { scale, offsetX, offsetY } = Graph._miniGeom;
  // world coord under mini click
  const wx = (mx - offsetX) / scale;
  const wy = (my - offsetY) / scale;
  const svgRect = Graph._el.svg.getBoundingClientRect();
  Graph.transform.x = svgRect.width / 2 - wx * Graph.transform.k;
  Graph.transform.y = svgRect.height / 2 - wy * Graph.transform.k;
  graphUpdateTransform();
}



// ============================================================
// LLM Settings
// ============================================================

let llmPresets = {};
let llmProviders = [];
let llmEditingId = null; // null = new, string = existing

function setupLLMSettings() {
  document.getElementById('btn-llm-settings').addEventListener('click', openLLMModal);
  document.getElementById('btn-llm-close').addEventListener('click', closeLLMModal);
  document.getElementById('btn-llm-add').addEventListener('click', () => newLLMForm());

  // LLM tab switching (config vs stats)
  document.querySelectorAll('.llm-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.llm-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const target = tab.dataset.llmtab;
      document.querySelectorAll('.llm-tab-content').forEach(c => c.style.display = 'none');
      document.querySelectorAll(`.llm-tab-content[data-llmtab="${target}"]`).forEach(c => c.style.display = '');
      if (target === 'stats') loadLLMStats();
    });
  });
  document.getElementById('btn-llm-save').addEventListener('click', saveLLMProvider);
  document.getElementById('btn-llm-delete').addEventListener('click', deleteLLMProvider);
  document.getElementById('btn-llm-test').addEventListener('click', testLLMProvider);

  // Provider dropdown change: fill defaults
  document.getElementById('llm-provider').addEventListener('change', e => {
    const preset = llmPresets[e.target.value];
    if (!preset) return;
    const baseUrl = document.getElementById('llm-baseurl');
    const model = document.getElementById('llm-model');
    const suggestions = document.getElementById('llm-model-suggestions');

    if (preset.default_base_url && !baseUrl.value) baseUrl.value = preset.default_base_url;
    if (preset.placeholder_url) baseUrl.placeholder = preset.placeholder_url;
    if (preset.default_model && !model.value) model.value = preset.default_model;

    // Fill model suggestions
    suggestions.innerHTML = '';
    for (const m of (preset.models || [])) {
      const opt = document.createElement('option');
      opt.value = m;
      suggestions.appendChild(opt);
    }

    // If no_api_key, clear placeholder
    if (preset.no_api_key) {
      document.getElementById('llm-apikey').placeholder = '(本地部署无需填写)';
    } else {
      document.getElementById('llm-apikey').placeholder = 'sk-...';
    }
  });
}

async function openLLMModal() {
  const modal = document.getElementById('llm-modal');
  modal.classList.remove('hidden');

  // Load presets and providers
  try {
    const presetsRes = await API.getLLMPresets();
    llmPresets = presetsRes.presets;
    const sel = document.getElementById('llm-provider');
    sel.innerHTML = '<option value="">-- 选择服务商 --</option>';
    for (const [key, preset] of Object.entries(llmPresets)) {
      const opt = document.createElement('option');
      opt.value = key;
      opt.textContent = preset.label;
      sel.appendChild(opt);
    }
  } catch {}

  await refreshLLMList();
}

function closeLLMModal() {
  document.getElementById('llm-modal').classList.add('hidden');
}

async function refreshLLMList() {
  try {
    const res = await API.listLLMProviders();
    llmProviders = res.providers;
  } catch { llmProviders = []; }

  const list = document.getElementById('llm-provider-list');
  list.innerHTML = '';

  for (const p of llmProviders) {
    const li = document.createElement('li');
    const presetLabel = llmPresets[p.provider]?.label || p.provider;
    li.className = `llm-item${p.is_active ? ' active-provider' : ''}${p.id === llmEditingId ? ' selected' : ''}`;
    li.innerHTML = `
      <div class="llm-item-info">
        <div class="llm-item-name">${p.name}</div>
        <div class="llm-item-sub">${presetLabel} / ${p.model}</div>
      </div>`;

    li.addEventListener('click', () => editLLMProvider(p));

    // Double-click to activate
    li.addEventListener('dblclick', async () => {
      await API.activateLLMProvider(p.id);
      await refreshLLMList();
    });

    list.appendChild(li);
  }

  if (!llmProviders.length) {
    list.innerHTML = '<li class="llm-item"><div class="llm-item-sub">暂无配置，点击 "+ 新增"</div></li>';
  }
}

function newLLMForm() {
  llmEditingId = null;
  const form = document.getElementById('llm-form');
  form.classList.remove('hidden');
  document.getElementById('llm-form-empty').classList.add('hidden');
  document.getElementById('btn-llm-delete').classList.add('hidden');
  clearLLMForm();
}

function editLLMProvider(p) {
  llmEditingId = p.id;
  const form = document.getElementById('llm-form');
  form.classList.remove('hidden');
  document.getElementById('llm-form-empty').classList.add('hidden');
  document.getElementById('btn-llm-delete').classList.remove('hidden');

  document.getElementById('llm-name').value = p.name || '';
  document.getElementById('llm-provider').value = p.provider || '';
  document.getElementById('llm-apikey').value = p.api_key || '';
  document.getElementById('llm-baseurl').value = p.base_url || '';
  document.getElementById('llm-model').value = p.model || '';
  document.getElementById('llm-temp').value = p.temperature ?? 0;
  document.getElementById('llm-maxtokens').value = p.max_tokens ?? 4096;
  document.getElementById('llm-topp').value = p.top_p ?? 1;
  document.getElementById('llm-timeout').value = p.timeout ?? 120;
  document.getElementById('llm-batchchars').value = p.batch_max_chars ?? 8000;
  document.getElementById('llm-notes').value = p.notes || '';

  // Trigger provider change to fill suggestions
  document.getElementById('llm-provider').dispatchEvent(new Event('change'));

  // Highlight in list
  document.querySelectorAll('.llm-item').forEach(i => i.classList.remove('selected'));
  const items = document.querySelectorAll('.llm-item');
  const idx = llmProviders.findIndex(x => x.id === p.id);
  if (idx >= 0 && items[idx]) items[idx].classList.add('selected');

  hideTestResult();
}

function clearLLMForm() {
  document.getElementById('llm-name').value = '';
  document.getElementById('llm-provider').value = '';
  document.getElementById('llm-apikey').value = '';
  document.getElementById('llm-baseurl').value = '';
  document.getElementById('llm-model').value = '';
  document.getElementById('llm-temp').value = '0';
  document.getElementById('llm-maxtokens').value = '4096';
  document.getElementById('llm-topp').value = '1';
  document.getElementById('llm-timeout').value = '120';
  document.getElementById('llm-batchchars').value = '8000';
  document.getElementById('llm-notes').value = '';
  hideTestResult();
}

function collectLLMForm() {
  return {
    id: llmEditingId || '',
    name: document.getElementById('llm-name').value.trim(),
    provider: document.getElementById('llm-provider').value,
    api_key: document.getElementById('llm-apikey').value,
    base_url: document.getElementById('llm-baseurl').value.trim() || null,
    model: document.getElementById('llm-model').value.trim(),
    temperature: parseFloat(document.getElementById('llm-temp').value) || 0,
    max_tokens: parseInt(document.getElementById('llm-maxtokens').value) || 4096,
    top_p: parseFloat(document.getElementById('llm-topp').value) || 1,
    timeout: parseInt(document.getElementById('llm-timeout').value) || 120,
    batch_max_chars: Math.max(2000, Math.min(40000,
      parseInt(document.getElementById('llm-batchchars').value) || 8000)),
    notes: document.getElementById('llm-notes').value.trim() || null,
    is_active: false,
  };
}

async function saveLLMProvider() {
  const data = collectLLMForm();
  if (!data.name) { await showDialog({ type: 'warning', title: '配置不完整', message: '请输入配置名称' }); return; }
  if (!data.provider) { await showDialog({ type: 'warning', title: '配置不完整', message: '请选择服务商' }); return; }
  if (!data.model) { await showDialog({ type: 'warning', title: '配置不完整', message: '请输入模型名称' }); return; }

  try {
    if (llmEditingId) {
      await API.updateLLMProvider(llmEditingId, data);
    } else {
      const res = await API.createLLMProvider(data);
      llmEditingId = res.id;
    }
    await refreshLLMList();
    const updated = llmProviders.find(p => p.id === llmEditingId);
    if (updated) editLLMProvider(updated);
  } catch (e) { await showDialog({ type: 'error', title: '保存失败', message: e.message }); }
}

async function deleteLLMProvider() {
  if (!llmEditingId) return;
  const ok = await showDialog({
    type: 'danger', title: '删除LLM配置',
    message: '确定删除此 LLM 配置吗？此操作无法恢复。',
    okText: '删除', danger: true,
  });
  if (!ok) return;
  try {
    await API.deleteLLMProvider(llmEditingId);
    llmEditingId = null;
    document.getElementById('llm-form').classList.add('hidden');
    document.getElementById('llm-form-empty').classList.remove('hidden');
    await refreshLLMList();
  } catch (e) { showDialog({ type: 'error', title: '删除失败', message: e.message }); }
}

async function testLLMProvider() {
  const resultDiv = document.getElementById('llm-test-result');
  resultDiv.className = 'llm-test-result testing';
  resultDiv.textContent = '正在测试连接...';
  resultDiv.classList.remove('hidden');

  try {
    let result;
    if (llmEditingId) {
      // Save first, then test saved config
      await saveLLMProvider();
      result = await API.testLLMProvider(llmEditingId);
    } else {
      // Test unsaved config
      const data = collectLLMForm();
      result = await API.testLLMUnsaved(data);
    }

    if (result.success) {
      resultDiv.className = 'llm-test-result success';
      resultDiv.textContent = result.message;
    } else {
      resultDiv.className = 'llm-test-result failure';
      resultDiv.textContent = result.message;
    }
  } catch (e) {
    resultDiv.className = 'llm-test-result failure';
    resultDiv.textContent = '测试失败: ' + e.message;
  }
}

function hideTestResult() {
  document.getElementById('llm-test-result').classList.add('hidden');
}

// ---- LLM Stats Dashboard ----

async function loadLLMStats() {
  const container = document.getElementById('llm-stats-content');
  try {
    const s = await API.getLLMStats();
    if (!s.total_calls) {
      container.innerHTML = '<p style="color:var(--text-dim);text-align:center;padding:40px">暂无调用记录</p>';
      return;
    }

    // Stats cards
    let html = `<div class="stats-grid">
      <div class="stats-card"><div class="stats-card-num" style="color:var(--accent)">${s.total_calls}</div><div class="stats-card-label">总调用</div></div>
      <div class="stats-card"><div class="stats-card-num" style="color:var(--green)">${s.success_rate}%</div><div class="stats-card-label">成功率</div></div>
      <div class="stats-card"><div class="stats-card-num" style="color:var(--yellow)">${s.avg_duration_s}s</div><div class="stats-card-label">平均响应</div></div>
      <div class="stats-card"><div class="stats-card-num" style="color:var(--purple)">${(s.estimated_tokens/1000).toFixed(1)}K</div><div class="stats-card-label">估算Tokens</div></div>
    </div>`;

    // Totals
    html += `<div class="stats-grid">
      <div class="stats-card"><div class="stats-card-num" style="font-size:16px;color:var(--green)">${s.success_calls}</div><div class="stats-card-label">成功</div></div>
      <div class="stats-card"><div class="stats-card-num" style="font-size:16px;color:var(--red)">${s.failed_calls}</div><div class="stats-card-label">失败</div></div>
      <div class="stats-card"><div class="stats-card-num" style="font-size:16px">${(s.total_prompt_chars/1000).toFixed(0)}K</div><div class="stats-card-label">输入字符</div></div>
      <div class="stats-card"><div class="stats-card-num" style="font-size:16px">${(s.total_response_chars/1000).toFixed(0)}K</div><div class="stats-card-label">输出字符</div></div>
    </div>`;

    // Hourly trend
    if (s.hourly_trend.length > 1) {
      const maxCalls = Math.max(...s.hourly_trend.map(h => h.calls), 1);
      html += '<div class="stats-section-title">调用趋势</div>';
      html += '<div class="stats-bar-chart">';
      for (const h of s.hourly_trend.slice(-20)) {
        const pct = (h.calls / maxCalls * 100);
        html += `<div class="stats-bar" style="height:${pct}%" title="${h.hour}: ${h.calls}次"><div class="stats-bar-label">${h.hour.split(' ')[1] || ''}</div></div>`;
      }
      html += '</div><div style="height:20px"></div>';
    }

    // Model breakdown
    html += '<div class="stats-section-title">模型分布</div>';
    html += '<table class="stats-table"><tr><th>模型</th><th>调用</th><th>成功</th><th>平均耗时</th></tr>';
    for (const [model, data] of Object.entries(s.by_model)) {
      const avg = data.calls > 0 ? (data.total_duration / data.calls).toFixed(1) : '-';
      html += `<tr><td class="mono">${model}</td><td>${data.calls}</td><td class="success-text">${data.success}</td><td class="mono">${avg}s</td></tr>`;
    }
    html += '</table>';

    // Recent calls
    html += '<div class="stats-section-title">最近调用</div>';
    html += '<table class="stats-table"><tr><th>时间</th><th>模型</th><th>耗时</th><th>输入</th><th>状态</th></tr>';
    for (const r of (s.recent_calls || []).slice(0, 15)) {
      const time = r.timestamp?.split('T')[1]?.substring(0, 8) || '';
      const status = r.success ? '<span class="success-text">✓</span>' : `<span class="error-text" title="${r.error || ''}">✗</span>`;
      html += `<tr><td class="mono">${time}</td><td class="mono">${r.model}</td><td class="mono">${r.duration_s}s</td><td class="mono">${(r.prompt_chars/1000).toFixed(1)}K</td><td>${status}</td></tr>`;
    }
    html += '</table>';

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<p style="color:var(--red);padding:20px">加载统计失败: ${e.message}</p>`;
  }
}

// ============================================================
// Feature 2: Minimize/Restore Progress
// ============================================================

let _progressMinimized = false;
let _currentTaskId = null;  // Track current extraction task for cancel

function setupEntityDetailPage() {
  document.getElementById('btn-ed-close').addEventListener('click', () => {
    document.getElementById('entity-detail-overlay').classList.add('hidden');
    _detailHistory.length = 0;  // clear history
  });
  // "返回" crumb
  document.addEventListener('click', e => {
    if (e.target.classList && e.target.classList.contains('ed-crumb') && e.target.dataset.action === 'back') {
      _detailHistory.pop();  // remove current
      const prev = _detailHistory.pop();  // get previous
      if (prev) {
        const m = prev.layer === 'm2' ? state.m2Model : state.m1Model;
        const pkg = m?.versions?.slice(-1)[0]?.package;
        const cls = pkg?.classes?.find(c => c.id === prev.clsId);
        if (cls) openEntityDetailPage(cls, pkg.classes, pkg.enumerations || [], pkg.associations || [], prev.layer, { fromBack: true });
      } else {
        document.getElementById('entity-detail-overlay').classList.add('hidden');
      }
    }
  });
}

function setupProgressMinimize() {
  document.getElementById('btn-progress-minimize').addEventListener('click', minimizeProgress);
  document.getElementById('progress-badge').addEventListener('click', e => {
    if (e.target.id === 'btn-badge-cancel') return;
    restoreProgress();
  });
  document.getElementById('btn-progress-cancel').addEventListener('click', cancelExtraction);
  document.getElementById('btn-badge-cancel').addEventListener('click', cancelExtraction);
  document.getElementById('btn-progress-start').addEventListener('click', startExtractionTask);
  document.getElementById('btn-progress-pause').addEventListener('click', pauseExtractionTask);
  document.getElementById('btn-progress-resume').addEventListener('click', resumeExtractionTask);
}

function updateProgressControlButtons(state) {
  // state: 'ready' | 'running' | 'paused' | 'done'
  const start = document.getElementById('btn-progress-start');
  const pause = document.getElementById('btn-progress-pause');
  const resume = document.getElementById('btn-progress-resume');
  const cancel = document.getElementById('btn-progress-cancel');
  // Hide all first
  [start, pause, resume, cancel].forEach(b => b && b.classList.add('hidden'));
  if (state === 'ready') {
    start.classList.remove('hidden');
    cancel.classList.remove('hidden');
  } else if (state === 'running') {
    pause.classList.remove('hidden');
    cancel.classList.remove('hidden');
  } else if (state === 'paused') {
    resume.classList.remove('hidden');
    cancel.classList.remove('hidden');
  }
  // 'done' hides all
}

async function startExtractionTask() {
  if (!_currentTaskId) return;
  try {
    // Activate timer, logging, tips, LIVE badge — all UI side-effects of starting
    onExtractionActuallyStarted();
    await API.startTask(_currentTaskId);
    updateProgressControlButtons('running');
  } catch (e) {
    showToast('启动失败: ' + e.message, 'error');
  }
}

async function pauseExtractionTask() {
  if (!_currentTaskId) return;
  try {
    await API.pauseTask(_currentTaskId);
    updateProgressControlButtons('paused');
    addLog('info', '⏸ 已请求暂停');
  } catch (e) {
    showToast('暂停失败: ' + e.message, 'error');
  }
}

async function resumeExtractionTask() {
  if (!_currentTaskId) return;
  try {
    await API.resumeTask(_currentTaskId);
    updateProgressControlButtons('running');
    addLog('step', '▶ 继续执行');
  } catch (e) {
    showToast('恢复失败: ' + e.message, 'error');
  }
}

async function cancelExtractionNoConfirm() {
  if (!_currentTaskId) return;
  _pollAborted = true;
  try {
    await API.cancelTask(_currentTaskId);
  } catch (e) { console.error('Cancel failed:', e); }
  _currentTaskId = null;
  hideProgress();
}

async function cancelExtraction() {
  if (!_currentTaskId) return;
  const isM2 = (_progMode === 'm2');
  const taskWord = isM2 ? '推导' : '提取';
  const ok = await showDialog({
    type: 'warning',
    title: '中止任务',
    message: `确定要中止当前${taskWord}任务吗？\n\n已完成的部分会保留在成果区，但无法继续处理剩余批次。`,
    okText: '确定中止',
    cancelText: '继续执行',
    danger: true,
  });
  if (!ok) return;
  await cancelExtractionNoConfirm();
}

function minimizeProgress() {
  _progressMinimized = true;
  document.getElementById('progress-overlay').classList.add('hidden');
  const badge = document.getElementById('progress-badge');
  badge.classList.remove('hidden', 'done');
  updateBadge();
}

function restoreProgress() {
  _progressMinimized = false;
  document.getElementById('progress-badge').classList.add('hidden');
  document.getElementById('progress-overlay').classList.remove('hidden');
}

function updateBadge() {
  if (!_progressMinimized) return;
  const pct = document.getElementById('progress-pct').textContent;
  const step = document.getElementById('progress-message').textContent;
  document.getElementById('badge-pct').textContent = pct;
  document.getElementById('badge-step').textContent = step;
}

// Override hideProgress to also handle badge
const _origHideProgress = hideProgress;
