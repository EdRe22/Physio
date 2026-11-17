/* Vorschau der Farbauswahl: setzt die Farbvariablen sofort, gespeichert wird erst mit „Speichern“. */
(function () {
  var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  var root = document.documentElement;
  var form = document.getElementById("palette-form");
  if (!form) return;
  var KEYS = ["bg", "surface", "line", "line-strong", "ink", "muted", "brand", "brand-2", "brand-ink", "brand-soft", "side-from", "side-to"];
  function apply(input) {
    var raw = input.getAttribute(dark ? "data-dark" : "data-light");
    if (!raw) return;
    var values = JSON.parse(raw);
    KEYS.forEach(function (k) { if (values[k]) root.style.setProperty("--" + k, values[k]); });
  }
  form.addEventListener("change", function (e) {
    if (e.target && e.target.name === "palette") apply(e.target);
  });
})();
