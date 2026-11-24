(function () {
  "use strict";

  var toggle = document.querySelector("[data-pend-toggle]");
  if (!toggle) { return; }

  var cards = Array.prototype.slice.call(
    document.querySelectorAll("[data-pend-posted][data-pend-total]"));
  if (!cards.length) { return; }

  function swap(card, on) {
    var amount = card.querySelector("[data-pend-amount]");
    var pct = card.querySelector("[data-pend-pct]");
    var bar = card.querySelector("[data-pend-bar]");

    var text = card.getAttribute(on ? "data-pend-total" : "data-pend-posted");
    if (amount && text !== null) { amount.textContent = text; }

    var share = card.getAttribute(
      on ? "data-pend-total-pct" : "data-pend-posted-pct");
    if (share !== null) {
      if (pct) { pct.textContent = share; }
      if (bar) { bar.style.width = share + "%"; }
    }

    var sign = card.getAttribute(
      on ? "data-pend-total-sign" : "data-pend-posted-sign");
    if (amount && sign) {
      amount.classList.remove("g", "o");
      amount.classList.add(sign);
    }
  }

  function show() {
    var on = toggle.checked;
    for (var i = 0; i < cards.length; i++) { swap(cards[i], on); }
  }

  var hero = cards[0].parentNode;
  if (hero) { hero.setAttribute("aria-live", "polite"); }

  toggle.addEventListener("change", show);
}());
