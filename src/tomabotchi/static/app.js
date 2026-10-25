// tomabotchi live page wiring: SSE swaps partials by element id, and "notify" events become
// browser notifications (when the keeper has already allowed them; the opt-in arrives in slice 10).
(() => {
  const live = () => [...document.querySelectorAll("[data-live]")].map((el) => el.id);
  const params = new URLSearchParams();
  if (document.body.dataset.pet) params.set("pet", document.body.dataset.pet);
  const es = new EventSource("/events" + (params.size ? `?${params}` : ""));
  const listening = new Set();

  const held = new Map();          // id -> html that arrived while the keeper was typing inside it

  function swap(id, html) {
    const old = document.getElementById(id);
    if (!old) return;
    const typing = document.activeElement;
    if (typing && old.contains(typing) && typing.matches("input, textarea, select")) {
      if (!held.has(id)) old.addEventListener("focusout", () => setTimeout(() => {
        if (old.contains(document.activeElement)) return;
        const later = held.get(id);
        held.delete(id);
        if (later !== undefined) swap(id, later);
      }), { once: true });
      held.set(id, html);
      return;
    }
    const tpl = document.createElement("template");
    tpl.innerHTML = html.trim();
    const el = tpl.content.firstElementChild;
    if (!el) return;
    old.replaceWith(el);
    hydrate(el);
    if (window.htmx) htmx.process(el);
    listen();                       // a swapped partial may bring new live ids
    window.onSwap?.(el);
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