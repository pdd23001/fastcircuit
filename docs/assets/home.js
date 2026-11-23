// Home page: start the benchmark bar race when it scrolls into view.
// Material's instant navigation swaps pages without reloading, so this runs
// on every page change through document$.
document$.subscribe(function () {
  var races = document.querySelectorAll(".fc-race");
  if (!races.length) return;
  if (!("IntersectionObserver" in window)) {
    races.forEach(function (race) { race.classList.add("is-running"); });
    return;
  }
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-running");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.5 });
  races.forEach(function (race) { observer.observe(race); });
});
