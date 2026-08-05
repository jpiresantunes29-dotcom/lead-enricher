/* "A Jornada de um Domínio" (§5.4) — scrollytelling pinned, fase L3.
   Desktop ≥980px com GSAP+ScrollTrigger; no mobile/reduced-motion a seção
   permanece no layout vertical com reveals (fallback já no CSS/HTML). */

export function initJourney(env) {
  if (env && (env.reducedMotion || env.lowPower)) return;
  if (matchMedia('(max-width: 980px)').matches) return;
  const gsap = window.gsap;
  const ScrollTrigger = window.ScrollTrigger;
  if (!gsap || !ScrollTrigger) return;

  const section = document.querySelector('.journey');
  if (!section) return;
  const stages = [...section.querySelectorAll('.journey-stage')];
  if (stages.length < 2) return;

  gsap.registerPlugin(ScrollTrigger);
  section.classList.add('journey--pin');

  // Trilho de progresso 01→04
  const rail = document.createElement('div');
  rail.className = 'journey-rail mono';
  rail.setAttribute('aria-hidden', 'true');
  rail.innerHTML = stages
    .map((_, i) => `<span class="jr-dot" data-i="${i}">0${i + 1}</span>`)
    .join('<span class="jr-line"></span>');
  section.appendChild(rail);
  const dots = [...rail.querySelectorAll('.jr-dot')];

  // No modo pinned os reveals do IO não se aplicam — força visível
  section.querySelectorAll('[data-reveal]').forEach((el) => el.classList.add('in'));
  gsap.set(stages, { autoAlpha: 0, y: 48 });

  const tl = gsap.timeline({
    scrollTrigger: {
      trigger: section,
      start: 'top top',
      end: '+=300%',
      pin: true,
      scrub: 0.8,
      onUpdate(self) {
        const idx = Math.min(stages.length - 1, Math.floor(self.progress * stages.length));
        dots.forEach((d, i) => d.classList.toggle('on', i <= idx));
      },
    },
  });

  stages.forEach((stage, i) => {
    tl.to(stage, { autoAlpha: 1, y: 0, duration: 1, ease: 'power3.out' });

    // Contador numérico opcional acompanha a entrada da última estação
    if (i === stages.length - 1) {
      const counter = stage.querySelector('[data-jcount]');
      if (counter) {
        const target = parseInt(counter.dataset.jcount, 10) || 0;
        const proxy = { v: 0 };
        tl.to(proxy, {
          v: target,
          duration: 1.4,
          ease: 'power2.out',
          onUpdate: () => { counter.textContent = Math.round(proxy.v); },
        }, '<');
      }
    }

    tl.to({}, { duration: 1.2 }); // hold para leitura
    if (i < stages.length - 1) {
      tl.to(stage, { autoAlpha: 0, y: -48, duration: 0.7, ease: 'power2.in' });
    }
  });
}
