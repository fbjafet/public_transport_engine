/* Small form helpers (no inline scripts are allowed by the Content-Security-Policy). */
(function () {
  "use strict";

  // 1. Double-submit guard: forms marked data-once disable their buttons after the first submit.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.hasAttribute || !form.hasAttribute("data-once")) return;
    if (form.getAttribute("data-submitted") === "1") {
      e.preventDefault();
      return;
    }
    form.setAttribute("data-submitted", "1");
    // Disable after the browser has collected the form data.
    setTimeout(function () {
      form.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = true; });
    }, 0);
  });

  // Back/forward cache: make the form usable again when the page is restored.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll("form[data-once]").forEach(function (f) {
      f.removeAttribute("data-submitted");
      f.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = false; });
    });
  });

  // 2. On the "log disruption" form, only offer stops that belong to the chosen route.
  var routeSel = document.getElementById("route-select");
  var stopSel = document.getElementById("stop-select");
  if (routeSel && stopSel) {
    var filterStops = function () {
      var routeId = routeSel.value;
      Array.prototype.forEach.call(stopSel.options, function (opt) {
        var routes = opt.getAttribute("data-routes");
        if (routes === null) return; // the "Network / multiple stops" option
        var show = routeId !== "" && routes.split(",").indexOf(routeId) !== -1;
        opt.hidden = !show;
        opt.disabled = !show;
      });
      var selected = stopSel.options[stopSel.selectedIndex];
      if (selected && selected.disabled) stopSel.value = "";
    };
    routeSel.addEventListener("change", filterStops);
    filterStops();
  }
})();
