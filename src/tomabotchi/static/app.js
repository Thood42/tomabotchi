// tomabotchi live page wiring: SSE swaps partials by element id, and "notify" events become
// browser notifications (when the keeper has already allowed them; the opt-in arrives in slice 10).
(() => {
  const live = () => [...document.querySelectorAll("[data-live]")].map((el) => el.id);
  const params = new URLSearchParams();
  if (document.body.dataset.pet) params.set("pet", document.body.dataset.pet);
  const es = new EventSource("/events" + (params.size ? `?${params}` : ""));
  const listening = new Set();

  function swap(id, html) {
    const old = document.getElementById(id);
    if (!old) return;
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
