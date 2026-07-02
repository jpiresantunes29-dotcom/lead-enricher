/* ============================================================
   Aurora background canvas — inspirado em Raycast
   3 blobs de gradiente que se movem suavemente atrás do hero
   ============================================================ */
(function () {
  const canvas = document.getElementById('hero-canvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  let width = 0, height = 0, dpr = window.devicePixelRatio || 1;

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    width = rect.width;1
    height = rect.height;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    ctx.scale(dpr, dpr);
  }
  resize();
  window.addEventListener('resize', resize);

  const blobs = [
    { x: 0.25, y: 0.30, r: 380, color: 'rgba(124, 58, 237, 0.55)', vx: 0.00018, vy: 0.00012, t: Math.random() * 1000 },
    { x: 0.70, y: 0.25, r: 340, color: 'rgba(236, 72, 153, 0.45)', vx: -0.00015, vy: 0.00020, t: Math.random() * 1000 },
    { x: 0.50, y: 0.55, r: 300, color: 'rgba(245, 158, 11, 0.30)', vx: 0.00022, vy: -0.00010, t: Math.random() * 1000 },
  ];

  function draw(now) {
    ctx.clearRect(0, 0, width, height);

    // Fundo escuro de base
    ctx.fillStyle = '#080808';
    ctx.fillRect(0, 0, width, height);

    // Renderiza blobs com glow pesado
    ctx.globalCompositeOperation = 'lighter';
    for (const b of blobs) {
      // Movimento suave (ondulado)
      b.t += 1;
      const ox = Math.sin(b.t * b.vx) * 0.12;
      const oy = Math.cos(b.t * b.vy) * 0.12;
      const cx = (b.x + ox) * width;
      const cy = (b.y + oy) * height;

      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, b.r);
      grad.addColorStop(0, b.color);
      grad.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, b.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalCompositeOperation = 'source-over';

    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);

  // Fade-up on intersection
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add('in');
        io.unobserve(e.target);
      }
    }
  }, { threshold: 0.12 });
  document.querySelectorAll('.fade-up').forEach(el => io.observe(el));
})();
