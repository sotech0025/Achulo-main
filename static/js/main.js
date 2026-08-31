// ACHULO — small progressive-enhancement helpers.
document.addEventListener("DOMContentLoaded", function () {
  // Auto-dismiss flash messages after a few seconds.
  document.querySelectorAll("main > div > div.rounded-lg").forEach(function (el) {
    setTimeout(function () {
      el.style.transition = "opacity 0.4s ease";
      el.style.opacity = "0";
    }, 4000);
  });

  initGalleryLightbox();
});

// ---------------------------------------------------------------------
// Full-screen property photo viewer.
// Any container marked [data-gallery] with [data-gallery-src] children
// (images or thumbnails) gets a click-to-expand, swipe/arrow-navigable
// lightbox with a blurred backdrop of the current photo.
// ---------------------------------------------------------------------
function initGalleryLightbox() {
  const galleries = document.querySelectorAll("[data-gallery]");
  if (!galleries.length) return;

  galleries.forEach(function (gallery) {
    const items = Array.from(gallery.querySelectorAll("[data-gallery-src]"));
    if (!items.length) return;

    const urls = items.map(function (el) { return el.getAttribute("data-gallery-src"); });
    let currentIndex = 0;

    // Build the overlay once per gallery.
    const overlay = document.createElement("div");
    overlay.className = "lightbox-overlay";
    overlay.innerHTML =
      '<div class="lightbox-track"></div>' +
      '<button type="button" class="lightbox-btn lightbox-close" aria-label="Close">' +
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="height:1.25rem;width:1.25rem"><path d="M18 6 6 18M6 6l12 12"/></svg>' +
      '</button>' +
      (urls.length > 1
        ? '<button type="button" class="lightbox-btn lightbox-prev" aria-label="Previous photo">' +
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="height:1.25rem;width:1.25rem"><path d="M15 18l-6-6 6-6"/></svg>' +
          '</button>' +
          '<button type="button" class="lightbox-btn lightbox-next" aria-label="Next photo">' +
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="height:1.25rem;width:1.25rem"><path d="M9 18l6-6-6-6"/></svg>' +
          '</button>' +
          '<div class="lightbox-counter"></div>'
        : "");
    document.body.appendChild(overlay);

    const track = overlay.querySelector(".lightbox-track");
    const counter = overlay.querySelector(".lightbox-counter");

    urls.forEach(function (url) {
      const slide = document.createElement("div");
      slide.className = "lightbox-slide";
      slide.innerHTML =
        '<div class="lightbox-slide-bg" style="background-image:url(\'' + url + '\')"></div>' +
        '<img class="lightbox-slide-img" src="' + url + '" alt="" />';
      track.appendChild(slide);
    });

    function render() {
      track.style.transform = "translateX(-" + currentIndex * 100 + "%)";
      if (counter) counter.textContent = (currentIndex + 1) + " / " + urls.length;
    }

    function open(index) {
      currentIndex = index;
      render();
      overlay.classList.add("is-open");
      document.body.classList.add("lightbox-open");
    }

    function close() {
      overlay.classList.remove("is-open");
      document.body.classList.remove("lightbox-open");
    }

    function next() { currentIndex = (currentIndex + 1) % urls.length; render(); }
    function prev() { currentIndex = (currentIndex - 1 + urls.length) % urls.length; render(); }

    items.forEach(function (el, i) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        open(i);
      });
    });

    overlay.querySelector(".lightbox-close").addEventListener("click", close);
    // Clicking the blurred backdrop (not the sharp photo) also closes it.
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay || e.target.classList.contains("lightbox-slide-bg") || e.target === track) close();
    });

    const nextBtn = overlay.querySelector(".lightbox-next");
    const prevBtn = overlay.querySelector(".lightbox-prev");
    if (nextBtn) nextBtn.addEventListener("click", next);
    if (prevBtn) prevBtn.addEventListener("click", prev);

    document.addEventListener("keydown", function (e) {
      if (!overlay.classList.contains("is-open")) return;
      if (e.key === "Escape") close();
      if (e.key === "ArrowRight") next();
      if (e.key === "ArrowLeft") prev();
    });

    // Basic swipe support for mobile.
    let touchStartX = null;
    overlay.addEventListener("touchstart", function (e) {
      touchStartX = e.changedTouches[0].clientX;
    }, { passive: true });
    overlay.addEventListener("touchend", function (e) {
      if (touchStartX === null) return;
      const delta = e.changedTouches[0].clientX - touchStartX;
      if (Math.abs(delta) > 40) { delta < 0 ? next() : prev(); }
      touchStartX = null;
    }, { passive: true });
  });
}
