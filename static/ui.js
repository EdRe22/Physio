// Formulare: Feld + Beschriftung gruppieren und auf großen Bildschirmen zweispaltig anordnen.
// Ohne dieses Skript bleibt alles funktionsfähig (einspaltig).
(function () {
  "use strict";
  function isBreaker(el) {
    return el.tagName === "BUTTON" || el.matches("a.btn, .row, input[type=hidden], hr, h2, h3, .muted:not(.helptext)") || el.tagName === "SCRIPT";
  }
  document.querySelectorAll("main form").forEach(function (form) {
    if (form.classList.contains("inline") || form.closest("table") || form.dataset.plain !== undefined) return;
    if (form.style.display === "inline" || form.style.display === "flex") return;
    var kids = Array.prototype.slice.call(form.children);
    var labels = kids.filter(function (k) { return k.tagName === "LABEL"; });
    var paras = kids.filter(function (k) { return k.tagName === "P" && k.querySelector("label"); });
    var count = labels.length + paras.length;
    if (count < 3) return;
    paras.forEach(function (p) { p.classList.add("field"); if (p.querySelector("textarea")) p.classList.add("wide"); });
    var current = null;
    kids.forEach(function (k) {
      if (k.tagName === "LABEL") {
        current = document.createElement("div");
        current.className = "field";
        form.insertBefore(current, k);
        current.appendChild(k);
      } else if (current && !isBreaker(k) && !k.classList.contains("field") && k.tagName !== "LABEL") {
        current.appendChild(k);
        if (k.tagName === "TEXTAREA") current.classList.add("wide");
      } else {
        current = null;
      }
    });
    form.classList.add("grid");
  });
})();
