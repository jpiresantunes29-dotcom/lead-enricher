/* Malha de rede do hero (§5.2) — fase L1: campo de nós + arestas em Canvas 2D.
   Fase L2 adiciona: pulsos viajando entre nós e cursor-revelação com labels
   (MX, SPF, ASN). Pausa fora do viewport e em document.hidden (§8). */

const GOLD_DEEP = 'rgba(138,100,0,';
const GOLD = 'rgba(245,183,0,';

export function initNetwork(canvas, env) {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const isLow = env && env.lowPower;
  const N = isLow ? 60 : env && env.coarsePointer ? 90 : 180;
  const LINK_DIST = 130;
  let w = 0;
  let h = 0;
  let nodes = [];
  let mouse = { x: -9999, y: -9999 };
  let running = true;

  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    w = canvas.clientWidth;
    h = canvas.clientHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function seed() {
    nodes = Array.from({ length: N }, () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.18,
      vy: (Math.random() - 0.5) * 0.18,
    }));
  }

  function frame() {
    if (!running) return;
    ctx.clearRect(0, 0, w, h);

    for (const n of nodes) {
      n.x += n.vx;
      n.y += n.vy;
      if (n.x < 0 || n.x > w) n.vx *= -1;
      if (n.y < 0 || n.y > h) n.vy *= -1;
    }

    // Arestas
    ctx.lineWidth = 1;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > LINK_DIST * LINK_DIST) continue;
        const alpha = 0.10 * (1 - Math.sqrt(d2) / LINK_DIST);
        ctx.strokeStyle = GOLD_DEEP + alpha.toFixed(3) + ')';
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    // Nós — acendem perto do cursor (versão simples; labels na fase L2)
    for (const n of nodes) {
      const dx = n.x - mouse.x;
      const dy = n.y - mouse.y;
      const near = dx * dx + dy * dy < 180 * 180;
      ctx.fillStyle = near ? GOLD + '0.85)' : GOLD_DEEP + '0.45)';
      ctx.beginPath();
      ctx.arc(n.x, n.y, near ? 2.2 : 1.4, 0, Math.PI * 2);
      ctx.fill();
    }

    requestAnimationFrame(frame);
  }

  // Pausa fora do viewport / aba oculta
  const io = new IntersectionObserver(([e]) => {
    const visible = e.isIntersecting && !document.hidden;
    if (visible && !running) {
      running = true;
      requestAnimationFrame(frame);
    } else if (!visible) {
      running = false;
    }
  });
  io.observe(canvas);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) running = false;
    else if (!running) {
      running = true;
      requestAnimationFrame(frame);
    }
  });

  if (!(env && env.coarsePointer)) {
    canvas.parentElement.addEventListener('pointermove', (e) => {
      const r = canvas.getBoundingClientRect();
      mouse.x = e.clientX - r.left;
      mouse.y = e.clientY - r.top;
    });
    canvas.parentElement.addEventListener('pointerleave', () => {
      mouse.x = -9999;
      mouse.y = -9999;
    });
  }

  addEventListener('resize', () => {
    resize();
    seed();
  });
  resize();
  seed();
  requestAnimationFrame(frame);
}
