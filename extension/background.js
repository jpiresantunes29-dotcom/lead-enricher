/* ═══════════════════════════════════════════════════════════════════
   LeadEnricher — service worker

   Centraliza TODA chamada à API. O content script nunca vê o token:
   ele só manda mensagens, e o token vive aqui + chrome.storage.local.
   Isso evita que qualquer script da página do LinkedIn leia a credencial.
   ═══════════════════════════════════════════════════════════════════ */

const DEFAULT_API = 'https://leadenricher.app';
const STORAGE = { token: 'le_token', api: 'le_api', label: 'le_device' };

async function getConfig() {
  const data = await chrome.storage.local.get([STORAGE.token, STORAGE.api]);
  return {
    token: data[STORAGE.token] || null,
    api: (data[STORAGE.api] || DEFAULT_API).replace(/\/+$/, ''),
  };
}

async function api(path, { method = 'POST', body = null, auth = true } = {}) {
  const { token, api: base } = await getConfig();
  if (auth && !token) {
    return { ok: false, status: 401, error: 'not_paired' };
  }

  const headers = { 'Content-Type': 'application/json' };
  if (auth && token) headers['Authorization'] = `Bearer ${token}`;

  let resp;
  try {
    resp = await fetch(`${base}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    return { ok: false, status: 0, error: 'network', detail: String(e) };
  }

  let data = null;
  try { data = await resp.json(); } catch (_) { data = null; }

  if (!resp.ok) {
    return {
      ok: false,
      status: resp.status,
      error: resp.status === 401 ? 'not_paired' : 'http',
      detail: (data && (data.detail || data.message)) || `HTTP ${resp.status}`,
    };
  }
  return { ok: true, status: resp.status, data };
}

/* ── Ações expostas ao content script e ao popup ────────────────── */
const ACTIONS = {
  me:       () => api('/api/extension/me', { method: 'GET' }),
  resolve:  (p) => api('/api/extension/resolve', { body: p }),
  reveal:   (p) => api('/api/extension/reveal', { body: p }),
  company:  (p) => api('/api/extension/company', { body: p }),
  save:     (p) => api('/api/extension/save', { body: p }),
  report:   (p) => api('/api/extension/report', { body: p }),

  async pair({ code, api: apiUrl, label }) {
    if (apiUrl) {
      await chrome.storage.local.set({ [STORAGE.api]: apiUrl.replace(/\/+$/, '') });
    }
    const res = await api('/api/extension/pair', {
      body: { code, device_label: label || 'Chrome' },
      auth: false,
    });
    if (res.ok && res.data && res.data.token) {
      await chrome.storage.local.set({
        [STORAGE.token]: res.data.token,
        [STORAGE.label]: res.data.device_label || 'Chrome',
      });
    }
    return res;
  },

  async status() {
    const { token, api: base } = await getConfig();
    return { ok: true, data: { paired: !!token, api: base } };
  },

  async unpair() {
    await chrome.storage.local.remove([STORAGE.token, STORAGE.label]);
    return { ok: true, data: { paired: false } };
  },

  async setApi({ api: apiUrl }) {
    await chrome.storage.local.set({ [STORAGE.api]: (apiUrl || DEFAULT_API).replace(/\/+$/, '') });
    return { ok: true, data: { api: apiUrl } };
  },
};

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const handler = ACTIONS[msg && msg.action];
  if (!handler) {
    sendResponse({ ok: false, error: 'unknown_action' });
    return false;
  }
  Promise.resolve(handler(msg.payload || {}))
    .then(sendResponse)
    .catch((e) => sendResponse({ ok: false, error: 'exception', detail: String(e) }));
  return true; // resposta assíncrona
});
