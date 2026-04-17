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
  async startM2Derivation(modelId) {
    return (await request(`/extraction/derive-m2/${modelId}`, { method: 'POST' })).json();
  },

  async pollTask(taskId) { return (await request(`/extraction/status/${taskId}`)).json(); },
  async cancelTask(taskId) { return (await request(`/extraction/cancel/${taskId}`, { method: 'POST' })).json(); },

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
  async exportModel(modelId, format) {
    const res = await request('/export/', {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId, format, version: null }),
    });
    return res.text();
  },
  async createVersion(modelId, changelog) {
    return (await request(`/models/${modelId}/versions`, { method: 'POST', body: JSON.stringify({ changelog }) })).json();
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
};
