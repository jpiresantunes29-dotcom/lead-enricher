/* ════════════════════════════════════════════════════════════════
   Landing corporativa — orquestrador
   Espec: docs/DESIGN_LANDING_V3.md §7-§8
   Sem efeitos decorativos (spotlight, botões magnéticos, smooth scroll,
   trilho e seção fixada foram removidos no visual minimalista).
   ════════════════════════════════════════════════════════════════ */

// ── Detecção de capacidade e preferência (§8) ──────────────────────
export const env = {
  reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
};

// ── Reveals por IntersectionObserver ───────────────────────────────
function initReveals() {
  const els = document.querySelectorAll('[data-reveal]');
  if (!('IntersectionObserver' in window)) {
    els.forEach((el) => el.classList.add('in'));
    return;
  }
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          e.target.classList.add('in');
          io.unobserve(e.target);
        }
      }
    },
    { threshold: 0.15, rootMargin: '0px 0px -40px 0px' }
  );
  els.forEach((el) => io.observe(el));
}

// ── Contadores com decode (§5.7) ───────────────────────────────────
function initCounters() {
  const els = document.querySelectorAll('[data-count]');
  if (!els.length) return;
  const animate = (el) => {
    const target = parseInt(el.dataset.count, 10);
    const suffix = el.dataset.suffix || '';
    if (env.reducedMotion) {
      el.textContent = target + suffix;
      return;
    }
    const t0 = performance.now();
    const dur = 1200;
    const tick = (t) => {
      const p = Math.min((t - t0) / dur, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased) + suffix;
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          animate(e.target);
          io.unobserve(e.target);
        }
      }
    },
    { threshold: 0.5 }
  );
  els.forEach((el) => io.observe(el));
}

// ── Status real da API no footer (§5.12) ───────────────────────────
async function initFooterStatus() {
  const el = document.getElementById('footer-status');
  if (!el) return;
  try {
    const r = await fetch('/health');
    const j = await r.json();
    if (j.status === 'ok') {
      el.textContent = 'Todos os sistemas operando';
      el.classList.add('ok');
      return;
    }
  } catch {
    /* offline / erro — cai no fallback abaixo */
  }
  el.textContent = 'Status indisponível';
}

// ── Boot ───────────────────────────────────────────────────────────
initReveals();
initCounters();
initFooterStatus();
