(function () {
  "use strict";

  document.addEventListener("budget:swapped", wire);
  wire();

  function wire() {
  var all = document.querySelector("[data-bulkall]");
  var picks = Array.prototype.slice.call(document.querySelectorAll("[data-bulkpick]"));
  var label = document.querySelector("[data-bulkcount]");
  if (!picks.length) { return; }

  var idle = label ? label.textContent : "";

  function sync() {
    var n = picks.filter(function (box) { return box.checked; }).length;
    if (label) {
      label.textContent = n ? n + (n === 1 ? " row ticked" : " rows ticked") : idle;
    }
    if (all) {
      all.checked = n === picks.length && n > 0;
      all.indeterminate = n > 0 && n < picks.length;
    }
  }

  if (all) {
    all.addEventListener("change", function () {
      picks.forEach(function (box) { box.checked = all.checked; });
      sync();
    });
  }
  picks.forEach(function (box) { box.addEventListener("change", sync); });
  sync();
  }
})();
