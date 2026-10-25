// tomabotchi kit: draws pixel pets on fake LCDs. Ported from the Gate 1 mockups; no build step.
const MAPS = {
  egg: [
    "......####......",
    "....##....##....",
    "...#........#...",
    "..#..#....#..#..",
    "..#..........#..",
    ".#....#..#....#.",
    ".#............#.",
    ".#..#......#..#.",
    ".#............#.",
    "..#..........#..",
    "..#..........#..",
    "...##......##...",
    ".....######.....",
  ],
  poop: ["..#..", ".###.", ".#.#.", "#####", "#####"],
  zz: ["###....", "..#....", ".#.####", "###..#.", "....#..", "...####"],
  bang: ["##", "##", "##", "##", "..", "##"],
  skull: [".###.", "#.#.#", "#####", ".###.", ".#.#."],
  dots: ["#.#.#"],
  dots2: ["#.#.."],
  heart: [".##.##.", "#######", "#######", ".#####.", "..###..", "...#..."],
  heartEmpty: [".##.##.", "#..#..#", "#.....#", ".#...#.", "..#.#..", "...#..."],
  icons: {
    task: [".........", "..#.#.#..", "..#.#.#..", ".........", "#########", "#.......#", ".#.....#.", "..#####..", "........."],
    lights: ["...###...", "..#...#..", ".#.....#.", ".#.....#.", "..#...#..", "...###...", "...###...", "...###...", "....#...."],
    chat: [".#######.", "#.......#", "#.##.##.#", "#.......#", ".#######.", "..#......", ".#.......", ".........", "........."],
    medicine: ["...###...", "...#.#...", "####.####", "#.......#", "####.####", "...#.#...", "...###...", ".........", "........."],
    tidy: ["......##.", ".....##..", "....##...", "...##....", "..####...", ".######..", "#.#.#.#..", "#.#.#.#..", "........."],
    vitals: [".##...##.", "####.####", "#########", "#########", ".#######.", "..#####..", "...###...", "....#....", "........."],
    train: ["....#....", "....#....", "...###...", "#########", ".#######.", "..#####..", "..##.##..", ".##...##.", "........."],
    calls: ["....#....", "...###...", "..#####..", "..#####..", "..#####..", ".#######.", "#########", "....#....", "...###..."],
    dice: ["#########", "#.......#", "#.##..#.#", "#.##....#", "#...#...#", "#....##.#", "#.#..##.#", "#.......#", "#########"],
  },
};

// ---- creature generator: a seed picks body, topper, eyes, feet, arms, mouth ----
function rng(seed) {
  let s = (seed * 2654435761) >>> 0 || 1;
  return () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
}

function genome(seed) {
  const r = rng(seed), pick = (a) => a[Math.floor(r() * a.length)];
  r(); r();
  const shape = pick(["round", "pear", "tall", "blocky", "ghost", "bean"]);
  let bottom = shape === "ghost" ? "wiggle" : pick(["feet", "feet3", "wheels", "stubs", "wiggle"]);
  if (bottom === "wheels" && !["blocky", "tall"].includes(shape)) bottom = "feet";
  return {
    shape,
    w: 4 + Math.floor(r() * 3),
    top: pick(["antenna", "twin", "ears", "horns", "sprout", "tuft", "none"]),
    bottom,
    arms: r() < 0.5,
    eyes: pick(["dot", "big", "tall", "cyclops", "visor"]),
    mouth: pick(["narrow", "wide", "fang"]),
  };
}

const TRAIT_WORDS = {
  shape: { round: "Round", pear: "Pear-shaped", tall: "Tall", blocky: "Boxy", ghost: "Ghostly", bean: "Bean-shaped" },
  top: { antenna: "antenna", twin: "twin antennae", ears: "cat ears", horns: "horns", sprout: "a sprout", tuft: "a tuft", none: "a smooth head" },
  eyes: { dot: "dot eyes", big: "big eyes", tall: "tall eyes", cyclops: "one eye", visor: "a visor" },
  bottom: { feet: "two feet", feet3: "three legs", wheels: "wheels", stubs: "stubby legs", wiggle: "a wiggly hem" },
};
function describe(seed) {
  const g = genome(seed), t = TRAIT_WORDS;
  return `${t.shape[g.shape]}, with ${t.top[g.top]}, ${t.eyes[g.eyes]} and ${t.bottom[g.bottom]}${g.arms ? ". Waves a lot" : ""}.`;
}

function halfWidths(g) {
  const y0 = g.shape === "tall" ? 3 : 4, y1 = 12, n = y1 - y0 + 1;
  const W = g.shape === "tall" ? Math.min(g.w, 5) : g.w;
  const hw = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1), ell = Math.sqrt(Math.max(0, 1 - (2 * t - 1) ** 2));
    let h = W;
    if (g.shape === "round") h = W * Math.max(ell, 0.35);
    if (g.shape === "pear") h = W * (0.55 + 0.45 * t) * Math.max(Math.sqrt(ell), 0.5);
    if (g.shape === "bean") h = W * (1 - 0.3 * t) * Math.max(Math.sqrt(ell), 0.55);
    if (g.shape === "ghost") h = t < 0.5 ? W * Math.max(ell, 0.35) : W;
    if (g.shape === "tall") h = i === 0 ? W - 2 : i === 1 || i === n - 1 ? W - 1 : W;
    if (g.shape === "blocky") h = i === 0 || i === n - 1 ? W - 1 : W;
    hw.push(Math.max(1, Math.round(h)));
  }
  return { y0, y1, hw };
}

function creature(seed, mood) {
  const g = genome(seed), { y0, y1, hw } = halfWidths(g), S = 16;
  const key = (r, c) => r * S + c, mask = new Set(), lit = new Set();
  const on = (r, c) => { if (r >= 0 && r < S && c >= 0 && c < S) lit.add(key(r, c)); };
  const both = (r, c) => { on(r, c); on(r, 15 - c); };
  const hwAt = (r) => hw[Math.min(Math.max(r - y0, 0), hw.length - 1)];

  // body silhouette, mirrored around the centre, drawn as an outline
  for (let r = y0; r <= y1; r++) for (let c = 8 - hwAt(r); c <= 7 + hwAt(r); c++) mask.add(key(r, c));
  mask.forEach((k) => {
    const r = (k / S) | 0, c = k % S;
    if (!mask.has(key(r - 1, c)) || !mask.has(key(r + 1, c)) || !mask.has(key(r, c - 1)) || !mask.has(key(r, c + 1))) lit.add(k);
  });

  const L2 = 8 - hwAt(y0 + 1);
  if (g.top === "antenna") { on(y0 - 1, 8); on(y0 - 2, 8); [3, 4].forEach((d) => { on(y0 - d, 7); on(y0 - d, 8); }); }
  if (g.top === "twin") { both(y0 - 1, L2 + 1); both(y0 - 2, L2 + 1); both(y0 - 3, L2); }
  if (g.top === "ears") { both(y0, L2); both(y0 - 1, L2); both(y0 - 2, L2); both(y0 - 1, L2 + 1); both(y0, L2 + 2); }
  if (g.top === "horns") { both(y0 - 1, L2 + 1); both(y0 - 2, L2); both(y0 - 3, L2); }
  if (g.top === "sprout") { on(y0 - 1, 8); on(y0 - 2, 8); on(y0 - 3, 9); on(y0 - 3, 10); on(y0 - 4, 10); on(y0 - 4, 11); on(y0 - 3, 7); on(y0 - 4, 6); }
  if (g.top === "tuft") { on(y0 - 1, 7); on(y0 - 2, 6); on(y0 - 1, 8); on(y0 - 2, 8); on(y0 - 3, 8); on(y0 - 1, 9); on(y0 - 2, 10); }

  const B = 8 - hwAt(y1);
  if (g.bottom === "feet") { both(y1 + 1, 5); both(y1 + 1, 6); }
  if (g.bottom === "feet3") { both(y1 + 1, 5); on(y1 + 1, 7); on(y1 + 1, 8); }
  if (g.bottom === "stubs") { both(y1 + 1, 6); both(y1 + 2, 6); }
  if (g.bottom === "wheels") [[1, 0], [1, 1], [1, 2], [2, 0], [2, 2], [3, 0], [3, 1], [3, 2]].forEach(([d, c]) => both(y1 + d, B + c));
  if (g.bottom === "wiggle") for (let c = B; c <= 7; c++) if ((c - B) % 2) { lit.delete(key(y1, c)); lit.delete(key(y1, 15 - c)); both(y1 + 1, c); }

  if (g.arms) { const ym = Math.round((y0 + y1) / 2) + 1, A = 8 - hwAt(ym); both(ym, A - 1); both(ym - 1, A - 2); }

  // face
  // keep a one-pixel gap between features and the outline; slim bodies get slimmer features
  const ey = y0 + Math.round((y1 - y0) * 0.3), a = hwAt(ey) >= 6 ? 4 : 5;
  let eyes = g.eyes;
  if (eyes === "visor" && hwAt(ey) < 6) eyes = "big";
  if (eyes === "big" && hwAt(ey) < 5) eyes = "tall";
  const my = Math.min(ey + 3, y1 - 2);
  const mouth = hwAt(my) < 5 && g.mouth === "wide" ? "narrow" : g.mouth;
  if (eyes === "visor") { for (let c = a; c <= 15 - a; c++) { on(ey - 1, c); on(ey + 1, c); } both(ey, a); }
  const open = () => {
    if (eyes === "dot") both(ey, a + 1);
    if (eyes === "big") [0, 1].forEach((d) => { both(ey + d, a); both(ey + d, a + 1); });
    if (eyes === "tall") { both(ey, a + 1); both(ey + 1, a + 1); }
    if (eyes === "cyclops") [0, 1].forEach((d) => { on(ey + d, 7); on(ey + d, 8); });
    if (eyes === "visor") both(ey, a + 2);
  };
  const shut = () => {
    if (eyes === "cyclops") [6, 7, 8, 9].forEach((c) => on(ey + 1, c));
    else if (eyes !== "visor") { both(ey + 1, a); both(ey + 1, a + 1); }
  };
  const dizzy = () => {
    if (eyes === "cyclops") { on(ey, 6); on(ey + 1, 7); on(ey + 1, 8); on(ey, 9); }
    else if (eyes === "visor") { both(ey, a + 1); both(ey, a + 3); }
    else { both(ey, a); both(ey + 1, a + 1); }
  };
  const smile = () => {
    if (mouth === "wide") { both(my, 5); both(my + 1, 6); both(my + 1, 7); }
    else { both(my, 6); both(my + 1, 7); }
    if (mouth === "fang" && my + 2 < y1) on(my + 2, 9);
  };
  if (mood === "happy") { open(); smile(); }
  if (mood === "work") { open(); on(my, 7); on(my, 8); }
  if (mood === "alert") { open(); [0, 1].forEach((d) => { on(my + d, 7); on(my + d, 8); }); }
  if (mood === "sleep") { shut(); on(my, 7); on(my, 8); }
  if (mood === "sick") { dizzy(); on(my + 1, 6); on(my, 7); on(my + 1, 8); on(my, 9); }

  return Array.from({ length: S }, (_, r) => Array.from({ length: S }, (_, c) => (lit.has(key(r, c)) ? "#" : ".")).join(""));
}

// ---- LCD rendering ----
// Render layers onto a w×h grid. ghost=true draws unlit LCD cells faintly.
function grid(w, h, layers, ghost = true) {
  const lit = new Set();
  layers.forEach(({ map, x = 0, y = 0 }) =>
    map.forEach((row, r) => [...row].forEach((ch, c) => {
      const X = x + c, Y = y + r;
      if (ch === "#" && X >= 0 && X < w && Y >= 0 && Y < h) lit.add(Y * w + X);
    }))
  );
  let s = `<svg viewBox="0 0 ${w} ${h}" shape-rendering="crispEdges" aria-hidden="true">`;
  for (let i = 0; i < w * h; i++) {
    const on = lit.has(i);
    if (!on && !ghost) continue;
    s += `<rect x="${(i % w) + 0.06}" y="${((i / w) | 0) + 0.06}" width=".88" height=".88" class="${on ? "on" : "off"}"/>`;
  }
  return s + "</svg>";
}

// Each scene has two frames, swapped like the real toy's stepped animation.
function frames(scene, seed) {
  const c = (m) => creature(seed, m);
  if (scene === "happy") return [[{ map: c("happy"), x: 7, y: 3 }], [{ map: c("happy"), x: 9, y: 3 }]];
  if (scene === "work") return [
    [{ map: c("work"), x: 7, y: 3 }, { map: MAPS.dots, x: 25, y: 5 }],
    [{ map: c("work"), x: 8, y: 3 }, { map: MAPS.dots2, x: 25, y: 5 }],
  ];
  if (scene === "alert") return [
    [{ map: c("alert"), x: 8, y: 3 }, { map: MAPS.bang, x: 27, y: 2 }],
    [{ map: c("alert"), x: 8, y: 2 }],
  ];
  if (scene === "sleep") return [
    [{ map: c("sleep"), x: 8, y: 3 }, { map: MAPS.zz, x: 24, y: 3 }],
    [{ map: c("sleep"), x: 8, y: 3 }, { map: MAPS.zz, x: 24, y: 1 }],
  ];
  if (scene === "sick") {
    const mess = [{ map: MAPS.poop, x: 26, y: 14 }, { map: MAPS.poop, x: 1, y: 14 }];
    return [
      [{ map: c("sick"), x: 7, y: 3 }, { map: MAPS.skull, x: 25, y: 3 }, ...mess],
      [{ map: c("sick"), x: 7, y: 4 }, { map: MAPS.skull, x: 25, y: 2 }, ...mess],
    ];
  }
  if (scene === "egg") return [[{ map: MAPS.egg, x: 8, y: 4 }], [{ map: MAPS.egg, x: 9, y: 4 }]];
  if (scene === "eggmini") return [[{ map: MAPS.egg }], [{ map: MAPS.egg }]];
}

function renderScene(el) {
  const scene = el.dataset.scene, [a, b] = frames(scene, Number(el.dataset.seed || 1));
  el.dataset.drawn = `${scene}/${el.dataset.seed}`;
  const [w, h] = scene === "eggmini" ? [16, 13] : [32, 20];
  const ghost = scene !== "eggmini";
  el.innerHTML = `<div class="f1">${grid(w, h, a, ghost)}</div><div class="f2">${grid(w, h, b, ghost)}</div>`;
}

// ---- hydration: draw every LCD, icon and heart row under root (root included) ----
const within = (root, sel) => [...(root.matches?.(sel) ? [root] : []), ...root.querySelectorAll(sel)];

function hydrate(root = document) {
  within(root, "[data-scene]").forEach((el) => {
    if (el.dataset.drawn !== `${el.dataset.scene}/${el.dataset.seed}` || !el.firstChild) renderScene(el);
  });
  within(root, "[data-icon]").forEach((el) => {
    if (!el.querySelector(":scope > svg")) {
      el.insertAdjacentHTML("afterbegin", grid(9, 9, [{ map: MAPS.icons[el.dataset.icon] }], false));
    }
  });
  within(root, "[data-hearts]").forEach((el) => {
    const [n, of] = el.dataset.hearts.split("/").map(Number);
    el.innerHTML = Array.from({ length: of }, (_, i) =>
      grid(7, 6, [{ map: i < n ? MAPS.heart : MAPS.heartEmpty }], false)).join("");
    el.setAttribute("aria-label", `${n} of ${of} hearts`);
  });
}

hydrate();

if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
  setInterval(() => document.querySelectorAll("[data-scene]").forEach((el) => el.classList.toggle("alt")), 650);
}
