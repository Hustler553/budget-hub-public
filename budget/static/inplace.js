(function () {
  "use strict";

  if (!window.fetch || !window.DOMParser || !window.FormData || !window.URL) {
    return;
  }
  var main = document.querySelector("main.page");
  if (!main) { return; }

  var busy = false;

  function path(url) {
    return new URL(url, location.href).pathname;
  }

  function csrfToken() {
    var field = document.querySelector('input[name="csrf_token"]');
    return field ? field.value : "";
  }

  function ask(url, options) {
    options = options || {};
    options.credentials = "same-origin";
    return fetch(url, options).then(function (response) {
      return response.text().then(function (text) {
        return {
          url: response.url,
          ok: response.ok,
          doc: new DOMParser().parseFromString(text, "text/html")
        };
      });
    });
  }

  function adopt(nodes) {
    return Array.prototype.map.call(nodes, function (node) {
      return document.importNode(node, true);
    });
  }

  function fill(target, nodes) {
    while (target.firstChild) { target.removeChild(target.firstChild); }
    adopt(nodes).forEach(function (node) { target.appendChild(node); });
  }

  function redraw(doc) {
    var fresh = doc.querySelector("main.page");
    if (!fresh) { return false; }
    fill(main, fresh.childNodes);
    upgrade();
    document.dispatchEvent(new CustomEvent("budget:swapped"));
    return true;
  }

  function settle(result) {
    if (result.ok && path(result.url) === location.pathname && redraw(result.doc)) {
      return;
    }
    location.assign(result.ok ? result.url : location.href);
  }

  function setBusy(on) {
    busy = on;
    if (on) { main.setAttribute("aria-busy", "true"); }
    else { main.removeAttribute("aria-busy"); }
  }

  function failed() {
    setBusy(false);
    window.alert("That didn't go through. Check your connection and try again.");
  }

  function forgetSavedScroll() {
    try { sessionStorage.removeItem("scroll:" + location.pathname); } catch (e) {  }
  }

  function formData(form, submitter) {
    var data = new FormData(form);
    if (submitter && submitter.name) { data.append(submitter.name, submitter.value); }
    return data;
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (event.defaultPrevented || !form || form.tagName !== "FORM") { return; }
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post") { return; }
    var inPanel = panel && panel.contains(form);
    if (!inPanel && !form.hasAttribute("data-inplace")) { return; }

    event.preventDefault();
    forgetSavedScroll();
    if (busy) { return; }
    setBusy(true);

    var base = inPanel ? panelUrl : location.href;
    var target = new URL(form.getAttribute("action") || base, base).href;

    ask(target, { method: "POST", body: formData(form, event.submitter) })
      .then(function (result) {
        setBusy(false);
        if (inPanel) { afterPanelSubmit(result); } else { settle(result); }
      })
      .catch(failed);
  });

  function upgrade() {
    var cells = main.querySelectorAll("td[data-cat]");
    Array.prototype.forEach.call(cells, function (cell) {
      cell.classList.add("catcell");
      cell.tabIndex = 0;
      cell.setAttribute("role", "button");
      cell.setAttribute("aria-label", "Change category");
    });
  }

  function openPicker(cell) {
    var template = document.getElementById("catoptions");
    if (!template || !template.content || cell.querySelector("select") || busy) { return; }
    var select = template.content.querySelector("select").cloneNode(true);
    var before = cell.getAttribute("data-category") || "";
    var shown = Array.prototype.slice.call(cell.childNodes);
    var done = false;

    function restore() {
      if (done) { return; }
      done = true;
      while (cell.firstChild) { cell.removeChild(cell.firstChild); }
      shown.forEach(function (node) { cell.appendChild(node); });
    }

    select.value = before;
    while (cell.firstChild) { cell.removeChild(cell.firstChild); }
    cell.appendChild(select);
    select.focus();

    select.addEventListener("change", function () {
      if (!select.value || select.value === before) { restore(); return; }
      done = true;
      var body = new URLSearchParams();
      body.append("csrf_token", csrfToken());
      body.append("category_id", select.value);
      setBusy(true);
      ask(cell.getAttribute("data-action"), { method: "POST", body: body })
        .then(function (result) { setBusy(false); settle(result); })
        .catch(failed);
    });
    select.addEventListener("blur", restore);
    select.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { restore(); cell.focus(); }
    });
  }

  var panel = null, scrim = null, panelBody = null, panelUrl = "", opener = null;

  function buildPanel() {
    scrim = document.createElement("div");
    scrim.className = "sidepanel-scrim";
    scrim.hidden = true;
    scrim.addEventListener("click", closePanel);

    panel = document.createElement("aside");
    panel.className = "sidepanel";
    panel.hidden = true;
    panel.tabIndex = -1;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "Edit transaction");

    var close = document.createElement("button");
    close.type = "button";
    close.className = "secondary sidepanel-close";
    close.textContent = "Close";
    close.addEventListener("click", closePanel);

    panelBody = document.createElement("div");
    panel.appendChild(close);
    panel.appendChild(panelBody);
    document.body.appendChild(scrim);
    document.body.appendChild(panel);

    panel.addEventListener("click", function (event) {
      var link = event.target.closest ? event.target.closest("a[href]") : null;
      if (link && path(link.href) === location.pathname) {
        event.preventDefault();
        closePanel();
      }
    });
  }

  function showPanel(result) {
    var fresh = result.doc.querySelector("main.page");
    if (!result.ok || !fresh || path(result.url) === location.pathname) {
      return false;
    }
    if (!panel) { buildPanel(); }
    fill(panelBody, fresh.childNodes);
    panelUrl = result.url;
    scrim.hidden = false;
    panel.hidden = false;
    panel.scrollTop = 0;
    panel.focus();
    return true;
  }

  function closePanel() {
    if (!panel || panel.hidden) { return; }
    panel.hidden = true;
    scrim.hidden = true;
    while (panelBody.firstChild) { panelBody.removeChild(panelBody.firstChild); }
    if (opener && document.contains(opener)) { opener.focus(); }
    opener = null;
  }

  function openPanel(link) {
    if (busy) { return; }
    opener = link;
    setBusy(true);
    ask(link.href)
      .then(function (result) {
        setBusy(false);
        if (!showPanel(result)) { location.assign(link.href); }
      })
      .catch(function () { setBusy(false); location.assign(link.href); });
  }

  function afterPanelSubmit(result) {
    if (showPanel(result)) { return; }
    if (!result.ok) { location.assign(location.href); return; }
    var said = adopt(result.doc.querySelectorAll("main.page > .flash"));
    closePanel();
    setBusy(true);
    ask(location.href)
      .then(function (list) {
        setBusy(false);
        settle(list);
        said.reverse().forEach(function (node) {
          main.insertBefore(node, main.firstChild);
        });
      })
      .catch(failed);
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    var target = event.target;
    if (!target.closest) { return; }

    var link = target.closest("a[data-panel]");
    if (link && main.contains(link)) {
      event.preventDefault();
      openPanel(link);
      return;
    }
    var cell = target.closest("td.catcell");
    if (cell && !target.closest("select, a, button")) { openPicker(cell); }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && panel && !panel.hidden) {
      closePanel();
      return;
    }
    var cell = event.target;
    if ((event.key === "Enter" || event.key === " ") && cell.classList &&
        cell.classList.contains("catcell")) {
      event.preventDefault();
      openPicker(cell);
    }
  });

  upgrade();
})();
