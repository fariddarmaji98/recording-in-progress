/* Runs on meet.google.com. Detects who speaks (detector.js) and hands the events to the background worker,
 * which forwards them to the meetrec app on this computer. Nothing leaves the machine. */
(function () {
  'use strict';
  if (window.__meetrecLoaded) return;
  window.__meetrecLoaded = true;

  const SENS = { low: 6, normal: 4, high: 3 };
  let det = null;
  let queue = [];

  const meeting = () => (location.pathname.replace(/^\//, '').split('/')[0] || '');

  function send(msg) {
    try { chrome.runtime.sendMessage(msg); } catch (e) { /* extension reloaded: this page needs a refresh */ }
  }
  function flush() {
    if (!queue.length) return;
    const events = queue;
    queue = [];
    send({ kind: 'events', meeting: meeting(), events });
  }

  function enableCaptions() {
    const btn = Array.from(document.querySelectorAll('button')).find((b) =>
      /caption|subtitle|teks|takarir|sous-titre|untertitel/i.test(b.getAttribute('aria-label') || ''));
    if (btn && btn.getAttribute('aria-pressed') !== 'true') btn.click();
    return !!btn;
  }

  async function boot() {
    const cfg = await chrome.storage.local.get({ sensitivity: 'normal', autoCaptions: false });
    det = MeetrecDetector.create(document, (ev) => queue.push(ev), { onCount: SENS[cfg.sensitivity] || SENS.normal });
    det.start();
    setInterval(flush, 500);
    if (cfg.autoCaptions) setTimeout(enableCaptions, 5000);
    window.addEventListener('pagehide', () => { det.stop(); flush(); });
  }

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.kind === 'get_live') reply(det ? det.status() : null);
    else if (msg.kind === 'enable_captions') reply({ ok: enableCaptions() });
  });

  boot();
})();
