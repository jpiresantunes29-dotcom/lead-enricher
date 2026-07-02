/* Data Spine (§3) — trilho de circuito vertical energizado pelo scroll.
   O pulso dourado percorre o trilho conforme o usuário rola; quando passa
   por uma seção, ela "liga" (.is-live). Desktop only; respeita reduced-motion. */

export function initSpine(env) {
  if (env && (env.reducedMotion || env.coarsePointer)) return;
  if (matchMedia('(max-width: 980px)').matches) return;

  const track = document.createElement('div');
  track.className = 'spine-track';
  track.setAttribute('aria-hidden', 'true');
  const pulse = document.createElement('div');
  pulse.className = 'spine-pulse';
  track.appendChild(pulse);
  document.body.appendChild(track);

  const sections = [...document.querySelectorAll('[data-section]')];
  let ticking = false;

  function update() {
    ticking = false;
    const max = document.documentElement.scrollHeight - innerHeight;
    const p = max > 0 ? Math.min(scrollY / max, 1) : 0;
    pulse.style.top = (p * 100).toFixed(2) + '%';

    // Seções "ligam" quando o conteúdo cruza 60% do viewport
    const threshold = innerHeight * 0.6;
    for (const s of sections) {
      const r = s.getBoundingClientRect();
      if (r.top < threshold && r.bottom > 0) s.classList.add('is-live');
    }
  }

  addEventListener('scroll', () => {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(update);
    }
  }, { passive: true });
  addEventListener('resize', update);
  update();
}
