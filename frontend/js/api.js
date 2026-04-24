/**
 * API client for MOF M1 Generator backend.
 */
const BASE = '/api/v1';

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    });
  } catch (networkErr) {
    throw new Error(
      `无法连接服务器 (${path})。可能原因:\n` +
      `1. 服务器未启动或已崩溃，请检查终端窗口\n` +
      `2. 网络连接中断\n` +
      `原始错误: ${networkErr.message}`
    );
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res;
}

export const API = {
  // Documents
  async uploadDocuments(files) {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    const res = await fetch(`${BASE}/documents/`, { method: 'POST', body: form });
    if (!res.ok) throw new Error('Upload failed');
    return res.json();
  },
  async listDocuments() { return (await request('/documents/')).json(); },
  async deleteDocument(id) { return (await request(`/documents/${id}`, { method: 'DELETE' })).json(); },
  // V3.4: Excel table extraction
  async previewExcel(docId) {
    return (await request(`/documents/${docId}/excel-preview`)).json();
  },
  async analyzeExcel(docId) {
    return (await request(`/documents/${docId}/excel-analyze`, { method: 'POST' })).json();
  },
  // V3.4.1: split endpoints for progress visualization
  async classifyExcelSheets(docId) {
    return (await request(`/documents/${docId}/excel-classify`, { method: 'POST' })).json();
  },
  async analyzeExcelSheet(docId, sheetName) {
    return (await request(`/documents/${docId}/excel-analyze-sheet`, {
      method: 'POST', body: JSON.stringify({ sheet_name: sheetName }),
    })).json();
  },
  async analyzeStatsTable(docId, sheetName) {
    return (await request(`/documents/${docId}/analyze-stats-table`, {
      method: 'POST', body: JSON.stringify({ sheet_name: sheetName }),
    })).json();
  },
  async createM1FromExcel(body) {
    return (await request('/models/create-from-excel', {
      method: 'POST', body: JSON.stringify(body),
    })).json();
  },

  // V3.1: document type tagging
  async setDocumentType(id, docType, source = 'user') {
    return (await request(`/documents/${id}/type`, {
      method: 'PATCH',
      body: JSON.stringify({ doc_type: docType, source }),
    })).json();
  },
  async classifyDocument(id) {
    return (await request(`/documents/${id}/classify`, { method: 'POST' })).json();
  },

  // Extraction: docs → M1
  async startM1Extraction(documentIds, modelName, modelLabel) {
    return (await request('/extraction/start-m1', {
      method: 'POST',
      body: JSON.stringify({
        document_ids: documentIds,
        model_name: modelName || null,
        model_label: modelLabel || null,
      }),
    })).json();
  },

  // Derivation: M1 → M2
  async startM2Derivation(modelId, classIds = null) {
    const body = classIds ? { class_ids: classIds } : {};
    return (await request(`/extraction/derive-m2/${modelId}`, {
      method: 'POST',
      body: JSON.stringify(body),
    })).json();
  },

  async pollTask(taskId) { return (await request(`/extraction/status/${taskId}`)).json(); },
  async cancelTask(taskId) { return (await request(`/extraction/cancel/${taskId}`, { method: 'POST' })).json(); },
  async startTask(taskId) { return (await request(`/extraction/start/${taskId}`, { method: 'POST' })).json(); },
  async pauseTask(taskId) { return (await request(`/extraction/pause/${taskId}`, { method: 'POST' })).json(); },
  async resumeTask(taskId) { return (await request(`/extraction/resume/${taskId}`, { method: 'POST' })).json(); },
  async retryFailed(taskId, batchIds = null) {
    const body = batchIds ? { batch_ids: batchIds } : {};
    return (await request(`/extraction/retry-failed/${taskId}`, { method: 'POST', body: JSON.stringify(body) })).json();
  },

  async refine(modelId, message) {
    return (await request('/extraction/refine', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId, user_message: message }),
    })).json();
  },

  // Models
  async listModels() { return (await request('/models/')).json(); },
  async getModel(id) { return (await request(`/models/${id}`)).json(); },
  async updateModel(id, data) {
    return (await request(`/models/${id}`, { method: 'PUT', body: JSON.stringify(data) })).json();
  },
  async deleteModel(id) { return (await request(`/models/${id}`, { method: 'DELETE' })).json(); },
  async saveFromExtraction(data) {
    return (await request('/models/from-extraction', { method: 'POST', body: JSON.stringify(data) })).json();
  },
  async saveFromM2Review(data) {
    return (await request('/models/save-m2', { method: 'POST', body: JSON.stringify(data) })).json();
  },

  // Classes
  async addClass(modelId, data) {
    return (await request(`/models/${modelId}/classes`, { method: 'POST', body: JSON.stringify(data) })).json();
  },
  async updateClass(modelId, classId, data) {
    return (await request(`/models/${modelId}/classes/${classId}`, { method: 'PATCH', body: JSON.stringify(data) })).json();
  },
  async deleteClass(modelId, classId) {
    return (await request(`/models/${modelId}/classes/${classId}`, { method: 'DELETE' })).json();
  },

  // Attributes
  async addAttribute(modelId, classId, data) {
    return (await request(`/models/${modelId}/classes/${classId}/attributes`, { method: 'POST', body: JSON.stringify(data) })).json();
  },
  async updateAttribute(modelId, classId, attrId, data) {
    return (await request(`/models/${modelId}/classes/${classId}/attributes/${attrId}`, { method: 'PATCH', body: JSON.stringify(data) })).json();
  },
  async deleteAttribute(modelId, classId, attrId) {
    return (await request(`/models/${modelId}/classes/${classId}/attributes/${attrId}`, { method: 'DELETE' })).json();
  },

  // Associations
  async addAssociation(modelId, data) {
    return (await request(`/models/${modelId}/associations`, { method: 'POST', body: JSON.stringify(data) })).json();
  },
  async updateAssociation(modelId, assocId, data) {
    return (await request(`/models/${modelId}/associations/${assocId}`, { method: 'PATCH', body: JSON.stringify(data) })).json();
  },
  async deleteAssociation(modelId, assocId) {
    return (await request(`/models/${modelId}/associations/${assocId}`, { method: 'DELETE' })).json();
  },

  // Enumerations
  async addEnumeration(modelId, data) {
    return (await request(`/models/${modelId}/enumerations`, { method: 'POST', body: JSON.stringify(data) })).json();
  },
  async deleteEnumeration(modelId, enumId) {
    return (await request(`/models/${modelId}/enumerations/${enumId}`, { method: 'DELETE' })).json();
  },

  // Validation & Export
  async validateModel(modelId) {
    return (await request(`/models/${modelId}/validate`, { method: 'POST' })).json();
  },
  async validateMOF(m1Id, m2Id) {
    return (await request(`/models/validate-mof`, {
      method: 'POST',
      body: JSON.stringify({ m1_id: m1Id, m2_id: m2Id }),
    })).json();
  },
  async exportModel(modelId, format) {
    const res = await request('/export/', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId, format, version: null }),
    });
    return res.text();
  },
  async exportReviewPackage(m1Id, m2Id) {
    // Returns a Blob (zip) plus suggested filename from Content-Disposition.
    const res = await request('/export/review-package', {
      method: 'POST',
      body: JSON.stringify({ m1_id: m1Id, m2_id: m2Id }),
    });
    const blob = await res.blob();
    // Extract filename from header (RFC 5987 utf-8 variant preferred)
    const cd = res.headers.get('Content-Disposition') || '';
    let filename = 'review_package.zip';
    const utf8Match = cd.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match) {
      try { filename = decodeURIComponent(utf8Match[1]); } catch {}
    } else {
      const asciiMatch = cd.match(/filename="([^"]+)"/i);
      if (asciiMatch) filename = asciiMatch[1];
    }
    return { blob, filename };
  },
  async createVersion(modelId, changelog) {
    return (await request(`/models/${modelId}/versions`, { method: 'POST', body: JSON.stringify({ changelog }) })).json();
  },
  async listVersions(modelId) {
    return (await request(`/models/${modelId}/versions`)).json();
  },
  async switchVersion(modelId, version) {
    return (await request(`/models/${modelId}/versions/${version}/activate`, { method: 'POST' })).json();
  },

  // V3.2: Structural Pattern CRUD + impact preview
  async validatePattern(m2Id, patternReq) {
    return (await request(`/models/${m2Id}/structural-patterns/validate`, {
      method: 'POST', body: JSON.stringify(patternReq),
    })).json();
  },
  async previewPatternImpact(m2Id, body) {
    // body: {pattern_id?, pattern: {...}}
    return (await request(`/models/${m2Id}/structural-patterns/preview-impact`, {
      method: 'POST', body: JSON.stringify(body),
    })).json();
  },
  async createPattern(m2Id, body) {
    return (await request(`/models/${m2Id}/structural-patterns`, {
      method: 'POST', body: JSON.stringify(body),
    })).json();
  },
  async updatePattern(m2Id, patternId, body) {
    return (await request(`/models/${m2Id}/structural-patterns/${patternId}`, {
      method: 'PUT', body: JSON.stringify(body),
    })).json();
  },
  async deletePattern(m2Id, patternId, keepClasses = true) {
    return (await request(`/models/${m2Id}/structural-patterns/${patternId}?keep_classes=${keepClasses ? 'true' : 'false'}`, {
      method: 'DELETE',
    })).json();
  },

  // V3.1: quality sanity checks
  async qualityCheck(modelId) {
    return (await request(`/models/${modelId}/quality-check`)).json();
  },
  // V3.1: synonym class detection + merge
  async detectSynonyms(modelId, useLLM = false) {
    return (await request(`/models/${modelId}/detect-synonyms?use_llm=${useLLM ? 'true' : 'false'}`, {
      method: 'POST',
    })).json();
  },
  async mergeClasses(modelId, merges) {
    return (await request(`/models/${modelId}/merge-classes`, {
      method: 'POST', body: JSON.stringify({ merges }),
    })).json();
  },

  // Publish lifecycle (V3.0 methodology § 2.4)
  async getPublishStatus(modelId) {
    return (await request(`/models/${modelId}/publish-status`)).json();
  },
  async setPublishStatus(modelId, targetStatus, publishedBy = '') {
    return (await request(`/models/${modelId}/publish-status`, {
      method: 'POST',
      body: JSON.stringify({ target_status: targetStatus, published_by: publishedBy }),
    })).json();
  },

  // M3
  async getM3() { return (await request('/m2-templates/m3')).json(); },

  // LLM Config
  async getLLMPresets() { return (await request('/llm/presets')).json(); },
  async listLLMProviders() { return (await request('/llm/providers')).json(); },
  async createLLMProvider(config) {
    return (await request('/llm/providers', { method: 'POST', body: JSON.stringify(config) })).json();
  },
  async updateLLMProvider(id, config) {
    return (await request(`/llm/providers/${id}`, { method: 'PUT', body: JSON.stringify(config) })).json();
  },
  async deleteLLMProvider(id) {
    return (await request(`/llm/providers/${id}`, { method: 'DELETE' })).json();
  },
  async activateLLMProvider(id) {
    return (await request(`/llm/providers/${id}/activate`, { method: 'POST' })).json();
  },
  async testLLMProvider(id) {
    return (await request(`/llm/providers/${id}/test`, { method: 'POST' })).json();
  },
  async testLLMUnsaved(config) {
    return (await request('/llm/test-unsaved', { method: 'POST', body: JSON.stringify(config) })).json();
  },
  async getLLMStats() { return (await request('/llm/stats')).json(); },
  async clearLLMStats() { return (await request('/llm/stats', { method: 'DELETE' })).json(); },

  // ---- Complete Model Package (.mofpkg.zip) V1.0 ----
  /** Export a complete package; returns { blob, filename }. */
  async exportPackage(m1Id, options = {}) {
    const body = {
      m1_id: m1Id,
      include_m2: options.includeM2 !== false,
      include_all_versions: !!options.includeAllVersions,
      include_documents: !!options.includeDocuments,
      include_llm_providers: !!options.includeLLM,
      note: options.note || '',
      exported_by: options.exportedBy || '',
    };
    const res = await request('/package/export', {
      method: 'POST', body: JSON.stringify(body),
    });
    const blob = await res.blob();
    // Extract filename from RFC 5987 UTF-8 Content-Disposition
    const cd = res.headers.get('Content-Disposition') || '';
    let filename = 'model_package.mofpkg.zip';
    const utf8Match = cd.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match) {
      try { filename = decodeURIComponent(utf8Match[1]); } catch {}
    } else {
      const asciiMatch = cd.match(/filename="([^"]+)"/i);
      if (asciiMatch) filename = asciiMatch[1];
    }
    return { blob, filename };
  },
  /** Dry-run: parse manifest + list conflicts. */
  async previewImportPackage(file) {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${BASE}/package/preview`, { method: 'POST', body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },
  /** Actual import; options = { strategy, import_documents, import_llm }. */
  async importPackage(file, options = {}) {
    const form = new FormData();
    form.append('file', file);
    form.append('options', JSON.stringify({
      strategy: options.strategy || 'rename',
      import_documents: options.importDocuments !== false,
      import_llm: !!options.importLLM,
    }));
    const res = await fetch(`${BASE}/package/import`, { method: 'POST', body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },
};
