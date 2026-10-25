// tomabotchi live page wiring: SSE swaps partials by element id, and "notify" events become
// browser notifications (when the keeper has already allowed them; the opt-in arrives in slice 10).
(() => {
  const live = () => [...document.querySelectorAll("[data-live]")].map((el) => el.id);
  const params = new URLSearchParams();
  if (document.body.dataset.pet) params.set("pet", document.body.dataset.pet);
  const es = new EventSource("/events" + (params.size ? `?${params}` : ""));
  // Updates sent while the stream was down aren't replayed, so coming back means showing the page afresh.
  let dropped = false;
  es.addEventListener("error", () => { dropped = true; });
  es.addEventListener("open", () => { if (dropped) location.reload(); });
  const listening = new Set();

  const keepLcd = {
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
    window.onSwap?.(now);
  }

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
  if (e.target.matches?.(".callform")) unsend(e.target, message); else toast(message);
});
document.addEventListener("htmx:sendError", (e) => {
  if (e.target.matches?.(".callform")) unsend(e.target, "Couldn't reach tomabotchi."); else toast("Couldn't reach tomabotchi.");
});

// Three-way switches: a press sets the row's hidden value and fires a change, which saves the rules.
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-set]");
  if (!b) return;
  const row = b.closest(".rule");
  const input = row.querySelector('input[name="rule"]');
  if (input.value === b.dataset.set) return;
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
