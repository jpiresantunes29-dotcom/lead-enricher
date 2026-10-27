/* Alternador claro/escuro. O atributo inicial é aplicado por um script inline
 * no <head> (evita flash); aqui só ligamos os botões [data-theme-toggle]. */
(function () {
  "use strict";
  var KEY = "le_tema";
  var root = document.documentElement;

  function current() {
    var t = root.getAttribute("data-theme");
    if (t === "light" || t === "dark") return t;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function apply(t) {
    root.setAttribute("data-theme", t);
    try { localStorage.setItem(KEY, t); } catch (e) {}
    document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
      b.setAttribute("aria-pressed", t === "dark" ? "true" : "false");
    });
  }

  document.querySelectorAll("[data-theme-toggle]").forEach(function (b) {
    b.setAttribute("aria-pressed", current() === "dark" ? "true" : "false");
    b.addEventListener("click", function () {
      apply(current() === "dark" ? "light" : "dark");
    });
  });
})();
