(function () {
  "use strict";

  var script = document.currentScript;
  var openButton = document.getElementById("plaid-open");
  var form = document.getElementById("plaid-result");
  var status = document.getElementById("plaid-status");

  if (!openButton || !form || !script) { return; }

  function say(message) {
    if (status) { status.textContent = message; }
  }

  if (typeof Plaid === "undefined") {
    openButton.disabled = true;
    say("Plaid's widget could not be loaded from cdn.plaid.com. A blocker or " +
        "a lost connection will do that; connecting a bank needs it.");
    return;
  }

  var token = script.getAttribute("data-link-token");
  if (!token) {
    openButton.disabled = true;
    say("No link token was issued, so there is nothing to open. The Plaid " +
        "settings may be incomplete.");
    return;
  }

  var handler = Plaid.create({
    token: token,
    onSuccess: function (publicToken, metadata) {
      say("Connected. Setting up the accounts…");
      form.elements.public_token.value = publicToken;
      if (metadata && metadata.institution) {
        form.elements.institution_id.value = metadata.institution.institution_id || "";
        form.elements.institution_name.value = metadata.institution.name || "";
      }
      openButton.disabled = true;
      form.submit();
    },
    onExit: function (err) {
      if (err) {
        say("Plaid ended with: " + (err.display_message || err.error_message ||
            err.error_code || "an unknown error") + ". Nothing was connected.");
      } else {
        say("Closed without connecting anything.");
      }
    },
  });

  openButton.addEventListener("click", function () {
    say("");
    handler.open();
  });
})();
