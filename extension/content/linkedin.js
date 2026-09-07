/* ═══════════════════════════════════════════════════════════════════
   LeadEnricher — content script do LinkedIn

   Regras de convivência (protegem a conta do usuário):
     • só lê a página que o usuário abriu com as próprias mãos
     • nunca navega, pagina ou clica sozinho
     • nenhuma chamada à API interna do LinkedIn
     • lote com teto e intervalo entre itens

   Extração em 4 camadas: se o LinkedIn mexer no HTML, a camada seguinte
   assume. O método que funcionou vai no console (le:extract) para a gente
   detectar quebra antes do usuário reclamar.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  if (window.__leadenricherLoaded) return;
  window.__leadenricherLoaded = true;

  const BULK_MAX = 25;              // teto de itens por lote
  const BULK_MIN_DELAY = 1500;      // intervalo entre revelações (ms)
  const BULK_JITTER = 1500;

  /* ══════════ utilidades ══════════ */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

  function send(action, payload) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ action, payload }, (resp) => {
          if (chrome.runtime.lastError) {
            resolve({ ok: false, error: 'disconnected', detail: chrome.runtime.lastError.message });
          } else {
            resolve(resp || { ok: false, error: 'empty' });
          }
        });
      } catch (e) {
        resolve({ ok: false, error: 'exception', detail: String(e) });
      }
    });
  }

  function jsonLdBlocks() {
    return $$('script[type="application/ld+json"]').map((el) => {
      try { return JSON.parse(el.textContent || 'null'); } catch (_) { return null; }
    }).filter(Boolean);
  }

  function findInGraph(predicate) {
    for (const block of jsonLdBlocks()) {
      const nodes = block['@graph'] || (Array.isArray(block) ? block : [block]);
      for (const node of nodes) {
        if (node && predicate(node)) return node;
      }
    }
    return null;
  }

  function meta(prop) {
    const el = document.querySelector(`meta[property="${prop}"]`) ||
               document.querySelector(`meta[name="${prop}"]`);
    return el ? clean(el.getAttribute('content')) : null;
  }

  function decodeLinkedinRedirect(href) {
    try {
      const url = new URL(href, location.origin);
      if (url.pathname.includes('/redir/redirect')) {
        const target = url.searchParams.get('url');
        if (target) return decodeURIComponent(target);
      }
      return href;
    } catch (_) { return href; }
  }

  function hostOf(href) {
    try { return new URL(href).hostname.replace(/^www\./, ''); } catch (_) { return null; }
  }

  const SOCIAL_HOSTS = /linkedin|facebook|instagram|twitter|x\.com|youtube|tiktok|google|bit\.ly|lnkd\.in/i;

  /* ══════════ extração: perfil de pessoa ══════════ */
  function extractProfile() {
    const slug = (location.pathname.match(/\/in\/([^/?#]+)/) || [])[1] || null;
    const out = { linkedin_slug: slug ? decodeURIComponent(slug) : null, methods: {} };

    // Camada 1 — JSON-LD (formato estável, muda menos que o HTML)
    const person = findInGraph((n) => (n['@type'] === 'Person' || (Array.isArray(n['@type']) && n['@type'].includes('Person'))) && n.name);
    if (person) {
      out.full_name = clean(person.name);
      out.methods.name = 'jsonld';
      const job = Array.isArray(person.jobTitle) ? person.jobTitle[0] : person.jobTitle;
      if (job) { out.title = clean(job); out.methods.title = 'jsonld'; }
      const works = Array.isArray(person.worksFor) ? person.worksFor[0] : person.worksFor;
      if (works && works.name) { out.company_name = clean(works.name); out.methods.company = 'jsonld'; }
      const addr = person.address || {};
      if (addr.addressLocality) out.location = clean([addr.addressLocality, addr.addressRegion].filter(Boolean).join(', '));
      if (person.image && person.image.contentUrl) out.photo_url = person.image.contentUrl;
    }

    // Camada 2 — meta tags Open Graph
    if (!out.full_name) {
      const ogTitle = meta('og:title');
      if (ogTitle) {
        out.full_name = clean(ogTitle.split(/\s+[-–|]\s+/)[0]);
        out.methods.name = 'og';
      }
    }
    if (!out.headline) {
      const ogDesc = meta('og:description');
      if (ogDesc) { out.headline = clean(ogDesc).slice(0, 300); out.methods.headline = 'og'; }
    }
    if (!out.photo_url) out.photo_url = meta('og:image') || null;

    // Camada 3 — DOM visível
    const main = $('main') || document.body;
    if (!out.full_name) {
      const h1 = $('h1', main);
      if (h1) { out.full_name = clean(h1.textContent); out.methods.name = 'dom-h1'; }
    }
    if (!out.headline) {
      const headlineEl = $('.text-body-medium.break-words', main) ||
                         $('[data-generated-suggestion-target]', main);
      if (headlineEl) { out.headline = clean(headlineEl.textContent); out.methods.headline = 'dom'; }
    }
    if (!out.location) {
      const locEl = $('.text-body-small.inline.t-black--light.break-words', main);
      if (locEl) out.location = clean(locEl.textContent);
    }
    const companyLink = $$('a[href*="/company/"]', main)
      .map((a) => (a.getAttribute('href') || '').match(/\/company\/([^/?#]+)/))
      .filter(Boolean)[0];
    if (companyLink) {
      out.company_linkedin_slug = decodeURIComponent(companyLink[1]).toLowerCase();
      out.methods.company_slug = 'dom';
    }
    if (!out.company_name) {
      const expCompany = $('[aria-label*="Empresa atual"], [aria-label*="Current company"]', main);
      if (expCompany) { out.company_name = clean(expCompany.textContent); out.methods.company = 'dom-aria'; }
    }

    // Camada 4 — título da aba (último recurso, sempre existe)
    if (!out.full_name) {
      const t = clean(document.title).replace(/^\(\d+\+?\)\s*/, '');
      out.full_name = clean(t.split(/\s*[|–-]\s*/)[0]);
      out.methods.name = 'title';
    }

    console.debug('le:extract profile', out.methods);
    return out;
  }

  /* ══════════ extração: página de empresa ══════════ */
  function extractCompany() {
    const slug = (location.pathname.match(/\/company\/([^/?#]+)/) || [])[1];
    const out = {
      linkedin_slug: slug ? decodeURIComponent(slug).toLowerCase() : null,
      methods: {},
    };

    const org = findInGraph((n) => /Organization|Corporation/.test(String(n['@type'] || '')) && n.name);
    if (org) {
      out.company_name = clean(org.name);
      out.methods.name = 'jsonld';
      const site = org.url || (Array.isArray(org.sameAs) ? org.sameAs.find((u) => !SOCIAL_HOSTS.test(u)) : null);
      const host = site ? hostOf(site) : null;
      if (host && !SOCIAL_HOSTS.test(host)) { out.domain = host; out.methods.domain = 'jsonld'; }
    }

    if (!out.company_name) {
      const ogTitle = meta('og:title');
      if (ogTitle) { out.company_name = clean(ogTitle.split(/\s*[|]\s*/)[0]); out.methods.name = 'og'; }
    }
    if (!out.company_name) {
      const h1 = $('h1');
      if (h1) { out.company_name = clean(h1.textContent); out.methods.name = 'dom-h1'; }
    }

    // Site oficial: o LinkedIn envolve links externos em /redir/redirect
    if (!out.domain) {
      const candidates = $$('a[href]')
        .map((a) => decodeLinkedinRedirect(a.getAttribute('href') || ''))
        .filter((href) => /^https?:\/\//i.test(href))
        .map(hostOf)
        .filter((h) => h && !SOCIAL_HOSTS.test(h) && !/^licdn\.com$/.test(h));
      if (candidates.length) { out.domain = candidates[0]; out.methods.domain = 'dom-link'; }
    }

    console.debug('le:extract company', out.methods);
    return out;
  }

  /* ══════════ extração: cards de pessoas (aba People / busca) ══════════ */
  function extractPeopleCards() {
    const seen = new Set();
    const cards = [];
    $$('a[href*="/in/"]').forEach((a) => {
      const match = (a.getAttribute('href') || '').match(/\/in\/([^/?#]+)/);
      if (!match) return;
      const slug = decodeURIComponent(match[1]).toLowerCase();
      if (seen.has(slug)) return;

      const container = a.closest('li, .entity-result, .org-people-profile-card, div[data-view-name]') || a.parentElement;
      if (!container) return;
      const text = clean(container.innerText || '');
      if (!text) return;

      const lines = text.split('\n').map(clean).filter(Boolean);
      const name = clean(a.innerText) || lines[0] || null;
      if (!name || name.length < 3) return;
      const headline = lines.find((l) => l !== name && l.length > 3 && !/^\d/.test(l)) || null;

      seen.add(slug);
      cards.push({ linkedin_slug: slug, full_name: name, headline });
    });
    return cards.slice(0, 100);
  }

  /* ══════════ UI ══════════ */
  const CSS = `
  :host{all:initial}
  .wrap{position:fixed;top:80px;right:20px;width:340px;max-height:calc(100vh - 120px);
    overflow:auto;z-index:2147483000;font:14px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
    background:#fff;color:#111827;border:1px solid #e5e7eb;border-radius:14px;
    box-shadow:0 12px 32px rgba(15,23,42,.18)}
  .hd{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid #f1f5f9;
    position:sticky;top:0;background:#fff;border-radius:14px 14px 0 0}
  .logo{width:22px;height:22px;border-radius:6px;background:#1D4ED8;color:#fff;font-weight:700;
    font-size:12px;display:flex;align-items:center;justify-content:center;flex:none}
  .hd b{font-size:13px;font-weight:700;letter-spacing:.2px}
  .hd .sp{flex:1}
  .icon{border:0;background:transparent;cursor:pointer;font-size:16px;line-height:1;color:#6b7280;padding:2px 4px}
  .icon:hover{color:#111827}
  .bd{padding:14px}
  .who{margin-bottom:12px}
  .who h3{margin:0;font-size:15px;font-weight:700}
  .who p{margin:2px 0 0;font-size:12.5px;color:#4b5563}
  .badge{display:inline-block;margin-top:6px;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600}
  .b-founder,.b-c_level{background:#eef2ff;color:#3730a3}
  .b-vp,.b-director{background:#ecfdf5;color:#065f46}
  .b-head,.b-manager{background:#fff7ed;color:#9a3412}
  .row{display:flex;align-items:center;gap:8px;padding:10px 0;border-top:1px solid #f1f5f9}
  .row .ic{width:18px;text-align:center;flex:none;opacity:.7}
  .row .val{flex:1;min-width:0;word-break:break-all;font-size:13px}
  .val.mask{color:#6b7280;letter-spacing:.5px}
  .conf{font-size:10.5px;font-weight:700;padding:1px 6px;border-radius:999px;flex:none}
  .c-hi{background:#dcfce7;color:#166534}
  .c-md{background:#fef9c3;color:#854d0e}
  .c-lo{background:#f1f5f9;color:#475569}
  .btn{border:0;border-radius:8px;padding:7px 11px;font-size:12.5px;font-weight:600;cursor:pointer;
    background:#1D4ED8;color:#fff;flex:none}
  .btn:hover{background:#1e40af}
  .btn.ghost{background:#f1f5f9;color:#334155}
  .btn.ghost:hover{background:#e2e8f0}
  .btn[disabled]{opacity:.55;cursor:default}
  .actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  .note{margin-top:10px;font-size:11.5px;color:#6b7280;line-height:1.4}
  .err{margin-top:10px;padding:9px 11px;background:#fef2f2;border:1px solid #fecaca;color:#991b1b;
    border-radius:8px;font-size:12px}
  .ok{margin-top:10px;padding:9px 11px;background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46;
    border-radius:8px;font-size:12px}
  .ft{padding:9px 14px;border-top:1px solid #f1f5f9;font-size:11.5px;color:#6b7280;display:flex;gap:6px}
  .ft a{color:#1D4ED8;text-decoration:none}
  .list{margin-top:6px}
  .item{display:flex;align-items:center;gap:8px;padding:8px 0;border-top:1px solid #f1f5f9}
  .item .info{flex:1;min-width:0}
  .item .info b{display:block;font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .item .info span{font-size:11px;color:#6b7280;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .kv{display:flex;gap:6px;font-size:12px;padding:3px 0}
  .kv b{color:#374151;font-weight:600;min-width:96px;flex:none}
  .kv span{color:#4b5563}
  .in{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #cbd5e1;border-radius:8px;font:inherit;font-size:13px;margin-top:6px}
  .tab{position:fixed;top:80px;right:0;z-index:2147483000;background:#1D4ED8;color:#fff;border:0;
    border-radius:10px 0 0 10px;padding:10px 8px;font-weight:700;font-size:12px;cursor:pointer;
    writing-mode:vertical-rl;box-shadow:0 6px 18px rgba(29,78,216,.35)}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid #cbd5e1;border-top-color:#1D4ED8;
    border-radius:50%;animation:sp .7s linear infinite;vertical-align:-2px}
  @keyframes sp{to{transform:rotate(360deg)}}
  @media (prefers-color-scheme: dark){
    .wrap{background:#0f172a;color:#e2e8f0;border-color:#1e293b}
    .hd{background:#0f172a;border-color:#1e293b}
    .row,.item,.ft,.list{border-color:#1e293b}
    .who p,.note,.ft{color:#94a3b8}
    .btn.ghost{background:#1e293b;color:#cbd5e1}
    .in{background:#0f172a;color:#e2e8f0;border-color:#334155}
  }`;

  let host = null, shadow = null, panel = null, minimized = false;
  let state = { view: 'idle', data: null, error: null, busy: false, msg: null };

  function ensurePanel() {
    if (host && document.documentElement.contains(host)) return;
    host = document.createElement('div');
    host.id = 'leadenricher-root';
    shadow = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = CSS;
    shadow.appendChild(style);
    panel = document.createElement('div');
    shadow.appendChild(panel);
    document.documentElement.appendChild(host);
  }

  function confClass(v) { return v >= 85 ? 'c-hi' : v >= 60 ? 'c-md' : 'c-lo'; }
  function confLabel(v) { return v >= 85 ? 'ALTA' : v >= 60 ? 'MÉDIA' : 'BAIXA'; }
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  function render() {
    ensurePanel();
    if (minimized) {
      panel.innerHTML = '<button class="tab" data-act="open">LeadEnricher</button>';
      bind();
      return;
    }

    let body = '';
    if (state.view === 'loading') {
      body = '<div class="bd"><span class="spin"></span> Consultando…</div>';
    } else if (state.view === 'unpaired') {
      body = `<div class="bd">
        <div class="who"><h3>Conecte sua conta</h3>
        <p>Abra o LeadEnricher, vá em Configurações → Extensão e gere um código de pareamento.</p></div>
        <div class="actions"><button class="btn" data-act="popup">Abrir instruções</button></div>
        <div class="note">O pareamento é feito uma vez por navegador.</div></div>`;
    } else if (state.view === 'person') {
      body = renderPerson();
    } else if (state.view === 'company') {
      body = renderCompany();
    } else if (state.view === 'people') {
      body = renderPeople();
    } else {
      body = '<div class="bd"><div class="note">Abra um perfil ou uma página de empresa no LinkedIn.</div></div>';
    }

    panel.innerHTML = `<div class="wrap">
      <div class="hd">
        <span class="logo">L</span><b>LeadEnricher</b><span class="sp"></span>
        <button class="icon" data-act="refresh" title="Atualizar">⟳</button>
        <button class="icon" data-act="min" title="Minimizar">—</button>
      </div>
      ${body}
      <div class="ft">
        <span class="sp" style="flex:1"></span>
        <a href="#" data-act="app">Abrir app</a>
      </div>
    </div>`;
    bind();
  }

  function contactRow(icon, item, kind) {
    if (!item) return '';
    const revealed = !!item.value;
    const value = revealed ? item.value : (item.masked || '—');
    const conf = item.confidence || 0;
    return `<div class="row">
      <span class="ic">${icon}</span>
      <span class="val ${revealed ? '' : 'mask'}">${esc(value)}</span>
      ${conf ? `<span class="conf ${confClass(conf)}">${confLabel(conf)}</span>` : ''}
      ${revealed
        ? `<button class="btn ghost" data-act="copy" data-value="${esc(item.value)}">Copiar</button>`
        : `<button class="btn" data-act="reveal" data-kind="${kind}">Revelar</button>`}
    </div>`;
  }

  function renderPerson() {
    const d = state.data || {};
    const badge = d.seniority && d.seniority !== 'other'
      ? `<span class="badge b-${d.seniority}">${({
          founder: 'FUNDADOR', c_level: 'C-LEVEL', vp: 'VP',
          director: 'DIRETOR', head: 'HEAD', manager: 'GESTOR',
        })[d.seniority] || ''}</span>` : '';

    if (d.blocked) {
      return `<div class="bd"><div class="err">Esta pessoa solicitou a remoção dos dados (LGPD).
        Não exibimos contatos dela.</div></div>`;
    }

    const needsDomain = d.needs_domain && !d.company_domain;
    return `<div class="bd">
      <div class="who">
        <h3>${esc(d.full_name || 'Perfil')}</h3>
        <p>${esc(d.title || '')}${d.company_name ? ' · ' + esc(d.company_name) : ''}</p>
        ${badge}
      </div>
      ${needsDomain ? `
        <div class="note">Não sabemos o site desta empresa — sem ele não dá para montar o e-mail.</div>
        <input class="in" data-role="domain" placeholder="ex.: empresa.com.br"/>
        <div class="actions">
          <button class="btn" data-act="setdomain">Usar este site</button>
          <button class="btn ghost" data-act="finddomain">Descobrir sozinho</button>
        </div>` : `
        ${contactRow('✉', d.email, 'email')}
        ${contactRow('☎', d.phone, 'phone')}
        <div class="actions">
          ${d.person_id ? '<button class="btn ghost" data-act="save">Salvar na pipeline</button>' : ''}
          ${d.person_id ? '<button class="btn ghost" data-act="report">Dado errado</button>' : ''}
        </div>`}
      ${d.phone && d.phone.is_company_phone ? '<div class="note">☎ é o telefone da empresa, não o direto da pessoa.</div>' : ''}
      ${state.error ? `<div class="err">${esc(state.error)}</div>` : ''}
      ${state.msg ? `<div class="ok">${esc(state.msg)}</div>` : ''}
      ${d.company_domain ? `<div class="note">Empresa: ${esc(d.company_domain)}${d.known_pattern ? ' · padrão de e-mail conhecido' : ''}</div>` : ''}
    </div>`;
  }

  function renderCompany() {
    const c = state.data || {};
    const people = c.people || [];
    return `<div class="bd">
      <div class="who"><h3>${esc(c.name || 'Empresa')}</h3>
      <p>${esc(c.sector || '')}${c.location ? ' · ' + esc(c.location) : ''}</p></div>
      ${c.domain ? `<div class="kv"><b>Site</b><span>${esc(c.domain)}</span></div>` : ''}
      ${c.cnpj ? `<div class="kv"><b>CNPJ</b><span>${esc(c.cnpj)}</span></div>` : ''}
      ${c.razao_social ? `<div class="kv"><b>Razão social</b><span>${esc(c.razao_social)}</span></div>` : ''}
      ${c.situacao ? `<div class="kv"><b>Situação</b><span>${esc(c.situacao)}</span></div>` : ''}
      ${c.porte ? `<div class="kv"><b>Porte</b><span>${esc(c.porte)}</span></div>` : ''}
      ${(c.phones || []).slice(0, 2).map((p) => `<div class="kv"><b>Telefone</b><span>${esc(p.formatted || p.e164)}</span></div>`).join('')}
      ${c.main_email ? `<div class="kv"><b>E-mail</b><span>${esc(c.main_email)}</span></div>` : ''}
      ${c.email_pattern ? `<div class="kv"><b>Padrão</b><span>${esc(c.email_pattern)}@${esc(c.domain)} (${c.pattern_confidence}%)</span></div>` : ''}
      ${people.length ? `<div class="list">
        <div class="note" style="margin:10px 0 0">Decisores conhecidos (${people.length})</div>
        ${people.map((p) => `<div class="item">
          <div class="info"><b>${esc(p.full_name)}</b><span>${esc(p.title || '')}</span></div>
          <button class="btn ghost" data-act="revealperson" data-id="${p.person_id}">Revelar</button>
        </div>`).join('')}
      </div>` : '<div class="note">Nenhum decisor mapeado ainda. Abra a aba “Pessoas” para capturar.</div>'}
      ${state.error ? `<div class="err">${esc(state.error)}</div>` : ''}
      ${state.msg ? `<div class="ok">${esc(state.msg)}</div>` : ''}
    </div>`;
  }

  function renderPeople() {
    const n = (state.data && state.data.cards ? state.data.cards.length : 0);
    return `<div class="bd">
      <div class="who"><h3>Pessoas visíveis</h3>
      <p>${n} perfil(is) nesta tela</p></div>
      <div class="actions">
        <button class="btn ghost" data-act="capture" ${state.busy ? 'disabled' : ''}>Capturar (grátis)</button>
        <button class="btn" data-act="bulk" ${state.busy || !n ? 'disabled' : ''}>Revelar até ${Math.min(n, BULK_MAX)}</button>
      </div>
      <div class="note">Capturar só registra nome e cargo. Revelar busca e-mail e telefone
      de cada pessoa, respeitando um intervalo entre os perfis para não sobrecarregar o
      LinkedIn.</div>
      ${state.msg ? `<div class="ok">${esc(state.msg)}</div>` : ''}
      ${state.error ? `<div class="err">${esc(state.error)}</div>` : ''}
    </div>`;
  }

  function bind() {
    $$('[data-act]', shadow).forEach((el) => {
      el.addEventListener('click', (ev) => {
        ev.preventDefault();
        handle(el.getAttribute('data-act'), el);
      });
    });
  }

  /* ══════════ ações ══════════ */
  async function handle(act, el) {
    if (act === 'min') { minimized = true; render(); return; }
    if (act === 'open') { minimized = false; render(); return; }
    if (act === 'refresh') { run(true); return; }
    if (act === 'app') { window.open('https://leadenricher.app/app', '_blank', 'noopener'); return; }
    if (act === 'popup') { state.msg = 'Clique no ícone da extensão, ao lado da barra de endereços.'; render(); return; }

    if (act === 'copy') {
      const value = el.getAttribute('data-value') || '';
      try { await navigator.clipboard.writeText(value); state.msg = 'Copiado.'; }
      catch (_) { state.msg = value; }
      state.error = null; render(); return;
    }

    if (act === 'setdomain' || act === 'finddomain') {
      const input = $('[data-role="domain"]', shadow);
      const typed = input ? clean(input.value) : '';
      await run(true, {
        company_domain: act === 'setdomain' ? typed : null,
        deep: act === 'finddomain',
      });
      return;
    }

    if (act === 'reveal') { await doReveal(el.getAttribute('data-kind')); return; }
    if (act === 'revealperson') { await doRevealPerson(Number(el.getAttribute('data-id'))); return; }
    if (act === 'save') { await doSave(); return; }
    if (act === 'report') { await doReport(); return; }
    if (act === 'capture') { await doCapture(false); return; }
    if (act === 'bulk') { await doCapture(true); return; }
  }

  async function doReveal(kind) {
    const d = state.data || {};
    if (!d.person_id) return;
    state.busy = true; state.error = null; state.msg = null; render();

    const resp = await send('reveal', {
      person_id: d.person_id,
      kind: kind || 'both',
      company_domain: d.company_domain || null,
    });
    state.busy = false;

    if (!resp.ok) {
      state.error = resp.detail || 'Não conseguimos revelar agora.';
      render(); return;
    }

    const r = resp.data;
    if (r.emails && r.emails.length) {
      d.email = { value: r.emails[0].email, confidence: r.emails[0].confidence, status: r.emails[0].status };
    }
    if (r.phones && r.phones.length) {
      d.phone = {
        value: r.phones[0].formatted || r.phones[0].e164,
        confidence: r.phones[0].confidence,
        is_company_phone: r.phones[0].is_company_phone,
      };
    }
    state.msg = r.success ? 'Contato revelado.' : r.message;
    if (!r.success) state.error = r.message;
    render();
  }

  async function doRevealPerson(personId) {
    if (!personId) return;
    state.busy = true; state.error = null; state.msg = null; render();
    const resp = await send('reveal', { person_id: personId, kind: 'both' });
    state.busy = false;
    if (!resp.ok) {
      state.error = resp.detail || 'Falhou.';
    } else {
      const r = resp.data;
      const email = r.emails && r.emails[0] ? r.emails[0].email : null;
      state.msg = email ? `${r.full_name}: ${email}` : r.message;
      try { if (email) await navigator.clipboard.writeText(email); } catch (_) {}
    }
    render();
  }

  async function doSave() {
    const d = state.data || {};
    if (!d.person_id) return;
    state.busy = true; render();
    const resp = await send('save', { person_id: d.person_id });
    state.busy = false;
    state.msg = resp.ok ? 'Salvo na sua pipeline.' : null;
    state.error = resp.ok ? null : (resp.detail || 'Não conseguimos salvar.');
    render();
  }

  async function doReport() {
    const d = state.data || {};
    if (!d.person_id) return;
    if (!confirm('Reportar este contato como incorreto e removê-lo da base?')) return;
    const resp = await send('report', { person_id: d.person_id, kind: 'linkedin', reason: 'reportado na extensão' });
    state.msg = resp.ok ? 'Registrado. O contato foi removido.' : null;
    state.error = resp.ok ? null : (resp.detail || 'Falhou.');
    render();
  }

  async function doCapture(withReveal) {
    const cards = (state.data && state.data.cards) || [];
    if (!cards.length) return;
    const batch = cards.slice(0, BULK_MAX);
    state.busy = true; state.error = null;

    let captured = 0, revealed = 0;
    for (let i = 0; i < batch.length; i++) {
      const card = batch[i];
      state.msg = `${withReveal ? 'Revelando' : 'Capturando'} ${i + 1} de ${batch.length}…`;
      render();

      const resolved = await send('resolve', {
        linkedin_slug: card.linkedin_slug,
        full_name: card.full_name,
        headline: card.headline,
        company_name: (state.data && state.data.company_name) || null,
        company_linkedin_slug: (state.data && state.data.company_slug) || null,
      });
      if (!resolved.ok) {
        if (resolved.error === 'not_paired') { state.view = 'unpaired'; state.busy = false; render(); return; }
        continue;
      }
      captured++;

      if (withReveal && resolved.data && resolved.data.person_id) {
        const r = await send('reveal', { person_id: resolved.data.person_id, kind: 'both' });
        if (r.ok && r.data.success) revealed++;
        // Intervalo com variação: nunca em ritmo de robô
        await sleep(BULK_MIN_DELAY + Math.floor(Math.random() * BULK_JITTER));
      } else {
        await sleep(250);
      }
    }

    state.busy = false;
    state.msg = withReveal
      ? `${revealed} de ${batch.length} revelado(s). Veja a lista completa no app.`
      : `${captured} perfil(is) capturado(s) — sem custo.`;
    render();
  }

  /* ══════════ roteamento ══════════ */
  function pageType() {
    const p = location.pathname;
    if (/\/company\/[^/]+\/people/.test(p)) return 'people';
    if (/\/search\/results\/people/.test(p)) return 'people';
    if (/^\/in\//.test(p)) return 'profile';
    if (/^\/company\//.test(p)) return 'company';
    return null;
  }

  async function run(force, extra) {
    const type = pageType();
    if (!type) { if (host) host.remove(); host = null; return; }

    state.error = null; state.msg = null;
    state.view = 'loading'; render();

    const status = await send('status');
    if (!status.ok || !status.data.paired) { state.view = 'unpaired'; render(); return; }

    if (type === 'profile') {
      const profile = extractProfile();
      const resp = await send('resolve', Object.assign({
        linkedin_slug: profile.linkedin_slug,
        linkedin_url: location.origin + location.pathname,
        full_name: profile.full_name,
        headline: profile.headline,
        title: profile.title,
        company_name: profile.company_name,
        company_linkedin_slug: profile.company_linkedin_slug,
        location: profile.location,
        photo_url: profile.photo_url,
        deep: false,
      }, extra || {}));

      if (!resp.ok) {
        state.view = resp.error === 'not_paired' ? 'unpaired' : 'person';
        state.data = state.data || {};
        state.error = resp.detail || 'Não conseguimos consultar agora.';
        render(); return;
      }
      const d = resp.data;
      state.data = {
        person_id: d.person_id,
        full_name: d.full_name || profile.full_name,
        title: d.title || profile.title,
        seniority: d.seniority,
        company_name: d.company_name || profile.company_name,
        company_domain: d.company_domain,
        known_pattern: d.known_pattern,
        needs_domain: d.needs_domain,
        blocked: d.blocked,
        email: d.email && d.email.has ? { masked: d.email.masked, confidence: d.email.confidence } : { masked: null, confidence: 0 },
        phone: d.phone && d.phone.has ? { masked: d.phone.masked, confidence: d.phone.confidence, is_company_phone: d.phone.is_company_phone } : { masked: null, confidence: 0 },
      };
      state.view = 'person';
      render();
      return;
    }

    if (type === 'company') {
      const info = extractCompany();
      const resp = await send('company', Object.assign({
        domain: info.domain || null,
        company_name: info.company_name,
        linkedin_slug: info.linkedin_slug,
        deep: false,
      }, extra || {}));
      if (!resp.ok) {
        state.view = 'company';
        state.data = { name: info.company_name, domain: info.domain };
        state.error = resp.detail || 'Não conseguimos consultar agora.';
        render(); return;
      }
      state.data = resp.data;
      state.view = 'company';
      render();
      return;
    }

    if (type === 'people') {
      const info = extractCompany();
      state.data = {
        cards: extractPeopleCards(),
        company_name: info.company_name,
        company_slug: info.linkedin_slug,
      };
      state.view = 'people';
      render();
    }
  }

  /* SPA do LinkedIn troca de página sem recarregar: observa a URL */
  let lastUrl = location.href;
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      state = { view: 'idle', data: null, error: null, busy: false, msg: null };
      run(false);
    }
  }, 900);

  run(false);
})();
