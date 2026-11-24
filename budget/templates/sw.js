const VERSION = "{{ version }}";
const CACHE = "fable-shell-" + VERSION;
const OFFLINE_URL = "{{ offline_url }}";
const SHELL = {{ shell | safe }};
const SHELL_SET = new Set(SHELL);

const PAGE_DEADLINE_MS = 8000;

function fetchWithDeadline(request) {
  let timer;
  const deadline = new Promise((_resolve, reject) => {
    timer = setTimeout(
      () => reject(new Error("page took longer than the deadline")),
      PAGE_DEADLINE_MS);
  });
  return Promise.race([fetch(request), deadline])
    .finally(() => clearTimeout(timer));
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.addAll(SHELL.concat([OFFLINE_URL]));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name !== CACHE) {
        await caches.delete(name);
      }
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;

  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);

  if (url.origin !== self.location.origin) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith((async () => {
      try {
        return await fetchWithDeadline(request);
      } catch (err) {
        const cache = await caches.open(CACHE);
        const offline = await cache.match(OFFLINE_URL);
        return offline || Response.error();
      }
    })());
    return;
  }

  if (SHELL_SET.has(url.pathname)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      const hit = await cache.match(url.pathname, { ignoreVary: true });
      if (hit) {
        return hit;
      }
      return fetch(request);
    })());
    return;
  }

});
