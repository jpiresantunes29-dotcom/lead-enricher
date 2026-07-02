/* Cursor custom (§6) — dot dourado + anel com atraso (lerp), fase L4.
   Desktop fine-pointer apenas; o anel expande sobre elementos clicáveis. */

export function initCursor(env) {
  if (env && (env.coarsePointer || env.reducedMotion)) return;

  const dot = document.createElement('div');
  dot.className = 'cursor-dot';
  const ring = document.createElement('div');
  ring.className = 'cursor-ring';
  document.body.append(dot, ring);
  document.documentElement.classList.add('has-cursor');

  let tx = -100, ty = -100;   // alvo (mouse)
  let rx = -100, ry = -100;   // posição do anel (lerp)

  addEventListener('pointermove', (e) => {
    tx = e.clientX;
    ty = e.clientY;
    dot.style.transform = `translate(${tx}px, ${ty}px)`;
  }, { passive: true });

  (function loop() {
    rx += (tx - rx) * 0.16;
    ry += (ty - ry) * 0.16;
    ring.style.transform = `translate(${rx}px, ${ry}px)`;
    requestAnimationFrame(loop);
  })();

  // Anel expande sobre interativos
  document.addEventListener('pointerover', (e) => {
    if (e.target.closest('a, button, input, [data-magnetic]')) ring.classList.add('big');
  });
  document.addEventListener('pointerout', (e) => {
    if (e.target.closest('a, button, input, [data-magnetic]')) ring.classList.remove('big');
  });
  document.addEventListener('pointerleave', () => {
    dot.style.opacity = '0';
    ring.style.opacity = '0';
  });
  document.addEventListener('pointerenter', () => {
    dot.style.opacity = '1';
    ring.style.opacity = '1';
  });
}
