// The desk: places each pet's blocks on the shared timeline, zooms it, and focuses a pet's deck.
// Server-rendered lanes carry epoch-millisecond times; this file turns them into positions.
(() => {
  const screen = document.getElementById("screen");
  if (!screen) return;
  const ZOOMS = [5, 15, 60];
  const memo = (k, v) => { try { return v === undefined ? localStorage.getItem(k) : localStorage.setItem(k, v); } catch { return null; } };
  const typing = () => document.activeElement?.matches?.("input, textarea, select, [contenteditable]");
  let zoom = ZOOMS.includes(Number(memo("tb-zoom"))) ? Number(memo("tb-zoom")) : 15;
  let current = document.querySelector("#deck")?.dataset.pet || null;

  const hm = (ms) => new Date(ms).toTimeString().slice(0, 5);
  const win = () => zoom * 60000;

  // A reel's children are placed in percent of its width, relative to the moment it was laid out.
  function layout(reel, now = Date.now()) {
    reel.dataset.origin = now;
    for (const b of reel.querySelectorAll("[data-t0]")) {
      const t0 = Number(b.dataset.t0), open = b.dataset.t1 === undefined;
      const t1 = open ? now : Number(b.dataset.t1);
      b.style.left = `${100 + (t0 - now) / win() * 100}%`;
      // an open block runs on past the right edge, so it keeps reaching "now" as time passes
      b.style.width = `${(t1 - t0) / win() * 100 + (open ? 300 : 0)}%`;
    }
  }

  function ticks(now) {
    const step = ({ 5: 1, 15: 3, 60: 10 })[zoom] * 60000;
    const ruler = document.getElementById("ruler"), grid = document.getElementById("gridlines");
    ruler.textContent = grid.textContent = "";
    for (let t = Math.ceil((now - win()) / step) * step; t < now + step; t += step) {
      const left = `${100 + (t - now) / win() * 100}%`;
      grid.insertAdjacentHTML("beforeend", `<i style="left:${left}"></i>`);
      if (t < now - win() * 0.04) ruler.insertAdjacentHTML("beforeend", `<span style="left:${left}">${hm(t)}</span>`);
    }
    ruler.dataset.origin = grid.dataset.origin = now;
  }

  function relayout() {
    const now = Date.now();
    laneWidth = 0;
    ticks(now);
    screen.querySelectorAll(".lane .reel").forEach((reel) => layout(reel, now));
  }

  function setZoom(minutes) {
    zoom = minutes;
    memo("tb-zoom", String(minutes));
    document.querySelectorAll(".zoom button").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.w) === zoom)));
    relayout();
  }

  function mark() {
    document.querySelectorAll(".strips .mod, .lane").forEach((el) => el.toggleAttribute("aria-current", el.dataset.pet === current));
  }

  async function focusPet(id) {
    if (!id || id === current) return;
    const r = await fetch(`/pets/${id}/deck`);
    if (!r.ok) return;
    const tpl = document.createElement("template");
    tpl.innerHTML = (await r.text()).trim();
    const el = tpl.content.firstElementChild;
    document.getElementById("deck")?.replaceWith(el);
    current = id;
    hydrate(el);
    window.htmx?.process(el);
    window.tbListen?.();                    // the new deck brings its own live ids
    history.replaceState(null, "", `?focus=${id}`);
    mark();
  }

  const strips = () => [...document.querySelectorAll(".strips .mod")].filter((m) => !m.hidden);

  document.addEventListener("click", (e) => {
    if (e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.target.closest("button, input, textarea")) return;
    const hit = e.target.closest(".strips .mod, .lane");
    if (!hit) return;
    e.preventDefault();                      // a plain click focuses; a modified click still opens the pet
    focusPet(hit.dataset.pet);
  });

  document.querySelectorAll(".zoom button").forEach((b) => b.addEventListener("click", () => setZoom(Number(b.dataset.w))));

  document.addEventListener("keydown", (e) => {
    if (typing() || e.ctrlKey || e.metaKey || e.altKey) return;
    const n = Number(e.key);
    if (n >= 1 && n <= 9 && strips()[n - 1]) focusPet(strips()[n - 1].dataset.pet);
    const i = ZOOMS.indexOf(zoom);
    if (e.key === "[" && i > 0) setZoom(ZOOMS[i - 1]);
    if (e.key === "]" && i < ZOOMS.length - 1) setZoom(ZOOMS[i + 1]);
  });

  const tip = document.getElementById("tip");
  screen.addEventListener("mousemove", (e) => {
    const b = e.target.closest("[data-tip]");
    if (!b) { tip.style.display = "none"; return; }
    tip.textContent = b.dataset.tip;
    tip.style.display = "block";
    tip.style.left = `${e.clientX + 14}px`;
    tip.style.top = `${e.clientY + 14}px`;
  });
  screen.addEventListener("mouseleave", () => { tip.style.display = "none"; });

  const prior = window.onSwap;
  window.onSwap = (el) => {
    prior?.(el);
    if (el.classList?.contains("lane")) layout(el.querySelector(".reel"));
    mark();
  };

  // The "now" edge glides: each reel slides left by the time since it was laid out. One transform per reel
  // per frame, and nothing is laid out again until the next relayout.
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  let laneWidth = 0;
  function glide() {
    if (!still.matches) {
      const now = Date.now();
      if (!laneWidth) laneWidth = screen.querySelector(".ruler").clientWidth;
      for (const reel of screen.querySelectorAll("[data-reel]")) {
        reel.style.transform = `translate3d(${-(now - Number(reel.dataset.origin)) / win() * laneWidth}px,0,0)`;
      }
    }
    requestAnimationFrame(glide);               // the browser pauses this while the tab is hidden
  }
  addEventListener("resize", () => { laneWidth = 0; });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) relayout(); });

  setZoom(zoom);
  setInterval(relayout, 5000);
  requestAnimationFrame(glide);
})();
