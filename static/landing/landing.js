/* ════════════════════════════════════════════════════════════════
   Landing "Gold Signal" — orquestrador (fase L1)
   Espec: docs/DESIGN_LANDING_V3.md §7-§8
   Vendor (UMD via <script defer>): window.Lenis, window.gsap,
   window.ScrollTrigger — usados a partir da fase L2.
   ════════════════════════════════════════════════════════════════ */

// ── Detecção de capacidade e preferência (§8) ──────────────────────
export const env = {
  reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
  coarsePointer: matchMedia('(pointer: coarse)').matches,
  lowPower:
    (navigator.hardwareConcurrency || 8) < 4 ||
    (navigator.deviceMemory || 8) < 4,
};

// ── Smooth scroll (Lenis) ──────────────────────────────────────────
function initLenis() {
  if (env.reducedMotion || typeof window.Lenis !== 'function') return null;
  const lenis = new window.Lenis({ autoRaf: true });
  return lenis;
}

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

// ── Spotlight do cursor nos cards (§5.5) ───────────────────────────
function initSpotlight() {
  if (env.coarsePointer) return;
  document.querySelectorAll('[data-spotlight]').forEach((card) => {
    card.addEventListener('pointermove', (e) => {
      const r = card.getBoundingClientRect();
      card.style.setProperty('--mx', `${e.clientX - r.left}px`);
      card.style.setProperty('--my', `${e.clientY - r.top}px`);
    });
  });
}

// ── Botões magnéticos (≤6px) ───────────────────────────────────────
function initMagnetic() {
  if (env.coarsePointer || env.reducedMotion) return;
  document.querySelectorAll('[data-magnetic]').forEach((btn) => {
    btn.addEventListener('pointermove', (e) => {
      const r = btn.getBoundingClientRect();
      const dx = ((e.clientX - r.left) / r.width - 0.5) * 12;
      const dy = ((e.clientY - r.top) / r.height - 0.5) * 12;
      btn.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px)`;
    });
    btn.addEventListener('pointerleave', () => {
      btn.style.transform = '';
    });
  });
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
      el.textContent = '● all systems operational';
      el.classList.add('ok');
      return;
    }
  } catch {
    /* offline / erro — cai no fallback abaixo */
  }
  el.textContent = '● status indisponível';
}

// ── Boot sequence (§5.0) — 1ª visita, máx ~1.2s, skippable ─────────
function bootSequence() {
  if (env.reducedMotion || sessionStorage.getItem('le-boot')) return;
  sessionStorage.setItem('le-boot', '1');
  const overlay = document.createElement('div');
  overlay.className = 'boot';
  overlay.innerHTML = '<span class="boot-line mono"></span><span class="boot-caret"></span>';
  document.body.appendChild(overlay);
  const line = overlay.querySelector('.boot-line');
  const text = 'resolving leadenricher.app … 200 OK';
  let i = 0;
  const close = () => {
    clearInterval(timer);
    overlay.classList.add('out');
    setTimeout(() => overlay.remove(), 450);
  };
  const timer = setInterval(() => {
    line.textContent = text.slice(0, ++i);
    if (i >= text.length) {
      clearInterval(timer);
      setTimeout(close, 280);
    }
  }, 22);
  overlay.addEventListener('click', close);
}

// ── Boot ───────────────────────────────────────────────────────────
bootSequence();
initLenis();
initReveals();
initSpotlight();
initMagnetic();
initCounters();
initFooterStatus();

// Efeitos pesados carregam após o first paint e fora do caminho crítico (§8).
const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 200));
idle(async () => {
  const { initTerminal } = await import('./fx/terminal.js');
  initTerminal(document.getElementById('terminal-body'), env);

  if (!env.reducedMotion && !env.lowPower) {
    const { initNetwork } = await import('./fx/network.js');
    initNetwork(document.getElementById('fx-network'), env);

    const { initSpine } = await import('./fx/spine.js');
    initSpine(env);

    const { initJourney } = await import('./fx/journey.js');
    initJourney(env);

    const { initCursor } = await import('./fx/cursor.js');
    initCursor(env);
  }
});
