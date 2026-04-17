/**
 * MOF M1 Generator — Main Application
 * Workflow: Upload docs → AI extract M1 → (optional) AI derive M2
 */
import { API } from './api.js';

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
  setupLLMSettings();
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
  } catch (e) { alert('上传失败: ' + e.message); }
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
function setupToolbar() {
  document.getElementById('btn-extract-m1').addEventListener('click', extractM1);
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
  document.getElementById('btn-validation-close').addEventListener('click', () => {
    document.getElementById('validation-modal').classList.add('hidden');
  });
}

function updateToolbarState() {
  const hasDocs = state.documents.length > 0;
  const hasM1 = state.m1ModelId !== null;
  const hasM2 = state.m2ModelId !== null;
  const hasActive = activeModelId() !== null;

  document.getElementById('btn-extract-m1').disabled = !hasDocs;
  document.getElementById('btn-derive-m2').disabled = !hasM1;
  document.getElementById('btn-validate').disabled = !hasActive;
  document.getElementById('btn-export').disabled = !hasActive;
  document.getElementById('btn-version').disabled = !hasActive;
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
    'AI正在从文档提取M1模型',
    `正在分析 ${state.documents.length} 份文档，基于MOF M3规范提取领域实体`,
    M1_STEPS
  );
  addLog('info', `输入文档: ${docNames}`);
  addLog('info', `共 ${state.documents.reduce((a, d) => a + d.char_count, 0).toLocaleString()} 字符`);

  try {
    const { task_id } = await API.startM1Extraction(docIds);
    _currentTaskId = task_id;
    await pollTask(task_id, result => {
      _currentTaskId = null;
      addLog('success', `提取完成: ${result.classes_found} 类, ${result.attributes_found} 属性, ${result.associations_found} 关联, ${result.enumerations_found} 枚举`);
      // Wait a moment for user to see the final progress, then switch to review
      setTimeout(() => {
        hideProgress();
        // Small delay to ensure progress overlay fully hides before review shows
        setTimeout(() => showReviewPanel(result), 100);
      }, 800);
    }, {
      parsing_documents: [
        '正在加载全量文档内容...',
      ],
      discovering_entities: [
        '全量数据按批次发送给AI大模型...',
        '后续批次会携带已发现实体作为上下文，保证关联一致性...',
      ],
      extracting_attributes: [
        '逐类提取领域专属属性...',
        '匹配数据类型: String/Float/Integer/Date/Boolean/Enum',
        '识别度量单位: MW, kV, rpm, mm, MPa...',
      ],
      extracting_associations: [
        '分析设备层级包含关系...',
        '识别组合(composition)与聚合(aggregation)模式...',
      ],
    });
  } catch (e) {
    addLog('info', '提取失败: ' + e.message);
    setTimeout(hideProgress, 2000);
    alert('M1提取失败: ' + e.message);
  }
}

// ============================================================
// AI Derivation: M1 → M2
// ============================================================
async function deriveM2() {
  if (!state.m1ModelId) return;

  const m1Label = state.m1Model?.label || state.m1Model?.name || 'M1模型';
  const classCount = state.m1Model?.versions?.slice(-1)[0]?.package?.classes?.length || 0;
  showProgress(
    'AI正在从M1反推M2元模型',
    `分析 ${m1Label} 中 ${classCount} 个类的共性，抽象出通用基类`,
    M2_STEPS
  );
  addLog('info', `源M1模型: ${m1Label}`);
  addLog('info', `包含 ${classCount} 个类待泛化`);

  try {
    const { task_id } = await API.startM2Derivation(state.m1ModelId);
    _currentTaskId = task_id;
    await pollTask(task_id, result => {
      _currentTaskId = null;
      state.m2ModelId = result.m2_model_id;
      loadModel(result.m2_model_id, 'm2');
      loadModel(state.m1ModelId, 'm1');
      addLog('success', 'M2元模型推导完成，已标记M1继承关系');
      const mappings = result.m1_class_mappings || [];
      if (mappings.length) {
        addLog('info', `继承映射: ${mappings.map(m => `${m.m1_class_name}→${m.m2_parent_name}`).join(', ')}`);
      }
      setTimeout(() => {
        hideProgress();
        document.querySelector('.tab[data-tab="m2"]').click();
      }, 1500);
    }, {
      deriving_m2: [
        '提取各M1类的共享属性...',
        '合并为抽象M2基类（如"设备"）...',
        '构建M1→M2继承映射...',
      ],
    });
  } catch (e) {
    addLog('info', '推导失败: ' + e.message);
    setTimeout(hideProgress, 2000);
    alert('M2推导失败: ' + e.message);
  }
}

// ============================================================
// Rich Progress Display
// ============================================================

const M1_STEPS = [
  { key: 'parsing_documents',      label: '加载全量文档' },
  { key: 'discovering_entities',    label: '分批识别实体类型与枚举' },
  { key: 'extracting_attributes',  label: '提取各类属性与数据类型' },
  { key: 'extracting_associations', label: '分析类间关联关系' },
  { key: 'saving',                 label: '保存M1模型' },
  { key: 'completed',              label: '完成' },
];

const M2_STEPS = [
  { key: 'starting',     label: '加载M1模型数据' },
  { key: 'deriving_m2',  label: '抽象共性属性为M2基类' },
  { key: 'completed',    label: '生成M2元模型并关联M1' },
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

function showProgress(title, subtitle, steps) {
  _progStart = Date.now();
  _progLogs = [];
  _progStepTimes = {};
  _progTipIdx = Math.floor(Math.random() * TIPS.length);

  document.getElementById('progress-title').textContent = title;
  document.getElementById('progress-subtitle').textContent = subtitle || '';
  document.getElementById('progress-fill').style.width = '0%';
  document.getElementById('progress-pct').textContent = '0%';
  document.getElementById('progress-message').textContent = '准备中...';
  document.getElementById('progress-tip-text').textContent = TIPS[_progTipIdx];

  // Render step pipeline
  const stepsEl = document.getElementById('progress-steps');
  stepsEl.innerHTML = '';
  for (const s of (steps || [])) {
    const div = document.createElement('div');
    div.className = 'prog-step pending';
    div.dataset.key = s.key;
    div.innerHTML = `
      <div class="prog-step-icon"></div>
      <span class="prog-step-label">${s.label}</span>
      <span class="prog-step-time"></span>`;
    stepsEl.appendChild(div);
  }

  // Clear log
  document.getElementById('progress-log').innerHTML = '';
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

  document.getElementById('progress-overlay').classList.remove('hidden');
}

function hideProgress() {
  clearInterval(_progTimer);
  clearInterval(_progTipInterval);
  _progressMinimized = false;
  document.getElementById('progress-overlay').classList.add('hidden');
  // Also hide badge (extraction complete)
  const badge = document.getElementById('progress-badge');
  badge.classList.add('hidden');
}

function updateTimer() {
  const elapsed = Math.floor((Date.now() - _progStart) / 1000);
  const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
  const s = String(elapsed % 60).padStart(2, '0');
  document.getElementById('progress-timer').textContent = `${m}:${s}`;
}

function updateProgress(step, progress, message) {
  const pct = Math.round((progress || 0) * 100);
  document.getElementById('progress-fill').style.width = `${pct}%`;
  document.getElementById('progress-pct').textContent = `${pct}%`;
  // Update floating badge if minimized
  if (_progressMinimized) {
    document.getElementById('badge-pct').textContent = `${pct}%`;
    document.getElementById('badge-step').textContent = (message || '').substring(0, 30);
  }
  document.getElementById('progress-message').textContent = message || '';

  // Update pipeline steps
  const allSteps = document.querySelectorAll('#progress-steps .prog-step');
  let reachedCurrent = false;
  for (const el of allSteps) {
    const key = el.dataset.key;
    if (key === step) {
      if (!el.classList.contains('active')) {
        el.className = 'prog-step active';
        _progStepTimes[key] = Date.now();
        addLog('step', message || el.querySelector('.prog-step-label').textContent);
      }
      reachedCurrent = true;
    } else if (!reachedCurrent) {
      // Previous steps are done
      if (!el.classList.contains('done')) {
        el.className = 'prog-step done';
        // Show elapsed time for this step
        const startTime = _progStepTimes[key];
        if (startTime) {
          const dur = ((Date.now() - startTime) / 1000).toFixed(1);
          el.querySelector('.prog-step-time').textContent = `${dur}s`;
        }
      }
    }
    // Future steps stay pending
  }

  // If completed, mark all done
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
  const elapsed = ((Date.now() - _progStart) / 1000).toFixed(1);
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

      updateProgress(s.step, s.progress, s.message);

      if (s.documents && s.documents.length) {
        renderDocProgress(s.documents);
      }

      if (s.parallel_tasks && s.parallel_tasks.length) {
        renderParallelTasks(s.parallel_tasks);
      } else {
        document.getElementById('progress-parallel').innerHTML = '';
      }

      const serverLogs = s.logs || [];
      if (serverLogs.length > lastLogCount) {
        for (let i = lastLogCount; i < serverLogs.length; i++) {
          const l = serverLogs[i];
          addLogRaw(l.type, l.text, l.time);
        }
        lastLogCount = serverLogs.length;
      }

      // Render LLM conversation stream
      if (s.llm_conversations && s.llm_conversations.length) {
        renderConversations(s.llm_conversations);
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
      await loadModel(m1s[0].id, 'm1');
    }
    if (m2s.length) {
      state.m2ModelId = m2s[0].id;
      await loadModel(m2s[0].id, 'm2');
    }
    renderModelPicker();
  } catch (e) { console.error('Load models failed:', e); }
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
    } else {
      state.m1Model = model;
      state.m1ModelId = modelId;
      renderTree('m1');
    }
    updateToolbarState();
  } catch (e) { console.error('Failed to load model:', e); }
}

// ============================================================
// Tree Rendering (shared for M1 and M2)
// ============================================================
const _treeViewMode = { m1: 'hierarchy', m2: 'hierarchy' };  // 'hierarchy' or 'flat'

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
  container.innerHTML = '';

  const root = document.createElement('div');
  root.className = 'tree-node';

  // Package header
  const viewMode = _treeViewMode[layer] || 'hierarchy';
  const pkgLabel = layer === 'm2' ? `M2: ${pkg.label || pkg.name}` : `M1: ${pkg.label || pkg.name}`;
  const pkgHeader = document.createElement('div');
  pkgHeader.className = 'tree-row pkg-header';
  pkgHeader.innerHTML = `
    <span class="tree-badge ${layer === 'm2' ? 'badge-m2' : 'badge-m1'}">${layer.toUpperCase()}</span>
    <span class="tree-label" style="font-weight:600">${pkgLabel}</span>`;
  root.appendChild(pkgHeader);

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
    const name = prompt('属性技术名 (camelCase):');
    if (!name) return;
    const label = prompt('属性中文名:') || '';
    await API.addAttribute(modelId, cls.id, { name, label });
    await loadModel(modelId, layer);
  });
  addTreeAction(row, '\u{1F5D1}', 'del', async () => {
    if (!confirm(`删除类 ${cls.name}？`)) return;
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
    const name = prompt('类技术名 (PascalCase):'); if (!name) return;
    const label = prompt('类中文名:') || '';
    await API.addClass(mid, { name, label });
    await loadModel(mid, state.activeTab);
  });
  document.getElementById('btn-add-enum').addEventListener('click', async () => {
    const mid = activeModelId(); if (!mid) return;
    const name = prompt('枚举技术名 (PascalCase):'); if (!name) return;
    const label = prompt('枚举中文名:') || '';
    await API.addEnumeration(mid, { name, label, literals: [] });
    await loadModel(mid, state.activeTab);
  });
  document.getElementById('btn-add-assoc').addEventListener('click', async () => {
    const mid = activeModelId(); if (!mid) return;
    const model = activeModel();
    const pkg = model.versions[model.versions.length - 1].package;
    if (!pkg?.classes?.length) { alert('请先添加至少两个类'); return; }
    const name = prompt('关联名称:'); if (!name) return;
    const classInfo = pkg.classes.map(c => `${c.id}: ${c.name}`).join('\n');
    const srcId = prompt(`源类ID:\n${classInfo}`);
    const tgtId = prompt(`目标类ID:\n${classInfo}`);
    if (!srcId || !tgtId) return;
    await API.addAssociation(mid, { name, source_class_id: srcId, target_class_id: tgtId });
    await loadModel(mid, state.activeTab);
  });
}

// ============================================================
// Validation / Export / Version
// ============================================================
async function validateModel() {
  const mid = activeModelId(); if (!mid) return;
  try {
    const result = await API.validateModel(mid);
    const modal = document.getElementById('validation-modal');
    const title = document.getElementById('validation-title');
    const content = document.getElementById('validation-content');
    title.textContent = result.is_valid ? '\u2705 验证通过' : '\u274C 验证失败';
    if (result.is_valid && !result.warnings?.length) {
      content.innerHTML = '<div class="val-success">模型符合M3规范，无错误或警告。</div>';
    } else {
      content.innerHTML = (result.errors || []).map(e => `<div class="val-item val-error"><span class="val-icon">\u274C</span>${e.message}</div>`).join('') +
        (result.warnings || []).map(w => `<div class="val-item val-warning"><span class="val-icon">\u26A0\uFE0F</span>${w.message}</div>`).join('');
    }
    modal.classList.remove('hidden');
  } catch (e) { alert('验证错误: ' + e.message); }
}

function showExportModal() {
  document.getElementById('export-preview').classList.add('hidden');
  // Pre-select the active layer
  const layerRadio = document.querySelector(`input[name="export-layer"][value="${state.activeTab}"]`);
  if (layerRadio) layerRadio.checked = true;
  document.getElementById('export-modal').classList.remove('hidden');
}

async function doExport() {
  const layer = document.querySelector('input[name="export-layer"]:checked').value;
  const mid = layer === 'm2' ? state.m2ModelId : state.m1ModelId;
  if (!mid) { alert(`没有${layer.toUpperCase()}模型可导出`); return; }
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
  } catch (e) { alert('导出失败: ' + e.message); }
}

async function createVersion() {
  const mid = activeModelId(); if (!mid) return;
  const changelog = prompt('版本变更说明:') || '';
  try {
    const result = await API.createVersion(mid, changelog);
    alert(`版本 ${result.version} 已创建`);
    await loadModel(mid, state.activeTab);
  } catch (e) { alert('版本创建失败: ' + e.message); }
}

// ============================================================
// Extraction Review Panel
// ============================================================

let _reviewData = null; // holds the raw extraction result for review

function setupReviewPanel() {
  document.getElementById('btn-review-close').addEventListener('click', closeReviewPanel);
  document.getElementById('btn-review-cancel').addEventListener('click', closeReviewPanel);
  document.getElementById('btn-review-confirm').addEventListener('click', confirmReviewImport);
  document.getElementById('btn-review-all').addEventListener('click', () => toggleAllReview(true));
  document.getElementById('btn-review-none').addEventListener('click', () => toggleAllReview(false));
}

function showReviewPanel(result) {
  console.log('[Review] showReviewPanel called, result:', result ? Object.keys(result) : 'null');

  if (!result || !result.package) {
    alert('提取结果为空，请重新提取。');
    return;
  }

  _reviewData = result;
  const pkg = result.package;
  const classes = pkg.classes || [];
  const enums = pkg.enumerations || [];
  const assocs = pkg.associations || [];

  // Pre-fill default label (user can edit)
  const timestamp = new Date().toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'});
  const defaultLabel = `M1模型 - ${timestamp}`;
  const labelInput = document.getElementById('review-model-label');
  if (labelInput) labelInput.value = pkg.label || defaultLabel;

  // ---- Stats ----
  const statsEl = document.getElementById('review-stats');
  statsEl.innerHTML = `
    <div class="review-stat-card stat-classes"><div class="review-stat-num">${classes.length}</div><div class="review-stat-label">类 Classes</div></div>
    <div class="review-stat-card stat-attrs"><div class="review-stat-num">${result.attributes_found}</div><div class="review-stat-label">属性 Attributes</div></div>
    <div class="review-stat-card stat-assocs"><div class="review-stat-num">${assocs.length}</div><div class="review-stat-label">关联 Associations</div></div>
    <div class="review-stat-card stat-enums"><div class="review-stat-num">${enums.length}</div><div class="review-stat-label">枚举 Enumerations</div></div>
  `;

  // ---- Process summary ----
  const procList = document.getElementById('review-process-list');
  const totalDocs = result.total_documents || state.documents.length;
  const totalChars = result.total_chars || 0;
  let procHtml = `
    <li>从 ${totalDocs} 份文档中提取（共 ${totalChars.toLocaleString()} 字符），识别出 ${classes.length} 个实体类型</li>
    <li>各类共提取 ${result.attributes_found} 个领域属性，覆盖 String/Float/Integer/Date/Boolean/Enum 数据类型</li>
  `;
  if (assocs.length) {
    const compCount = assocs.filter(a => a.association_type === 'composition').length;
    const assocCount = assocs.length - compCount;
    procHtml += `<li>发现 ${assocs.length} 条类间关联（${compCount} composition + ${assocCount} association/aggregation）</li>`;
  }
  procList.innerHTML = procHtml;

  // ---- Confidence notes ----
  const notesEl = document.getElementById('review-notes');
  const notes = result.confidence_notes || [];
  if (notes.length) {
    notesEl.innerHTML = '<strong>&#9888; AI注意事项:</strong> ' + notes.join(' | ');
    notesEl.classList.remove('hidden');
  } else {
    notesEl.classList.add('hidden');
  }

  // ---- Selectable entity list ----
  renderReviewEntities(classes, enums, assocs);

  // Show
  document.getElementById('review-overlay').classList.remove('hidden');
  updateReviewConfirmBtn();
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
  btn.textContent = `确认导入选中的 ${checked} 项`;
  btn.disabled = checked === 0;
}

function closeReviewPanel() {
  document.getElementById('review-overlay').classList.add('hidden');
  _reviewData = null;
}

async function confirmReviewImport() {
  if (!_reviewData) return;
  const pkg = _reviewData.package;

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

  // Use user-entered label
  const userLabel = (document.getElementById('review-model-label')?.value || '').trim();
  const finalLabel = userLabel || filteredPkg.label || `M1_${Date.now()}`;

  try {
    const res = await API.saveFromExtraction({
      package: { ...filteredPkg, label: finalLabel },
      name: filteredPkg.name || `M1_Model`,
      label: finalLabel,
      source_document_ids: _reviewData.source_document_ids || [],
    });

    // Refresh model list FIRST so picker shows new model
    const listRes = await API.listModels();
    state.allModels = listRes.models;

    // Then load & select the new model
    state.m1ModelId = res.model_id;
    await loadModel(res.model_id, 'm1');
    renderModelPicker();
    closeReviewPanel();

    // Show success notification
    showToast(`✓ 新M1模型已保存: ${finalLabel}`);

    // Switch to M1 tab
    document.querySelector('.tab[data-tab="m1"]').click();
  } catch (e) {
    alert('保存失败: ' + e.message);
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
// Relationship Diagram (M3 → M2 → M1)
// ============================================================

function renderDiagram() {
  const canvas = document.getElementById('diagram-canvas');
  const m2 = state.m2Model;
  const m1 = state.m1Model;

  const m2Pkg = m2?.versions?.slice(-1)[0]?.package;
  const m1Pkg = m1?.versions?.slice(-1)[0]?.package;
  const m1Classes = m1Pkg?.classes || [];
  const m2Classes = m2Pkg?.classes || [];
  const m3Concepts = m3Data?.concepts || [];

  // Build mapping: M1 class → M2 parent
  const m1ToM2 = {};
  for (const c of m1Classes) {
    if (c.parent_class_name) m1ToM2[c.name] = c.parent_class_name;
  }

  // Build mapping: M2 class → M3 concepts it uses
  // Every M2 class is a "Class" with "Attributes" and possibly "Associations"
  const m2UsesM3 = {};
  for (const c of m2Classes) {
    const used = new Set(['Class', 'Attribute']);
    for (const a of (c.attributes || [])) {
      if (a.data_type === 'Enum') used.add('Enumeration');
      used.add('DataType');
    }
    m2UsesM3[c.name] = [...used];
  }

  let html = '';

  // ---- M3 Layer ----
  html += `
    <div class="diagram-layer" id="diagram-m3">
      <div class="diagram-layer-header">
        <span class="diagram-layer-badge badge-m3-layer">M3</span>
        <span class="diagram-layer-title">元元模型 — 建模语言</span>
        <span class="diagram-layer-desc">\u{1F512} 固定层，定义可用的建模概念</span>
      </div>
      <div class="diagram-boxes" id="m3-boxes">`;
  for (const c of m3Concepts) {
    html += `
        <div class="diagram-box" data-id="${c.id}" data-name="${c.name}" style="border-color:${c.color}40">
          <div class="diagram-box-name" style="color:${c.color}">${c.name}</div>
          <div class="diagram-box-label">${c.label}</div>
        </div>`;
  }
  html += `</div></div>`;

  // ---- Arrow M3→M2 ----
  html += `
    <div class="diagram-arrow-zone">
      <div class="diagram-arrow">
        <div class="diagram-arrow-line"></div>
        <span class="diagram-arrow-text">\u2193 M2 使用 M3 的概念来定义通用类型</span>
        <div class="diagram-arrow-line"></div>
      </div>
    </div>`;

  // ---- M2 Layer ----
  html += `
    <div class="diagram-layer" id="diagram-m2">
      <div class="diagram-layer-header">
        <span class="diagram-layer-badge badge-m2-layer">M2</span>
        <span class="diagram-layer-title">元模型 — 通用业务对象类型</span>
        <span class="diagram-layer-desc">${m2Classes.length ? m2Classes.length + ' 个抽象类' : '待推导'}</span>
      </div>
      <div class="diagram-boxes" id="m2-boxes">`;
  if (m2Classes.length) {
    for (const c of m2Classes) {
      const attrs = (c.attributes || []).slice(0, 6);
      const attrsHtml = attrs.map(a =>
        `<div class="attr-line"><span>${a.name}</span><span class="attr-type">${a.data_type}</span></div>`
      ).join('') + (c.attributes?.length > 6 ? `<div class="attr-line" style="opacity:0.5">...${c.attributes.length - 6} more</div>` : '');
      html += `
        <div class="diagram-box" data-id="${c.id}" data-name="${c.name}" style="border-color:var(--purple)">
          <div class="diagram-box-name" style="color:var(--purple)">${c.name}</div>
          <div class="diagram-box-label">${c.label || ''} ${c.is_abstract ? '(abstract)' : ''}</div>
          ${attrsHtml ? `<div class="diagram-box-attrs">${attrsHtml}</div>` : ''}
        </div>`;
    }
  } else {
    html += '<div class="diagram-empty">M2元模型待推导 — 先生成M1后点击"反推M2"</div>';
  }
  html += `</div></div>`;

  // ---- Arrow M2→M1 ----
  html += `
    <div class="diagram-arrow-zone">
      <div class="diagram-arrow">
        <div class="diagram-arrow-line"></div>
        <span class="diagram-arrow-text">\u2193 M1 继承 M2 的通用属性，新增领域专属属性</span>
        <div class="diagram-arrow-line"></div>
      </div>
    </div>`;

  // ---- M1 Layer ----
  html += `
    <div class="diagram-layer" id="diagram-m1">
      <div class="diagram-layer-header">
        <span class="diagram-layer-badge badge-m1-layer">M1</span>
        <span class="diagram-layer-title">模型 — 领域特定模板</span>
        <span class="diagram-layer-desc">${m1Classes.length ? m1Classes.length + ' 个领域类' : '待提取'}</span>
      </div>
      <div class="diagram-boxes" id="m1-boxes">`;
  if (m1Classes.length) {
    for (const c of m1Classes) {
      const ownAttrs = (c.attributes || []).filter(a => !a.is_inherited).slice(0, 5);
      const inherited = (c.attributes || []).filter(a => a.is_inherited).length;
      const attrsHtml = ownAttrs.map(a =>
        `<div class="attr-line"><span>${a.name}</span><span class="attr-type">${a.data_type}${a.unit ? ' ('+a.unit+')' : ''}</span></div>`
      ).join('') + (inherited ? `<div class="attr-line" style="opacity:0.4;font-style:italic">+${inherited} inherited</div>` : '');
      const parentTag = c.parent_class_name ? ` extends ${c.parent_class_name}` : '';
      html += `
        <div class="diagram-box" data-id="${c.id}" data-name="${c.name}" data-parent="${c.parent_class_name || ''}" style="border-color:var(--accent)">
          <div class="diagram-box-name" style="color:var(--accent)">${c.name}</div>
          <div class="diagram-box-label">${c.label || ''}${parentTag}</div>
          ${attrsHtml ? `<div class="diagram-box-attrs">${attrsHtml}</div>` : ''}
        </div>`;
    }
  } else {
    html += '<div class="diagram-empty">M1模型待提取 — 上传文档后点击"AI提取M1"</div>';
  }
  html += `</div></div>`;

  canvas.innerHTML = html;

  // ---- Draw SVG connection lines ----
  requestAnimationFrame(() => drawConnectionLines());
}

function drawConnectionLines() {
  const canvas = document.getElementById('diagram-canvas');
  // Remove old SVG
  const oldSvg = canvas.querySelector('.diagram-svg');
  if (oldSvg) oldSvg.remove();

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('diagram-svg');
  svg.style.width = canvas.scrollWidth + 'px';
  svg.style.height = canvas.scrollHeight + 'px';

  const canvasRect = canvas.getBoundingClientRect();
  const scrollLeft = canvas.scrollLeft;
  const scrollTop = canvas.scrollTop;

  // Draw M1→M2 inheritance lines
  const m1Boxes = canvas.querySelectorAll('#m1-boxes .diagram-box');
  const m2Boxes = canvas.querySelectorAll('#m2-boxes .diagram-box');

  for (const m1Box of m1Boxes) {
    const parentName = m1Box.dataset.parent;
    if (!parentName) continue;
    const m2Box = [...m2Boxes].find(b => b.dataset.name === parentName);
    if (!m2Box) continue;

    const r1 = m1Box.getBoundingClientRect();
    const r2 = m2Box.getBoundingClientRect();

    const x1 = r1.left - canvasRect.left + scrollLeft + r1.width / 2;
    const y1 = r1.top - canvasRect.top + scrollTop;
    const x2 = r2.left - canvasRect.left + scrollLeft + r2.width / 2;
    const y2 = r2.top - canvasRect.top + scrollTop + r2.height;

    const midY = (y1 + y2) / 2;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`);
    path.classList.add('conn-line', 'active', 'm1-line');
    svg.appendChild(path);

    // Arrow head
    const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    const ax = x2, ay = y2;
    arrow.setAttribute('points', `${ax},${ay} ${ax-4},${ay+8} ${ax+4},${ay+8}`);
    arrow.setAttribute('fill', 'var(--accent)');
    arrow.setAttribute('opacity', '0.8');
    svg.appendChild(arrow);
  }

  canvas.insertBefore(svg, canvas.firstChild);
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
    notes: document.getElementById('llm-notes').value.trim() || null,
    is_active: false,
  };
}

async function saveLLMProvider() {
  const data = collectLLMForm();
  if (!data.name) { alert('请输入配置名称'); return; }
  if (!data.provider) { alert('请选择服务商'); return; }
  if (!data.model) { alert('请输入模型名称'); return; }

  try {
    if (llmEditingId) {
      await API.updateLLMProvider(llmEditingId, data);
    } else {
      const res = await API.createLLMProvider(data);
      llmEditingId = res.id;
    }
    await refreshLLMList();
    // Re-select
    const updated = llmProviders.find(p => p.id === llmEditingId);
    if (updated) editLLMProvider(updated);
  } catch (e) { alert('保存失败: ' + e.message); }
}

async function deleteLLMProvider() {
  if (!llmEditingId) return;
  if (!confirm('确定删除此配置？')) return;
  try {
    await API.deleteLLMProvider(llmEditingId);
    llmEditingId = null;
    document.getElementById('llm-form').classList.add('hidden');
    document.getElementById('llm-form-empty').classList.remove('hidden');
    await refreshLLMList();
  } catch (e) { alert('删除失败: ' + e.message); }
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

function setupProgressMinimize() {
  document.getElementById('btn-progress-minimize').addEventListener('click', minimizeProgress);
  document.getElementById('progress-badge').addEventListener('click', e => {
    // Don't restore if clicking the cancel button
    if (e.target.id === 'btn-badge-cancel') return;
    restoreProgress();
  });
  document.getElementById('btn-progress-cancel').addEventListener('click', cancelExtraction);
  document.getElementById('btn-badge-cancel').addEventListener('click', cancelExtraction);
}

async function cancelExtraction() {
  if (!_currentTaskId) return;
  if (!confirm('确定要中止当前提取任务吗？已完成的部分将丢失。')) return;

  // 1. Abort the frontend polling loop immediately
  _pollAborted = true;

  // 2. Tell backend to cancel the async task
  try {
    await API.cancelTask(_currentTaskId);
  } catch (e) {
    console.error('Cancel failed:', e);
  }

  // 3. Clean up frontend state
  _currentTaskId = null;
  hideProgress();
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
