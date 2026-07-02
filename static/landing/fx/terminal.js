/* Terminal-demo do hero (§5.2) — loop de enriquecimento simulado.
   Dados de exemplo estáticos; o input real do form leva a /app?domain=… */

const RUNS = [
  {
    domain: 'nubank.com.br',
    lines: [
      ['├─ MX     → Google Workspace', 'ok', '0.8s'],
      ['├─ SPF    → v=spf1 include:_spf…', 'ok', ''],
      ['├─ DMARC  → p=reject', 'ok', ''],
      ['├─ PORTE  → 5.000+ funcionários', 'ok', '1.9s'],
      ['├─ DECISOR→ CTO encontrado · e-mail válido', 'ok', ''],
      ['└─ SCORE  → 87/100  ████████▌  ALTA PRIORIDADE', 'gold', ''],
    ],
  },
  {
    domain: 'stripe.com',
    lines: [
      ['├─ MX     → Google Workspace', 'ok', '0.6s'],
      ['├─ DMARC  → p=reject', 'ok', ''],
      ['├─ HOST   → AWS (ASN 16509)', 'ok', '1.2s'],
      ['├─ PORTE  → 8.000+ funcionários', 'ok', ''],
      ['├─ DECISOR→ Diretor Comercial · e-mail válido', 'ok', ''],
      ['└─ SCORE  → 92/100  █████████▏ ALTA PRIORIDADE', 'gold', ''],
    ],
  },
  {
    domain: 'kinghost.com.br',
    lines: [
      ['├─ MX     → Microsoft 365', 'ok', '0.7s'],
      ['├─ SPF    → v=spf1 include:spf…', 'ok', ''],
      ['├─ HOST   → infraestrutura própria', 'ok', '1.4s'],
      ['├─ PORTE  → 51–200 funcionários', 'ok', ''],
      ['├─ DECISOR→ Founder encontrado', 'ok', ''],
      ['└─ SCORE  → 64/100  ██████▍    MÉDIA PRIORIDADE', 'gold', ''],
    ],
  },
];

const CLASS = { ok: 't-ok', gold: 't-gold', dim: 't-dim' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function initTerminal(el, env) {
  if (!el) return;

  // Reduced motion: imprime um run completo, sem animação nem loop.
  if (env && env.reducedMotion) {
    const run = RUNS[0];
    el.innerHTML =
      `<span class="t-gold">$ enrich ${run.domain}</span>\n` +
      run.lines.map(([txt, kind]) => `<span class="${CLASS[kind]}">${txt}</span>`).join('\n');
    return;
  }

  let i = 0;
  // Loop infinito intencional: roda enquanto a página viver, pausando fora do viewport.
  for (;;) {
    const run = RUNS[i % RUNS.length];
    i += 1;

    el.innerHTML = '';
    // Digita o comando
    const cmd = `$ enrich ${run.domain}`;
    const cmdSpan = document.createElement('span');
    cmdSpan.className = 't-gold';
    el.appendChild(cmdSpan);
    for (const ch of cmd) {
      cmdSpan.textContent += ch;
      await sleep(34);
    }
    el.appendChild(document.createTextNode('\n'));
    await sleep(350);

    // Linhas chegam uma a uma
    for (const [txt, kind, time] of run.lines) {
      const line = document.createElement('span');
      line.className = CLASS[kind] || '';
      line.textContent = txt + (time ? `   ${time}` : '');
      el.appendChild(line);
      el.appendChild(document.createTextNode('\n'));
      await sleep(420);
    }

    await sleep(3200);

    // Pausa o loop enquanto o terminal não está visível
    while (!isInViewport(el) || document.hidden) await sleep(800);
  }
}

function isInViewport(el) {
  const r = el.getBoundingClientRect();
  return r.bottom > 0 && r.top < innerHeight;
}
