// Runs before styles and React; an external script also works with the bank's CSP.
(function () {
  var choice = "system";
  try {
    choice = localStorage.getItem("horizon.theme") || "system";
  } catch (_) {
    /* Storage can be disabled. */
  }
  if (["light", "dark", "system"].indexOf(choice) === -1) choice = "system";
  var dark =
    choice === "dark" ||
    (choice === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
})();
