const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const D = require('../detector.js');

const flush = () => new Promise((r) => setTimeout(r, 0)); // let MutationObserver callbacks run

function page(html) {
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`, { pretendToBeVisual: true });
  return dom.window.document;
}
const tile = (id, name, extra = '') => `<div data-participant-id="${id}"><video></video><span class="notranslate">${name}</span><div class="ind" data-x="0">${extra}</div></div>`;
const selfTile = (name) => `<div data-participant-id="me"><div data-self-name="${name}"></div><div class="ind"></div></div>`;
const captionBlock = (name, text) => `<div class="blk"><div><img src="a.png" data-iml="1"></div><div><span>${name}</span></div><div class="txt"><div>${text}</div></div></div>`;

function harness(doc, opts = {}) {
  let now = 1_000_000;
  const events = [];
  const det = D.create(doc, (e) => events.push(e), Object.assign({ clock: () => now }, opts));
  return { det, events, advance: (ms) => { now += ms; }, now: () => now };
}
async function pulse(doc, sel, h, n, gapMs = 80) {
  const el = doc.querySelector(sel);
  for (let i = 0; i < n; i++) { el.className = 'ind v' + i; await flush(); h.advance(gapMs); }
}

test('cleanName strips presentation suffix, zero-width chars, extra spaces', () => {
  assert.equal(D.cleanName('  Budi   Santoso ​'), 'Budi Santoso');
  assert.equal(D.cleanName('Sari (Presentation)'), 'Sari');
  assert.equal(D.cleanName('Sari (Presentasi layar)'), 'Sari');
  assert.equal(D.isSelfWord('You'), true);
  assert.equal(D.isSelfWord('Anda'), true);
  assert.equal(D.isSelfWord('Budi'), false);
});

test('tileInfo: self tile via data-self-name, others via notranslate span, fallback aria-label', () => {
  const doc = page(selfTile('Farid') + tile('p1', 'Budi') + '<div data-participant-id="p2" aria-label="Sari"></div>');
  const [t0, t1, t2] = doc.querySelectorAll('[data-participant-id]');
  assert.deepEqual(D.tileInfo(t0), { name: 'Farid', self: true });
  assert.deepEqual(D.tileInfo(t1), { name: 'Budi', self: false });
  assert.deepEqual(D.tileInfo(t2), { name: 'Sari', self: false });
});

test('SpeakingTracker: one start, no duplicates, stop stamped at LAST activity, totals kept', () => {
  const ev = [];
  const tr = new D.SpeakingTracker((e) => ev.push(e), { tileGapMs: 900 });
  tr.ping('tile', 'p1', 'Budi', false, 1000);
  tr.ping('tile', 'p1', 'Budi', false, 1300);
  tr.ping('tile', 'p1', 'Budi', false, 1700);
  tr.tick(2000);                 // only 300 ms quiet: still speaking
  assert.equal(ev.length, 1);
  tr.tick(2700);                 // 1000 ms quiet: stopped
  assert.deepEqual(ev.map((e) => [e.state, e.t]), [['start', 1000], ['stop', 1700]]);
  assert.equal(tr.seen.get('Budi'), 700);
  assert.deepEqual(tr.speakingNow(), []);
});

test('tile: sustained attribute mutations on a tile = that person speaks; stops after quiet', async () => {
  const doc = page(tile('p1', 'Budi') + tile('p2', 'Sari'));
  const h = harness(doc);
  h.det.tiles.start();
  await pulse(doc, '[data-participant-id="p1"] .ind', h, 8);
  h.det.step();
  assert.deepEqual(h.events.map((e) => [e.state, e.name, e.source]), [['start', 'Budi', 'tile']]);
  h.advance(1200); h.det.step();
  assert.equal(h.events.at(-1).state, 'stop');
  assert.equal(h.events.at(-1).name, 'Budi');
  assert.ok(!h.events.some((e) => e.name === 'Sari'), 'a silent tile must stay silent');
  h.det.tiles.stop();
});

test('tile: a couple of stray mutations is not speech (below threshold)', async () => {
  const doc = page(tile('p1', 'Budi'));
  const h = harness(doc);
  h.det.tiles.start();
  await pulse(doc, '[data-participant-id="p1"] .ind', h, 2);
  h.det.step();
  assert.equal(h.events.length, 0);
  h.det.tiles.stop();
});

test('tile: hover effects (mouse over the tile) are ignored', async () => {
  const doc = page(tile('p1', 'Budi'));
  const h = harness(doc);
  h.det.tiles.start();
  const el = doc.querySelector('[data-participant-id="p1"]');
  el.dispatchEvent(new doc.defaultView.MouseEvent('mousemove', { bubbles: true }));
  await pulse(doc, '[data-participant-id="p1"] .ind', h, 8, 40);
  h.det.step();
  assert.equal(h.events.length, 0);
  h.det.tiles.stop();
});

test('tile: <video> / <img> mutations never count', async () => {
  const doc = page(tile('p1', 'Budi'));
  const h = harness(doc);
  h.det.tiles.start();
  const v = doc.querySelector('video');
  for (let i = 0; i < 10; i++) { v.setAttribute('style', 'width:' + i + 'px'); await flush(); h.advance(50); }
  h.det.step();
  assert.equal(h.events.length, 0);
  h.det.tiles.stop();
});

test('tile: data-audio-level (if Meet exposes it) is used directly', () => {
  const doc = page(tile('p1', 'Budi', '<i data-audio-level="0.4"></i>'));
  const h = harness(doc);
  h.det.step();
  assert.deepEqual(h.events.map((e) => [e.state, e.name]), [['start', 'Budi']]);
});

test('tile: two people at the same time (overlap) are both active; the self tile is flagged', async () => {
  const doc = page(selfTile('Farid') + tile('p1', 'Budi') + tile('p2', 'Sari'));
  const h = harness(doc);
  h.det.tiles.start();
  const a = doc.querySelector('[data-participant-id="p1"] .ind'), b = doc.querySelector('[data-participant-id="p2"] .ind'), s = doc.querySelector('[data-participant-id="me"] .ind');
  for (let i = 0; i < 8; i++) { a.className = 'ind a' + i; b.className = 'ind b' + i; s.className = 'ind s' + i; await flush(); h.advance(80); }
  h.det.step();
  assert.deepEqual(h.det.tracker.speakingNow().sort(), ['Budi', 'Farid', 'Sari']);
  const me = h.events.find((e) => e.name === 'Farid');
  assert.equal(me.self, true);
  assert.equal(h.events.find((e) => e.name === 'Budi').self, false);
  h.det.tiles.stop();
});

test('captions: parse name + latest text, structural (no class names), self word detected', () => {
  const doc = page(`<div role="region" aria-label="Captions">${captionBlock('Budi', 'halo semuanya')}${captionBlock('You', 'oke saya dengar')}</div>`);
  assert.deepEqual(D.parseCaptions(doc), [
    { name: 'Budi', text: 'halo semuanya', self: false },
    { name: 'You', text: 'oke saya dengar', self: true },
  ]);
});

test('captions: works with a localised aria-label and extra wrapper divs', () => {
  const doc = page(`<div role="region" aria-label="Teks"><div><div>${captionBlock('Sari', 'setuju')}</div></div></div>`);
  assert.deepEqual(D.parseCaptions(doc).map((c) => c.name), ['Sari']);
});

test('captions: text that keeps changing = speaking, then stops; unchanged text stays silent', () => {
  const doc = page(`<div role="region" aria-label="Captions">${captionBlock('Budi', 'halo')}</div>`);
  const h = harness(doc);
  h.det.captions.poll(h.now());
  h.advance(500); doc.querySelector('.txt div').textContent = 'halo semuanya'; h.det.captions.poll(h.now());
  h.advance(500); h.det.captions.poll(h.now());                         // unchanged
  h.advance(2500); h.det.tracker.tick(h.now());
  assert.deepEqual(h.events.map((e) => [e.state, e.name, e.source]), [['start', 'Budi', 'caption'], ['stop', 'Budi', 'caption']]);
  assert.equal(h.det.captions.found, true);
});

test('status(): what the popup shows', async () => {
  const doc = page(tile('p1', 'Budi') + tile('p2', 'Sari'));
  const h = harness(doc);
  h.det.tiles.start();
  await pulse(doc, '[data-participant-id="p1"] .ind', h, 8);
  h.det.step();
  const s = h.det.status();
  assert.equal(s.tiles, 2);
  assert.deepEqual(s.speaking, ['Budi']);
  assert.equal(s.captionsFound, false);
  h.det.tiles.stop();
});

test('flush() ends active speech so nothing stays open when the page closes', async () => {
  const doc = page(tile('p1', 'Budi'));
  const h = harness(doc);
  h.det.tiles.start();
  await pulse(doc, '[data-participant-id="p1"] .ind', h, 8);
  h.det.step();
  h.det.tracker.flush(h.now());
  assert.equal(h.events.at(-1).state, 'stop');
  assert.deepEqual(h.det.tracker.speakingNow(), []);
  h.det.tiles.stop();
});
