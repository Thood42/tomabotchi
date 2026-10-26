// tomabotchi live page wiring: SSE swaps partials by element id, and "notify" events become
// browser notifications (when the keeper has already allowed them; the opt-in arrives in slice 10).
(() => {
  const live = () => [...document.querySelectorAll("[data-live]")].map((el) => el.id);
  const es = new EventSource("/events");           // every pet: the rack on a pet's page shows them all live
  // Updates sent while the stream was down aren't replayed, so coming back means showing the page afresh.
  let dropped = false;
  es.addEventListener("error", () => { dropped = true; });
  es.addEventListener("open", () => { if (dropped) location.reload(); });
  const listening = new Set();

  const keepLcd = window.keepLcd = {
    // kit.js draws each LCD from its data attributes; a morph must not wipe the drawing or the animation frame.
    beforeNodeMorphed(oldNode, newNode) {
      if (!oldNode.classList?.contains("lcd")) return true;
      if (oldNode.dataset.scene !== newNode.dataset.scene || oldNode.dataset.seed !== newNode.dataset.seed) {
        oldNode.dataset.scene = newNode.dataset.scene;
        oldNode.dataset.seed = newNode.dataset.seed;
        renderScene(oldNode);
      }
      return false;
    },
  };

  // Updates are diffed into the live DOM, so scroll, focus, a half-typed reason and running animations survive.
  function swap(id, html) {
    const old = document.getElementById(id);
    if (!old) return;
    const pin = old.closest("[data-pin]");
    const pinned = pin && pinned_(pin);
    const tpl = document.createElement("template");
    tpl.innerHTML = html.trim();
    const el = tpl.content.firstElementChild;
    if (!el) return;
    let now = el;
    if (window.Idiomorph) {
      Idiomorph.morph(old, el, { morphStyle: "outerHTML", ignoreActiveValue: true, callbacks: keepLcd });
      now = document.getElementById(id) || el;
    } else {
      old.replaceWith(el);
    }
    hydrate(now);
    if (window.htmx) htmx.process(now);
    listen();                       // a swapped partial may bring new live ids
    if (pinned) pin.scrollTop = pin.scrollHeight;
    if (now.matches?.(".mod")) markRack();
    window.onSwap?.(now);
  }

  // A stream stays pinned to the newest line unless the keeper has scrolled up to read.
  function pinned_(el) { return el.scrollHeight - el.scrollTop - el.clientHeight < 48; }

  function markRack() {
    const here = document.querySelector("[data-page-pet]")?.dataset.pagePet;
    document.querySelectorAll(".rail .mod").forEach((m) => m.toggleAttribute("aria-current", m.dataset.pet === here));
  }

  // A new page is in place (first load, a boosted navigation, or Back): draw its LCDs, subscribe to its live ids,
  // mark the pet in view and let the page's own script set up. This runs in the same task as the swap, so
  // nothing half-ready is ever painted.
  const ready = () => {
    hydrate(document.body);
    listen();
    markRack();
    document.querySelectorAll("[data-pin]").forEach((el) => { el.scrollTop = el.scrollHeight; });
    window.onPage?.forEach((fn) => fn());
  };
  window.onPage = window.onPage || [];
  document.addEventListener("htmx:afterSwap", (e) => { if (e.detail.target === document.body) ready(); });
  document.addEventListener("htmx:historyRestore", ready);
  window.addEventListener("DOMContentLoaded", ready);

  // On a pet's page, the number keys choose a pet in the rack (the desk handles them itself).
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || !/^[1-9]$/.test(e.key)) return;
    if (document.activeElement?.matches?.("input, textarea, select, [contenteditable]")) return;
    document.querySelectorAll(".rail .mod .hit")[Number(e.key) - 1]?.click();
  });

  // The composer sends on Enter, and Shift Enter adds a line.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing || !e.target.matches?.(".composer textarea")) return;
    e.preventDefault();
    e.target.form.requestSubmit(e.target.form.querySelector('[value="task"]'));
  });

  function listen() {
    for (const id of live()) {
      if (listening.has(id)) continue;
      listening.add(id);
      es.addEventListener(id, (e) => swap(id, e.data));
    }
  }

  es.addEventListener("notify", (e) => {
    const n = JSON.parse(e.data);
    const test = n.tag === "test";
    if (!test && document.visibilityState === "visible" && location.pathname === n.url) return;
    beep();
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const note = new Notification(n.title, { body: n.body, tag: n.tag });
    note.onclick = () => { window.focus(); location.href = n.url; };
  });

  window.tbListen = listen;
  listen();
})();

// Markdown fields: Write shows the textarea, Preview renders it on the server (sanitised).
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".md-tabs [data-md]");
  if (!btn) return;
  const field = btn.closest(".md-field");
  const area = field.querySelector("textarea");
  const view = field.querySelector(".md-preview");
  const preview = btn.dataset.md === "preview";
  field.querySelectorAll("[data-md]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
  if (preview) {
    const r = await fetch("/markdown/preview", { method: "POST", body: new URLSearchParams({ md: area.value }) });
    view.innerHTML = await r.text();
    view.style.minHeight = area.offsetHeight + "px";
  }
  area.hidden = preview;
  view.hidden = !preview;
  if (!preview) area.focus();
});


// A proposed lesson's "Edit wording" swaps the line for an input holding it.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-reword]");
  if (!btn) return;
  const form = btn.closest("form");
  const box = form.querySelector(".reword");
  box.hidden = false;
  form.querySelector(".add").hidden = true;
  btn.hidden = true;
  const input = box.querySelector("input");
  input.focus();
  input.select();
});

// Two short square-wave chirps, like the toy. Browsers only allow sound after the keeper has
// clicked something on the page, so a background tab may stay silent; the notification still shows.
let audio;
function beep() {
  try {
    audio = audio || new AudioContext();
    for (const [at, hz] of [[0, 1320], [0.16, 1760]]) {
      const osc = audio.createOscillator(), gain = audio.createGain();
      osc.type = "square";
      osc.frequency.value = hz;
      gain.gain.value = 0.05;
      osc.connect(gain).connect(audio.destination);
      osc.start(audio.currentTime + at);
      osc.stop(audio.currentTime + at + 0.1);
    }
  } catch { /* no audio here */ }
}

// Opt-in: browsers only show the permission prompt in answer to a click, so ask with a banner.
(() => {
  const banner = document.querySelector(".optin");
  const store = (k, v) => { try { return v === undefined ? localStorage.getItem(k) : localStorage.setItem(k, v); } catch { return null; } };
  const supported = "Notification" in window;
  if (banner && supported && Notification.permission === "default" && !store("tb-optin-later")) banner.hidden = false;
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-optin]");
    if (!b) return;
    if (b.dataset.optin === "ask" && supported) await Notification.requestPermission();
    else store("tb-optin-later", "1");
    banner.hidden = true;
    describePermission();
  });

  // Settings: say what this browser allows, and remember the keeper's timezone for quiet hours.
  function describePermission() {
    const el = document.querySelector("[data-permission]");
    if (!el) return;
    el.textContent = !supported ? "This browser can't show notifications."
      : Notification.permission === "granted" ? "Allowed in this browser. Turn off to silence everything."
      : Notification.permission === "denied" ? "Blocked by this browser. Allow notifications for this site in its settings."
      : "This browser hasn't been asked yet.";
    if (supported && Notification.permission === "default" && !el.querySelector("button")) {
      const ask = document.createElement("button");
      ask.type = "button"; ask.className = "btn"; ask.dataset.optin = "ask"; ask.textContent = "Ask my browser";
      el.append(" ", ask);
    }
  }
  function rememberZone() {
    const tz = document.querySelector("[data-tz]");
    if (tz && !tz.value) {
      tz.value = Intl.DateTimeFormat().resolvedOptions().timeZone;
      tz.form.dispatchEvent(new Event("change", { bubbles: true }));    // save it once
    }
  }
  describePermission();
  window.addEventListener("load", rememberZone);     // after htmx has wired the form
  document.body.addEventListener("htmx:afterSwap", () => { describePermission(); });
})();

// A line at the bottom of the page for what just happened, or why it didn't.
function toast(message) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    el.setAttribute("role", "status");
    document.body.append(el);
  }
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(el.hide);
  el.hide = setTimeout(() => el.classList.remove("show"), 3200);
}

// The server can say something after an action with an HX-Trigger header: {"toast": "..."}.
document.addEventListener("toast", (e) => toast(e.detail.value));

function errorText(xhr) {
  try { return JSON.parse(xhr.responseText).detail || xhr.statusText; } catch { return xhr.responseText?.slice(0, 160) || xhr.statusText; }
}

// Calls: A answers once, Shift A always allows, D denies. The key presses the keycap in the call in view.
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || !["a", "A", "d"].includes(e.key)) return;
  if (document.activeElement?.matches?.("input, textarea, select, [contenteditable]")) return;
  const cap = document.querySelector(`.callform [data-key="${e.key}"]:not(:disabled)`);
  if (!cap) return;
  e.preventDefault();
  cap.classList.add("hit");
  setTimeout(() => cap.classList.remove("hit"), 140);
  cap.click();
});

// Optimistic answers: the card says "Sending..." the moment you press, and comes back with the error if it fails.
function sending(form) {
  form.classList.add("busy");
  form.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  form.querySelector(".sending").hidden = false;
  form.querySelector(".senderr").hidden = true;
}

function unsend(form, message) {
  form.classList.remove("busy");
  form.querySelectorAll("button").forEach((b) => { b.disabled = false; });
  form.querySelector(".sending").hidden = true;
  const err = form.querySelector(".senderr");
  err.textContent = message;
  err.hidden = false;
}

document.addEventListener("htmx:beforeRequest", (e) => { if (e.target.matches?.(".callform")) sending(e.target); });
document.addEventListener("htmx:responseError", (e) => {
  const message = errorText(e.detail.xhr);
  e.target.querySelectorAll?.("input[data-was]").forEach((input) => {      // a rule that didn't save goes back
    input.value = input.dataset.was;
    input.closest(".rule")?.querySelectorAll(".sw3 [data-set]").forEach((o) => o.setAttribute("aria-pressed", String(o.dataset.set === input.value)));
    delete input.dataset.was;
  });
  if (e.target.matches?.(".callform")) unsend(e.target, message); else toast(message);
});
document.addEventListener("htmx:sendError", (e) => {
  if (e.target.matches?.(".callform")) unsend(e.target, "Couldn't reach tomabotchi."); else toast("Couldn't reach tomabotchi.");
});

document.addEventListener("htmx:afterRequest", (e) => {
  if (e.detail.successful && e.target.matches?.(".rules form")) toast("Rules saved. They apply from its next tool call.");
});

// Three-way switches: a press sets the row's hidden value and fires a change, which saves the rules.
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-set]");
  if (!b) return;
  const row = b.closest(".rule");
  const input = row.querySelector('input[name="rule"]');
  if (input.value === b.dataset.set) return;
  input.dataset.was = input.value;                  // so a failed save can put the switch back
  input.value = b.dataset.set;
  row.querySelectorAll(".sw3 [data-set]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
  input.dispatchEvent(new Event("change", { bubbles: true }));
});

// How long a call has waited, kept current without republishing the card.
setInterval(() => {
  const now = Date.now();
  document.querySelectorAll("[data-since]").forEach((el) => {
    const s = Math.max(0, Math.round((now - Number(el.dataset.since)) / 1000));
    el.textContent = `waiting ${s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`}`;
  });
}, 1000);

// ---- jump palette (Ctrl K), next call (N) and theme ----
(() => {
  const dlg = () => document.getElementById("palette");
  let shown = [], sel = 0;
  const entries = () => { try { return JSON.parse(document.getElementById("jump-data").textContent); } catch { return []; } };
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  function render() {
    const q = document.getElementById("palette-q").value.trim().toLowerCase();
    shown = entries().filter((e) => !q || `${e.label} ${e.hint}`.toLowerCase().includes(q));
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    let last = "", html = "";
    shown.forEach((e, i) => {
      if (e.group !== last) { html += `<li class="sec" role="presentation">${e.group}</li>`; last = e.group; }
      html += `<li role="option" data-i="${i}" aria-selected="${i === sel}">${esc(e.label)}<small>${esc(e.hint || "")}</small></li>`;
    });
    document.getElementById("palette-list").innerHTML = html || '<li class="sec" role="presentation">Nothing matches</li>';
  }

  function open() {
    const d = dlg();
    if (!d || d.open) return;
    document.getElementById("palette-q").value = "";
    sel = 0;
    render();
    d.showModal();
    document.getElementById("palette-q").focus();
  }

  // Navigate with the same boosted fetch a link would use, so the stream and the rack stay put.
  function go(href, boost = true, focus) {
    if (focus) sessionStorage.setItem("tb-focus", focus);
    if (!boost || !window.htmx) { location.href = href; return; }
    const a = document.createElement("a");
    a.href = href;
    a.hidden = true;
    document.body.append(a);
    htmx.process(a);
    a.click();
    a.remove();
  }

  async function run(e) {
    dlg().close();
    if (e.post) {
      const r = await fetch(e.post, { method: "POST", body: new URLSearchParams(e.body || {}) });
      toast(r.ok ? `${e.label}: done` : errorText({ responseText: await r.text(), statusText: r.statusText }));
    } else if (e.focus && location.pathname === e.href) {
      document.querySelector(e.focus)?.focus();
    } else if (e.href) {
      go(e.href, e.boost !== false, e.focus);
    }
  }

  // arriving by a palette entry that asked for a field: focus it once the page is in
  window.onPage = window.onPage || [];
  window.onPage.push(() => {
    let f = null;
    try { f = sessionStorage.getItem("tb-focus"); sessionStorage.removeItem("tb-focus"); } catch { /* private window */ }
    if (f) document.querySelector(f)?.focus();
  });

  document.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-open-palette]")) return open();
    const li = ev.target.closest("#palette-list [data-i]");
    if (li) return run(shown[Number(li.dataset.i)]);
    if (ev.target === dlg()) dlg().close();                     // a click on the backdrop
    const t = ev.target.closest("[data-theme-set]");
    if (t) setTheme(t.dataset.themeSet);
  });
  document.addEventListener("input", (ev) => { if (ev.target.id === "palette-q") { sel = 0; render(); } });
  document.addEventListener("keydown", (ev) => {
    const d = dlg();
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") { ev.preventDefault(); return d.open ? d.close() : open(); }
    if (d?.open) {
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        sel = Math.max(0, Math.min(shown.length - 1, sel + (ev.key === "ArrowDown" ? 1 : -1)));
        render();
        document.querySelector('#palette-list [aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
      } else if (ev.key === "Enter" && shown[sel]) { ev.preventDefault(); run(shown[sel]); }
      return;
    }
    if (ev.ctrlKey || ev.metaKey || ev.altKey || ev.key.toLowerCase() !== "n") return;
    if (document.activeElement?.matches?.("input, textarea, select, [contenteditable]")) return;
    const next = document.getElementById("status");
    if (next?.classList.contains("on")) { ev.preventDefault(); next.click(); }
  });

  // Theme: light, dark, or follow the system; remembered in this browser.
  const theme = () => { try { return localStorage.getItem("tb-theme") || "system"; } catch { return "system"; } };
  function setTheme(mode) {
    try { mode === "system" ? localStorage.removeItem("tb-theme") : localStorage.setItem("tb-theme", mode); } catch { /* private window */ }
    mode === "system" ? delete document.documentElement.dataset.theme : (document.documentElement.dataset.theme = mode);
    label();
  }
  const label = () => document.querySelectorAll("[data-theme-set]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeSet === theme())));
  window.setTheme = setTheme;
  window.onPage.push(label);
})();


// ---- navigation: boosted links and forms morph the page in place ----
// Idiomorph diffs the new page into the old one, so what both pages share (the top bar, the rack, the LCDs)
// is never torn down, and nothing flashes. A thin bar shows when a page takes a moment to arrive.
htmx.defineExtension("tb-morph", {
  isInlineSwap: (style) => style === "tb-morph",
  handleSwap(style, target, fragment) {
    if (style !== "tb-morph") return false;
    Idiomorph.morph(target, fragment, { morphStyle: "innerHTML", ignoreActiveValue: true, callbacks: window.keepLcd });
    return [...target.children];
  },
});
document.addEventListener("htmx:beforeRequest", (e) => { if (e.detail.boosted) document.documentElement.classList.add("loading"); });
for (const name of ["htmx:afterRequest", "htmx:sendError", "htmx:historyRestore"]) {
  document.addEventListener(name, () => document.documentElement.classList.remove("loading"));
}

// ---- the hatch form: six looks to pick from, a live preview, and the button says who you're hatching ----
(() => {
  const $ = (id) => document.getElementById(id);
  function choose(seed) {
    $("look_seed").value = seed;
    $("preview-lcd").dataset.seed = seed;
    renderScene($("preview-lcd"));
    $("look-desc").textContent = describe(Number(seed));
    $("looks").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.seed === String(seed))));
  }
  function roll(first) {
    const seeds = [first || Math.floor(Math.random() * 99999) + 1];
    while (seeds.length < 6) seeds.push(Math.floor(Math.random() * 99999) + 1);
    $("looks").innerHTML = seeds.map((s) =>
      `<button type="button" data-seed="${s}" aria-label="Look ${s}"><div class="lcd" data-scene="happy" data-seed="${s}"></div></button>`).join("");
    hydrate($("looks"));
    choose(seeds[0]);
  }
  document.addEventListener("click", (e) => {
    const look = e.target.closest("#looks button");
    if (look) choose(look.dataset.seed);
    if (e.target.closest("#shuffle")) roll();
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "name") $("hatch-btn").textContent = `Hatch ${e.target.value.trim() || "it"}`;
  });
  window.onPage.push(() => {
    if (!$("looks") || $("looks").children.length) return;
    roll(Number($("look_seed").value));
    $("name").focus();
    $("name").select();             // the suggested name is a starting point: typing replaces it
  });
})();
