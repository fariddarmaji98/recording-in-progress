/* Service worker: batches speaking events from the Meet tab and POSTs them to the meetrec app on 127.0.0.1.
 * Sent from here (not from the page) so Meet's page origin / CORS rules do not apply. */
const DEFAULTS = { port: 47831, token: '' };
const MAX_QUEUE = 5000;

let queue = [];
let sent = 0;
let lastError = '';
let connected = false;
let recording = false;
let meeting = '';
let flushing = false;

const config = () => chrome.storage.local.get(DEFAULTS);

async function call(path, body) {
  const c = await config();
  return fetch(`http://127.0.0.1:${c.port}${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', 'X-Meetrec-Token': c.token },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function hello() {
  try {
    const r = await call('/v1/hello');
    if (r.ok) { const j = await r.json(); connected = true; recording = !!j.recording; lastError = ''; return j; }
    connected = r.status !== 401 && connected;
    lastError = r.status === 401 ? 'Token salah. Salin ulang dari Pengaturan di aplikasi meetrec.' : `Aplikasi menjawab ${r.status}`;
  } catch (e) {
    connected = false;
    lastError = 'Aplikasi meetrec tidak terjangkau. Sudah dijalankan?';
  }
  return null;
}

async function flush() {
  if (flushing || !queue.length) return;
  flushing = true;
  const batch = queue.splice(0, 500);
  try {
    const r = await call('/v1/events', { meeting, events: batch });
    if (r.ok) {
      const j = await r.json();
      sent += batch.length; connected = true; recording = !!j.recording; lastError = '';
    } else {
      queue.unshift(...batch);
      lastError = r.status === 401 ? 'Token salah. Salin ulang dari Pengaturan di aplikasi meetrec.' : `Aplikasi menjawab ${r.status}`;
    }
  } catch (e) {
    queue.unshift(...batch);
    connected = false;
    lastError = 'Aplikasi meetrec tidak terjangkau. Sudah dijalankan?';
  }
  if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE);
  flushing = false;
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.kind === 'events') {
    meeting = msg.meeting || meeting;
    queue.push(...msg.events);
    flush();
    reply({ ok: true });
  } else if (msg.kind === 'status' || msg.kind === 'test') {
    hello().then((info) => reply({ connected, recording, lastError, queued: queue.length, sent, meeting, info }));
    return true; // async reply
  }
});

setInterval(flush, 1500);
