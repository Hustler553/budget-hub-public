(function () {
  "use strict";

  var KEY = "scroll:" + location.pathname;

  function save(event) {
    if (event.defaultPrevented) return;
    try {
      sessionStorage.setItem(KEY, String(window.scrollY));
    } catch (e) {
    }
  }

  document.addEventListener("submit", save);

  var saved = null;
  try {
    saved = sessionStorage.getItem(KEY);
    if (saved !== null) sessionStorage.removeItem(KEY);
  } catch (e) {
    return;
  }
  if (saved === null) return;

  var y = parseInt(saved, 10);
  if (isNaN(y)) return;

  function restore() {
    window.scrollTo(0, y);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", restore);
  } else {
    restore();
  }
})();
