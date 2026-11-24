(function () {
  "use strict";

  function label() {
    var tables = document.querySelectorAll("table.rows");
    Array.prototype.forEach.call(tables, function (table) {
      var heads = table.querySelectorAll("thead th");
      if (!heads.length) { return; }
      var names = Array.prototype.map.call(heads, function (th) {
        return th.textContent.replace(/\s+/g, " ").trim();
      });
      Array.prototype.forEach.call(table.querySelectorAll("tbody tr"), function (row) {
        Array.prototype.forEach.call(row.children, function (cell, index) {
          if (names[index] && !cell.hasAttribute("data-label")) {
            cell.setAttribute("data-label", names[index]);
          }
        });
      });
    });
  }

  document.addEventListener("budget:swapped", label);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", label);
  } else {
    label();
  }
})();
