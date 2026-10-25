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
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    if (document.visibilityState === "visible" && location.pathname === n.url) return;
    const note = new Notification(n.title, { body: n.body, tag: n.tag });
    note.onclick = () => { window.focus(); location.href = n.url; };
  });

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