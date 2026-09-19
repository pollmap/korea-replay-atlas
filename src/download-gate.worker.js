/* Classic service worker: Vite substitutes the content version and publishes /download-gate.js. */
const DOWNLOAD_GATE_VERSION = '__DOWNLOAD_GATE_VERSION__';
const DOWNLOAD_GATE_PROTOCOL = 1;
const DOWNLOAD_LIMIT = 4;
const MAX_BODY_BYTES = 24 * 1024 * 1024;
const WAIT_TIMEOUT_MS = 120000;
const ACTIVE_TIMEOUT_MS = 60000;
const RETRY_BACKOFF_MS = [100, 300];

const abortError = () => new DOMException('Map download cancelled', 'AbortError');
const backoff = (milliseconds, signal) => new Promise((resolve, reject) => {
  signal.throwIfAborted();
  const finish = () => {signal.removeEventListener('abort', cancel); resolve();};
  const timer = setTimeout(finish, milliseconds);
  const cancel = () => {clearTimeout(timer); signal.removeEventListener('abort', cancel); reject(signal.reason);};
  signal.addEventListener('abort', cancel, {once: true});
});
const priority = (url) => {
  const pathname = new URL(url).pathname;
  if (/\/(catalog(?:-v2)?\.json|releases\/pub-[a-f0-9]+\.json)$/.test(pathname)) return 0;
  if (pathname.endsWith('.json')) return 1;
  return 2;
};

class MapDownloadGate {
  constructor() {
    this.waiting = [];
    this.active = 0;
    this.peak = 0;
    this.completed = 0;
    this.failed = 0;
    this.cancelled = 0;
    this.retries = 0;
    this.lastFailure = null;
    this.bytes = 0;
    this.instance = self.crypto.randomUUID();
  }

  snapshot() {
    return {type: 'korea-download-gate-status', protocol: DOWNLOAD_GATE_PROTOCOL,
      version: DOWNLOAD_GATE_VERSION, instance: this.instance, limit: DOWNLOAD_LIMIT,
      maxBodyBytes: MAX_BODY_BYTES, active: this.active, queued: this.waiting.length,
      peak: this.peak, completed: this.completed, failed: this.failed,
      cancelled: this.cancelled, bytes: this.bytes, retries: this.retries,
      lastFailure: this.lastFailure ? {...this.lastFailure} : null};
  }

  recordFailure(job, phase, error) {
    const name = typeof error?.name === 'string' && /^[A-Za-z][A-Za-z0-9]{0,63}$/.test(error.name) ? error.name : 'Error';
    this.lastFailure = {pathname: new URL(job.request.url).pathname, phase, name};
  }

  enqueue(request) {
    if (request.signal.aborted) {
      this.cancelled++;
      return Promise.reject(abortError());
    }
    return new Promise((resolve, reject) => {
      const job = {request, resolve, reject, controller: new AbortController(),
        state: 'waiting', added: Date.now(), priority: priority(request.url), timer: null,
        onAbort: null};
      job.onAbort = () => {
        job.controller.abort(abortError());
        if (job.state !== 'waiting') return;
        this.waiting.splice(this.waiting.indexOf(job), 1);
        this.cancelled++;
        this.cleanup(job);
        reject(abortError());
      };
      request.signal.addEventListener('abort', job.onAbort, {once: true});
      job.timer = setTimeout(() => {
        if (job.state !== 'waiting') return;
        this.waiting.splice(this.waiting.indexOf(job), 1);
        this.failed++;
        this.cleanup(job);
        const error = new DOMException('Map download queue timed out', 'TimeoutError');
        this.recordFailure(job, 'queue', error);
        reject(error);
      }, WAIT_TIMEOUT_MS);
      this.waiting.push(job);
      this.drain();
    });
  }

  cleanup(job) {
    clearTimeout(job.timer);
    job.request.signal.removeEventListener('abort', job.onAbort);
    job.state = 'finished';
  }

  drain() {
    while (this.active < DOWNLOAD_LIMIT && this.waiting.length) {
      // Catalog/index/tileset JSON wins among waiting downloads. Aging prevents starvation.
      const now = Date.now();
      this.waiting.sort((a, b) => (now - a.added > 5000 ? -1 : a.priority) -
        (now - b.added > 5000 ? -1 : b.priority) || a.added - b.added);
      const job = this.waiting.shift();
      clearTimeout(job.timer);
      job.state = 'active';
      this.active++;
      this.peak = Math.max(this.peak, this.active);
      void this.run(job);
    }
  }

  async run(job) {
    job.timer = setTimeout(() => job.controller.abort(new DOMException('Map download timed out', 'TimeoutError')), ACTIVE_TIMEOUT_MS);
    let phase = 'fetch';
    try {
      // Preserve request cache, credentials, range and conditional headers. No CacheStorage.
      const marker = `${DOWNLOAD_GATE_VERSION};${this.instance}`;
      const requestHeaders = new Headers(job.request.headers);
      requestHeaders.set('X-Korea-Download-Gate', marker);
      for (let attempt = 0; ; attempt++) {
        let response;
        phase = 'fetch';
        try {
          job.controller.signal.throwIfAborted();
          // All intercepted URLs are same-origin. Preserve diagnostics for no-cors images.
          response = await self.fetch(job.request, {signal: job.controller.signal, headers: requestHeaders, mode: 'same-origin'});
          phase = 'response';
          const headers = new Headers(response.headers);
          headers.set('X-Korea-Download-Gate', marker);
          // fetch exposes decoded bytes; wire encoding/length must not describe the new response.
          headers.delete('Content-Encoding');
          headers.delete('Content-Length');
          headers.delete('Transfer-Encoding');
          const emptyBody = job.request.method === 'HEAD' || [204, 205, 304].includes(response.status);
          let buffer = null;
          if (!emptyBody) {
            phase = 'body';
            buffer = await this.readBody(response, job.controller.signal);
            phase = 'response';
            headers.set('Content-Length', String(buffer.byteLength));
          }
          job.controller.signal.throwIfAborted();
          const result = new Response(buffer, {status: response.status, statusText: response.statusText, headers});
          this.bytes += buffer?.byteLength ?? 0;
          this.completed++;
          job.resolve(result);
          return;
        } catch (error) {
          // TypeError is fetch/stream's network failure signal. HTTP errors,
          // redirects, byte limits, response-construction errors and cancellation
          // are not transient retries. readBody has awaited cancellation cleanup.
          job.controller.signal.throwIfAborted();
          this.recordFailure(job, phase, error);
          const retryable = ['GET', 'HEAD'].includes(job.request.method) && error instanceof TypeError &&
            (phase === 'fetch' || phase === 'body' && response?.ok && !response.redirected);
          if (!retryable || attempt >= RETRY_BACKOFF_MS.length) throw error;
          phase = 'backoff';
          await backoff(RETRY_BACKOFF_MS[attempt], job.controller.signal);
          job.controller.signal.throwIfAborted();
          this.retries++;
        }
      }
    } catch (error) {
      if (job.request.signal.aborted) this.cancelled++;
      else {this.failed++; this.recordFailure(job, phase, error);}
      job.reject(error);
    } finally {
      this.cleanup(job);
      // The slot covers the complete decoded body, including failure/cancellation cleanup.
      this.active--;
      this.drain();
    }
  }

  async readBody(response, signal) {
    if (!response.body) return new ArrayBuffer(0);
    const reader = response.body.getReader();
    const chunks = [];
    let length = 0;
    let cancellation;
    const cancel = reason => cancellation ??= reader.cancel(reason).catch(() => {});
    const onAbort = () => {void cancel(signal.reason);};
    signal.addEventListener('abort', onAbort, {once: true});
    try {
      while (true) {
        signal.throwIfAborted();
        const {done, value} = await reader.read();
        signal.throwIfAborted();
        if (done) break;
        length += value.byteLength;
        if (length > MAX_BODY_BYTES) throw new RangeError('Map asset exceeds 24 MiB decoded limit');
        chunks.push(value);
      }
      const bytes = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.byteLength;}
      return bytes.buffer;
    } catch (error) {
      await cancel(error);
      throw error;
    } finally {
      signal.removeEventListener('abort', onAbort);
      reader.releaseLock();
    }
  }
}

const gate = new MapDownloadGate();
// Do not skipWaiting: two versions must never run separate download pools for live clients.
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith('/data/')) return;
  event.respondWith(gate.enqueue(event.request));
});
self.addEventListener('message', event => {
  if (event.data?.type !== 'korea-download-gate-status') return;
  const reply = () => event.ports[0]?.postMessage(gate.snapshot());
  if (event.data.claim === true) event.waitUntil(self.clients.claim().then(reply));
  else reply();
});
