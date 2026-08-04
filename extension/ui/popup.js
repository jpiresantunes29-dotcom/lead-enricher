/* Popup: pareamento do navegador e saldo de créditos. */
const $ = (id) => document.getElementById(id);

function send(action, payload) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ action, payload }, (resp) => resolve(resp || { ok: false }));
  });
}

function show(kind, text) {
  const el = $('msg');
  el.className = 'msg ' + kind;
  el.textContent = text;
}

async function refresh() {
  const status = await send('status');
  const paired = status.ok && status.data.paired;
  $('paired').style.display = paired ? 'block' : 'none';
  $('unpaired').style.display = paired ? 'none' : 'block';
  if (status.ok && status.data.api) $('api').value = status.data.api;

  if (!paired) return;

  const me = await send('me');
  if (me.ok) {
    const left = me.data.credits_left;
    $('credits').textContent = left === null ? '∞' : left;
    const providers = (me.data.premium_providers || []);
    $('plan').textContent =
      `Plano ${me.data.plan}${providers.length ? ' · provedores: ' + providers.join(', ') : ' · fontes gratuitas'}`;
  } else if (me.error === 'not_paired') {
    await send('unpair');
    refresh();
  } else {
    show('err', me.detail || 'Não conseguimos falar com o servidor.');
  }
}

$('pair').addEventListener('click', async () => {
  const code = ($('code').value || '').trim().toUpperCase();
  if (code.length < 4) { show('err', 'Digite o código gerado no app.'); return; }
  const apiUrl = ($('api').value || '').trim() || null;

  show('ok', 'Conectando…');
  const resp = await send('pair', { code, api: apiUrl, label: 'Chrome' });
  if (resp.ok) {
    show('ok', 'Conectado. Abra um perfil no LinkedIn.');
    $('code').value = '';
    refresh();
  } else {
    show('err', resp.detail || 'Código inválido ou expirado.');
  }
});

$('unpair').addEventListener('click', async () => {
  await send('unpair');
  show('ok', 'Navegador desconectado.');
  refresh();
});

refresh();
