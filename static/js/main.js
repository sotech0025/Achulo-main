// ACHOULO — small progressive-enhancement helpers.
document.addEventListener("DOMContentLoaded", function () {
  // Auto-dismiss flash messages after a few seconds.
  document.querySelectorAll("main > div > div.rounded-lg").forEach(function (el) {
    setTimeout(function () {
      el.style.transition = "opacity 0.4s ease";
      el.style.opacity = "0";
    }, 4000);
  });
});
