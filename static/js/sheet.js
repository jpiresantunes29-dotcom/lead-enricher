/* ════════════════════════════════════════════════════════════════
   LeadEnricher — Planilha (#sheet)

   O grid que substitui o Excel: as colunas do arquivo importado (com o
   rótulo e a ordem originais) seguidas das colunas do sistema, que o
   enriquecimento preenche. Edição inline, navegação por teclado, busca,
   ordenação e uma fila de enriquecimento que roda vários leads em
   paralelo — o gargalo é a rede, não a máquina, então o paralelismo é o
   que faz 900 empresas caberem em uma sessão.
   ════════════════════════════════════════════════════════════════ */

const SH_PER_PAGE_OPTIONS = [100, 200, 500];
const SH_WORKER_OPTIONS = [2, 4, 6, 8, 12];
const SH_STATUS = {
  imported: { label: 'Importado', cls: 'pend' },
  enriched: { label: 'Enriquecido', cls: 'ok' },
  partial: { label: 'Parcial', cls: 'warn' },
  failed: { label: 'Falhou', cls: 'err' },
};

let _sh = {
  loaded: false,
  batch: null, batches: [], columns: [], system: [], rows: [],
  total: 0, page: 1, perPage: 200, q: '', sort: null, dir: 'asc', only: null,
  pendingIds: [], enrichable: 0,
  sel: null,            // {r, c} célula selecionada
  editing: null,        // {r, c} célula em edição
  selectedRows: new Set(),
  workers: 6,
  queue: { running: false, cancel: false, done: 0, total: 0, ok: 0, fail: 0 },
};

function shCols() { return [..._sh.columns, ..._sh.system]; }
function shIsSystem(idx) { return idx >= _sh.columns.length; }

/* ══════ CARREGAMENTO ══════ */
async function loadSheet(opts = {}) {
  Object.assign(_sh, opts);
  const body = document.getElementById('sheet-body');
  if (!_sh.loaded) body.innerHTML = '<div class="panel"><div class="muted-box">Carregando planilha…</div></div>';
  const params = new URLSearchParams({ page: _sh.page, per_page: _sh.perPage });
  if (_sh.batch) params.set('batch_id', _sh.batch.id);
  if (_sh.q) params.set('q', _sh.q);
  if (_sh.sort) { params.set('sort', _sh.sort); params.set('dir', _sh.dir); }
  if (_sh.only) params.set('only', _sh.only);
  try {
    const resp = await authFetch('/api/sheet?' + params.toString());
    if (!resp.ok) { body.innerHTML = '<div class="panel"><div class="muted-box">Erro ao carregar a planilha.</div></div>'; return; }
    const data = await resp.json();
    _sh.loaded = true;
    _sh.batch = data.batch; _sh.batches = data.batches;
    _sh.columns = data.columns; _sh.system = data.system_columns;
    _sh.rows = data.rows; _sh.total = data.total;
    _sh.pendingIds = data.pending_ids; _sh.enrichable = data.enrichable;
    _sh.selectedRows.clear();
    renderSheet();
  } catch (e) {
    if (e.message !== 'not_authenticated') body.innerHTML = '<div class="panel"><div class="muted-box">Erro de conexão.</div></div>';
  }
}

/* ══════ RENDER ══════ */
function renderSheet() {
  const body = document.getElementById('sheet-body');
  if (!_sh.batches.length && !_sh.rows.length) { body.innerHTML = shEmptyState(); return; }

  const cols = shCols();
  const pages = Math.max(1, Math.ceil(_sh.total / _sh.perPage));
  const start = (_sh.page - 1) * _sh.perPage;

  const head = cols.map((col, idx) => {
    const on = _sh.sort === col.label;
    const arrow = on ? (_sh.dir === 'asc' ? ' ↑' : ' ↓') : '';
    return `<th class="${shIsSystem(idx) ? 'sys' : ''}" title="${esc(col.label)}">
      <button class="sh-th${on ? ' on' : ''}" data-sort="${esc(col.label)}">${esc(col.label)}${arrow}</button>
    </th>`;
  }).join('');

  const rows = _sh.rows.map((row, r) => {
    const st = SH_STATUS[row.status] || { label: row.status, cls: '' };
    const cells = cols.map((col, c) => {
      const value = row.values[col.label];
      const cls = [
        shIsSystem(c) ? 'sys' : '',
        col.kind === 'number' ? 'num' : '',
        _sh.sel && _sh.sel.r === r && _sh.sel.c === c ? 'sel' : '',
      ].filter(Boolean).join(' ');
      return `<td class="${cls}" data-r="${r}" data-c="${c}">${shCellHtml(value)}</td>`;
    }).join('');
    return `<tr data-id="${row.id}" class="${_sh.selectedRows.has(row.id) ? 'row-sel' : ''}">
      <th class="sh-rowhead">
        <input type="checkbox" class="sh-check" data-id="${row.id}" ${_sh.selectedRows.has(row.id) ? 'checked' : ''} />
        <span class="sh-rownum">${start + r + 1}</span>
        <span class="sh-state ${st.cls}" data-state="${row.id}" title="${st.label}"></span>
      </th>${cells}
    </tr>`;
  }).join('');

  body.innerHTML = `
    ${shToolbar()}
    <div class="sh-wrap" id="sh-wrap">
      <table class="sh-tbl">
        <thead><tr><th class="sh-corner">#</th>${head}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="sh-foot">
      <div class="sh-foot-l">
        ${_sh.selectedRows.size ? `<button class="ghost-btn danger" id="sh-del">Excluir ${_sh.selectedRows.size} linha(s)</button>` : ''}
        <button class="ghost-btn" id="sh-add">+ Nova linha</button>
        <span class="sh-count">${_sh.total.toLocaleString('pt-BR')} linha(s)${_sh.q ? ' no filtro' : ''} · ${cols.length} colunas</span>
      </div>
      <div class="sh-pager">
        <select class="sh-select" id="sh-perpage">
          ${SH_PER_PAGE_OPTIONS.map(n => `<option value="${n}" ${n === _sh.perPage ? 'selected' : ''}>${n} por página</option>`).join('')}
        </select>
        <button class="ghost-btn" id="sh-prev" ${_sh.page <= 1 ? 'disabled' : ''}>‹</button>
        <span class="sh-page">${_sh.page} / ${pages}</span>
        <button class="ghost-btn" id="sh-next" ${_sh.page >= pages ? 'disabled' : ''}>›</button>
      </div>
    </div>`;
  shBindGrid();
}

function shCellHtml(value) {
  if (value === null || value === undefined || value === '') return '';
  const text = String(value);
  // Data ISO vira dd/mm/aaaa; o valor guardado continua ISO
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const [y, m, d] = text.split('-');
    return `${d}/${m}/${y}`;
  }
  if (/^https?:\/\//i.test(text)) {
    return `<a href="${esc(text)}" target="_blank" rel="noopener" class="sh-link">${esc(text.replace(/^https?:\/\/(www\.)?/, ''))}</a>`;
  }
  return esc(text).replace(/\n/g, ' ⏎ ');
}

function shEmptyState() {
  return `<div class="panel panel-pad sh-empty">
    <div class="sh-empty-ico">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="9" x2="9" y2="21"/></svg>
    </div>
    <h3>Sua planilha vive aqui</h3>
    <p class="imp-done-sub">Importe o arquivo que você usa hoje no Excel. Todas as colunas vêm junto,
    do jeito que estão, e o sistema acrescenta as colunas que ele descobre sozinho.</p>
    <div class="imp-done-actions">
      <button class="app-btn-primary" onclick="nav('import')">Importar planilha</button>
      <button class="ghost-btn" onclick="downloadImportTemplate('xlsx')">Baixar modelo</button>
    </div>
  </div>`;
}

function shToolbar() {
  const batchOpts = _sh.batches.map(b =>
    `<option value="${b.id}" ${_sh.batch && b.id === _sh.batch.id ? 'selected' : ''}>
      ${esc(b.filename || 'planilha')} · ${b.row_count} linhas</option>`).join('');
  const filters = [['', 'Todas'], ['pending', 'Não enriquecidas'], ['enriched', 'Enriquecidas'], ['failed', 'Falhas']];
  return `
    <div class="sh-bar">
      <div class="sh-bar-l">
        <select class="sh-select" id="sh-batch">${batchOpts}</select>
        <div class="sh-search">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input id="sh-q" placeholder="Buscar em todas as colunas…" value="${esc(_sh.q)}" autocomplete="off" />
        </div>
        <div class="sh-chips">
          ${filters.map(([v, l]) => `<button class="sh-chip${(_sh.only || '') === v ? ' on' : ''}" data-only="${v}">${l}</button>`).join('')}
        </div>
      </div>
      <div class="sh-bar-r">
        <button class="ghost-btn" id="sh-import">Importar</button>
        <button class="ghost-btn" id="sh-export">Exportar</button>
        <select class="sh-select" id="sh-workers" title="Quantas empresas o navegador processa ao mesmo tempo">
          ${SH_WORKER_OPTIONS.map(n => `<option value="${n}" ${n === _sh.workers ? 'selected' : ''}>${n} em paralelo</option>`).join('')}
        </select>
        <button class="app-btn-primary" id="sh-enrich" ${_sh.enrichable ? '' : 'disabled'}>
          Enriquecer ${_sh.enrichable.toLocaleString('pt-BR')}
        </button>
      </div>
    </div>
    <div class="sh-progress" id="sh-progress" style="display:none">
      <div class="imp-prog-bar"><div class="imp-prog-fill" id="sh-prog-fill" style="width:0%"></div></div>
      <div class="imp-prog-meta">
        <span id="sh-prog-txt"></span>
        <button class="ghost-btn" id="sh-stop">Parar</button>
      </div>
    </div>`;
}

/* ══════ EVENTOS ══════ */
function shBindGrid() {
  const $ = id => document.getElementById(id);
  $('sh-batch')?.addEventListener('change', e => {
    _sh.batch = _sh.batches.find(b => b.id === e.target.value) || null;
    loadSheet({ page: 1 });
  });
  let qTimer = null;
  $('sh-q')?.addEventListener('input', e => {
    clearTimeout(qTimer);
    const value = e.target.value;
    qTimer = setTimeout(() => loadSheet({ q: value, page: 1 }), 300);
  });
  document.querySelectorAll('.sh-chip').forEach(chip => chip.addEventListener('click', () => {
    loadSheet({ only: chip.dataset.only || null, page: 1 });
  }));
  $('sh-import')?.addEventListener('click', () => nav('import'));
  $('sh-export')?.addEventListener('click', exportSheet);
  $('sh-workers')?.addEventListener('change', e => { _sh.workers = parseInt(e.target.value, 10); });
  $('sh-enrich')?.addEventListener('click', runSheetQueue);
  $('sh-stop')?.addEventListener('click', () => { _sh.queue.cancel = true; });
  $('sh-perpage')?.addEventListener('change', e => loadSheet({ perPage: parseInt(e.target.value, 10), page: 1 }));
  $('sh-prev')?.addEventListener('click', () => loadSheet({ page: _sh.page - 1 }));
  $('sh-next')?.addEventListener('click', () => loadSheet({ page: _sh.page + 1 }));
  $('sh-add')?.addEventListener('click', addSheetRow);
  $('sh-del')?.addEventListener('click', deleteSheetRows);

  document.querySelectorAll('.sh-th').forEach(th => th.addEventListener('click', () => {
    const label = th.dataset.sort;
    const dir = (_sh.sort === label && _sh.dir === 'asc') ? 'desc' : 'asc';
    loadSheet({ sort: label, dir, page: 1 });
  }));
  document.querySelectorAll('.sh-check').forEach(box => box.addEventListener('change', () => {
    const id = parseInt(box.dataset.id, 10);
    if (box.checked) _sh.selectedRows.add(id); else _sh.selectedRows.delete(id);
    box.closest('tr').classList.toggle('row-sel', box.checked);
    renderSheetFooter();
  }));

  const table = document.querySelector('.sh-tbl');
  table?.addEventListener('click', e => {
    const td = e.target.closest('td[data-r]');
    if (!td || e.target.closest('a')) return;
    shSelect(parseInt(td.dataset.r, 10), parseInt(td.dataset.c, 10));
  });
  table?.addEventListener('dblclick', e => {
    const td = e.target.closest('td[data-r]');
    if (td) shEdit(parseInt(td.dataset.r, 10), parseInt(td.dataset.c, 10));
  });
}

function renderSheetFooter() {
  const del = document.getElementById('sh-del');
  const foot = document.querySelector('.sh-foot-l');
  if (!foot) return;
  if (_sh.selectedRows.size && !del) { renderSheet(); return; }
  if (!_sh.selectedRows.size && del) { del.remove(); return; }
  if (del) del.textContent = `Excluir ${_sh.selectedRows.size} linha(s)`;
}

/* ══════ SELEÇÃO E EDIÇÃO ══════ */
function shCell(r, c) { return document.querySelector(`td[data-r="${r}"][data-c="${c}"]`); }

function shSelect(r, c) {
  if (_sh.editing) shCommitEdit();
  document.querySelector('.sh-tbl td.sel')?.classList.remove('sel');
  const td = shCell(r, c);
  if (!td) return;
  td.classList.add('sel');
  _sh.sel = { r, c };
  td.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function shMove(dr, dc) {
  if (!_sh.sel) return;
  const r = Math.min(Math.max(_sh.sel.r + dr, 0), _sh.rows.length - 1);
  const c = Math.min(Math.max(_sh.sel.c + dc, 0), shCols().length - 1);
  shSelect(r, c);
}

function shEdit(r, c, initial) {
  const cols = shCols();
  const col = cols[c];
  const row = _sh.rows[r];
  if (!col || !row) return;
  // Prioridade/Situação são calculados — o servidor recusa, avisamos antes
  if (col.field && ['priority', 'status'].includes(col.field)) {
    shToast(`"${col.label}" é calculada pelo enriquecimento e não pode ser editada.`);
    return;
  }
  shSelect(r, c);
  const td = shCell(r, c);
  const current = row.values[col.label];
  _sh.editing = { r, c, previous: current };
  td.classList.add('editing');
  td.innerHTML = `<input class="sh-input" value="${esc(initial !== undefined ? initial : (current ?? ''))}" />`;
  const input = td.querySelector('input');
  input.focus();
  // Editando com Enter/duplo clique: seleciona tudo. Começando pela digitação:
  // caret no fim, senão o resto da palavra entra antes da primeira letra.
  if (initial === undefined) input.select();
  else input.setSelectionRange(input.value.length, input.value.length);
  input.addEventListener('blur', () => shCommitEdit());
}

async function shCommitEdit(move) {
  if (!_sh.editing) return;
  const { r, c, previous } = _sh.editing;
  const td = shCell(r, c);
  const input = td?.querySelector('input');
  if (!input) { _sh.editing = null; return; }
  const value = input.value;
  _sh.editing = null;
  td.classList.remove('editing');

  const col = shCols()[c];
  const row = _sh.rows[r];
  if (String(previous ?? '') === value) { td.innerHTML = shCellHtml(previous); return; }

  row.values[col.label] = value;
  td.innerHTML = shCellHtml(value);
  td.classList.add('saving');
  try {
    const resp = await authFetch('/api/sheet/cell', {
      method: 'PATCH',
      body: JSON.stringify({ lead_id: row.id, column: col.label, value }),
    });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      row.values[col.label] = previous;
      td.innerHTML = shCellHtml(previous);
      shToast(json.detail || 'Não consegui salvar esta célula.');
    } else if (json.row) {
      // O servidor normaliza (domínio vira host, funcionários vira faixa)
      row.values = json.row.values;
      row.status = json.row.status;
      shRepaintRow(r);
    }
  } catch (e) {
    row.values[col.label] = previous;
    td.innerHTML = shCellHtml(previous);
    if (e.message !== 'not_authenticated') shToast('Erro de conexão ao salvar.');
  } finally {
    td.classList.remove('saving');
  }
  if (move) shMove(move[0], move[1]);
}

function shRepaintRow(r) {
  const row = _sh.rows[r];
  const cols = shCols();
  cols.forEach((col, c) => {
    const td = shCell(r, c);
    if (td && !td.classList.contains('editing')) td.innerHTML = shCellHtml(row.values[col.label]);
  });
  const state = document.querySelector(`[data-state="${row.id}"]`);
  const st = SH_STATUS[row.status] || { label: row.status, cls: '' };
  if (state) { state.className = `sh-state ${st.cls}`; state.title = st.label; }
}

document.addEventListener('keydown', e => {
  if (!document.getElementById('view-sheet')?.classList.contains('active')) return;
  if (_sh.editing) {
    if (e.key === 'Enter') { e.preventDefault(); shCommitEdit([1, 0]); }
    else if (e.key === 'Tab') { e.preventDefault(); shCommitEdit([0, e.shiftKey ? -1 : 1]); }
    else if (e.key === 'Escape') {
      const { r, c, previous } = _sh.editing;
      _sh.editing = null;
      const td = shCell(r, c);
      if (td) { td.classList.remove('editing'); td.innerHTML = shCellHtml(previous); }
    }
    return;
  }
  if (!_sh.sel) return;
  const keys = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
  if (keys[e.key]) { e.preventDefault(); shMove(...keys[e.key]); }
  else if (e.key === 'Enter') { e.preventDefault(); shEdit(_sh.sel.r, _sh.sel.c); }
  else if (e.key === 'Tab') { e.preventDefault(); shMove(0, e.shiftKey ? -1 : 1); }
  else if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
    // Digitar direto substitui, como no Excel. O preventDefault evita que o
    // navegador entregue essa mesma tecla ao input recém-criado (letra dobrada).
    e.preventDefault();
    shEdit(_sh.sel.r, _sh.sel.c, e.key);
  }
});

/* ══════ LINHAS ══════ */
async function addSheetRow() {
  try {
    const resp = await authFetch('/api/sheet/row', {
      method: 'POST',
      body: JSON.stringify({ batch_id: _sh.batch ? _sh.batch.id : null, values: {} }),
    });
    if (!resp.ok) { shToast('Não consegui criar a linha.'); return; }
    await loadSheet({ page: Math.ceil((_sh.total + 1) / _sh.perPage) });
    shSelect(_sh.rows.length - 1, 0);
  } catch (e) { if (e.message !== 'not_authenticated') shToast('Erro de conexão.'); }
}

async function deleteSheetRows() {
  const ids = [..._sh.selectedRows];
  if (!ids.length) return;
  if (!confirm(`Excluir ${ids.length} linha(s) da planilha? Isso não pode ser desfeito.`)) return;
  try {
    const resp = await authFetch('/api/sheet/rows/delete', { method: 'POST', body: JSON.stringify({ ids }) });
    if (!resp.ok) { shToast('Não consegui excluir.'); return; }
    _sh.selectedRows.clear();
    loadSheet();
  } catch (e) { if (e.message !== 'not_authenticated') shToast('Erro de conexão.'); }
}

async function exportSheet() {
  const token = await getToken();
  if (!token) { openAuthModal(); return; }
  const params = new URLSearchParams();
  if (_sh.batch) params.set('batch_id', _sh.batch.id);
  if (_sh.q) params.set('q', _sh.q);
  if (_sh.only) params.set('only', _sh.only);
  const resp = await fetch('/api/sheet/export?' + params.toString(), { headers: { Authorization: `Bearer ${token}` } });
  if (!resp.ok) { shToast('Nada para exportar.'); return; }
  const url = URL.createObjectURL(await resp.blob());
  const a = document.createElement('a');
  a.href = url; a.download = 'planilha_leadenricher.xlsx';
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
}

/* ══════ FILA DE ENRIQUECIMENTO (paralela) ══════ */
function shSleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function runSheetQueue() {
  if (_sh.queue.running) return;
  const ids = [..._sh.pendingIds];
  if (!ids.length) { shToast('Nenhuma linha pendente no filtro atual.'); return; }

  const quota = _profile && _profile.searches_limit > 0
    ? Math.max(_profile.searches_limit - _profile.searches_used, 0) : null;
  const aviso = quota !== null && quota < ids.length
    ? `\n\nVocê tem ${quota} busca(s) na cota e a fila tem ${ids.length}. Ela para quando a cota acabar.` : '';
  if (!confirm(`Enriquecer ${ids.length} empresa(s) com ${_sh.workers} em paralelo?` +
    `\nCada uma consome 1 busca da cota e leva de 10 a 30 segundos.${aviso}`)) return;

  _sh.queue = { running: true, cancel: false, done: 0, total: ids.length, ok: 0, fail: 0 };
  const bar = document.getElementById('sh-progress');
  if (bar) bar.style.display = 'block';
  document.getElementById('sh-enrich').disabled = true;

  const byId = new Map(_sh.rows.map((row, idx) => [row.id, idx]));
  let cursor = 0;
  let stopped = '';

  const paint = () => {
    const q = _sh.queue;
    const fill = document.getElementById('sh-prog-fill');
    const txt = document.getElementById('sh-prog-txt');
    if (fill) fill.style.width = Math.round(q.done / q.total * 100) + '%';
    if (txt) txt.textContent = `${q.done} de ${q.total} · ${q.ok} enriquecida(s)${q.fail ? ` · ${q.fail} sem dados` : ''}`;
  };

  async function worker() {
    while (cursor < ids.length && !_sh.queue.cancel && !stopped) {
      const id = ids[cursor++];
      const idx = byId.get(id);
      if (idx !== undefined) {
        const state = document.querySelector(`[data-state="${id}"]`);
        if (state) { state.className = 'sh-state running'; state.title = 'Coletando…'; }
      }
      let settled = false;
      for (let attempt = 0; attempt < 3 && !settled && !stopped; attempt++) {
        try {
          const resp = await authFetch(`/api/leads/${id}/enrich`, { method: 'POST' });
          if (resp.status === 402) { stopped = 'cota'; break; }
          if (resp.status === 429) { await shSleep(4000 + attempt * 4000); continue; }
          const json = await resp.json().catch(() => ({}));
          if (resp.ok && json.data) {
            _sh.queue.ok++;
            if (idx !== undefined) shApplyLead(idx, json.data);
          } else {
            _sh.queue.fail++;
            if (idx !== undefined) shMarkState(id, 'failed');
          }
          settled = true;
        } catch (e) {
          if (e.message === 'not_authenticated') { stopped = 'auth'; break; }
          _sh.queue.fail++; settled = true;
        }
      }
      if (!settled && !stopped) _sh.queue.fail++;
      if (stopped) break;
      _sh.queue.done++;
      paint();
    }
  }

  paint();
  await Promise.all(Array.from({ length: _sh.workers }, worker));

  _sh.queue.running = false;
  if (bar) bar.style.display = 'none';
  loadProfile();
  const q = _sh.queue;
  const motivo = stopped === 'cota' ? ' A fila parou: cota de buscas esgotada.'
    : stopped === 'auth' ? ' Sua sessão expirou.'
      : _sh.queue.cancel ? ' Fila interrompida por você.' : '';
  shToast(`${q.ok} enriquecida(s), ${q.fail} sem dados.${motivo}`, stopped ? 8000 : 5000);
  loadSheet();
}

function shMarkState(id, status) {
  const state = document.querySelector(`[data-state="${id}"]`);
  const st = SH_STATUS[status] || { label: status, cls: '' };
  if (state) { state.className = `sh-state ${st.cls}`; state.title = st.label; }
}

function shEmployeeText(value) {
  if (!value || typeof value !== 'object') return value ? String(value) : '';
  if (value.exact) return String(value.exact);
  if (value.min && value.max) return `${value.min}–${value.max}`;
  if (value.min) return `${value.min}+`;
  return value.band || value.raw || '';
}

/* Escreve o resultado da coleta nas colunas do sistema, sem tocar nas do usuário */
function shApplyLead(rowIdx, lead) {
  const row = _sh.rows[rowIdx];
  if (!row) return;
  _sh.system.forEach(col => {
    let value = lead[col.field];
    if (col.field === 'employee_count') value = shEmployeeText(value);
    if (col.field === 'status') value = (SH_STATUS[value] || {}).label || value;
    row.values[col.label] = value ?? '';
  });
  row.status = lead.status;
  shRepaintRow(rowIdx);
}

/* ══════ TOAST ══════ */
let _shToastTimer = null;
function shToast(message, ms = 4000) {
  let el = document.getElementById('sh-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'sh-toast';
    el.className = 'sh-toast';
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add('on');
  clearTimeout(_shToastTimer);
  _shToastTimer = setTimeout(() => el.classList.remove('on'), ms);
}
