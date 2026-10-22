/* meetrec: who is speaking in Google Meet, from the page itself (no Meet API).
 *
 * Two independent signals, neither depends on Meet's obfuscated class names (those change without notice):
 *   tile    : a participant tile ([data-participant-id]) whose subtree keeps mutating attributes (the speaking
 *             indicator animates) = that person is speaking. Hover animations are ignored.
 *   caption : the live-captions region shows "<name> <text>" per speaker; text that changes = that person speaks.
 *             Works for people whose tile is off-screen, but needs captions switched on.
 *
 * Output: events {t, type:"speaking", state:"start"|"stop", name, id, self, source}, t = epoch ms.
 * Pure logic with an injectable clock, so it can be tested with jsdom (see test/).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.MeetrecDetector = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const TILE_SELECTOR = '[data-participant-id]';
  const SELF_WORDS = ['you', 'anda', 'kamu', 'saya', 'ik', 'tú', 'vous', 'du'];
  const DEFAULTS = {
    windowMs: 600,        // tile: look back this long for mutations
    onCount: 4,           // tile: this many mutations in the window = speaking
    tileGapMs: 900,       // tile: quiet this long = stopped
    captionGapMs: 1800,   // caption: no text change this long = stopped
    hoverQuietMs: 1200,   // tile: ignore mutations this soon after the mouse moved over it
    captionPollMs: 400,
    tickMs: 200,
  };

  function cleanName(s) {
    return String(s || '')
      .replace(/​/g, '')
      .replace(/\s*\((presentation|presentasi|presenting|menyajikan)[^)]*\)\s*$/i, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function isSelfWord(name) {
    return SELF_WORDS.includes(String(name || '').trim().toLowerCase());
  }

  /** {name, self} of a participant tile, or null. */
  function tileInfo(tile) {
    const selfEl = tile.matches('[data-self-name]') ? tile : tile.querySelector('[data-self-name]');
    if (selfEl) {
      const n = cleanName(selfEl.getAttribute('data-self-name'));
      if (n) return { name: n, self: true };
    }
    const leaf = tile.querySelector('span.notranslate, .notranslate');
    if (leaf && cleanName(leaf.textContent)) return { name: cleanName(leaf.textContent), self: false };
    const label = tile.getAttribute('aria-label');
    if (label && cleanName(label)) return { name: cleanName(label), self: false };
    return null;
  }

  /** Turns "activity pings" into start/stop events. stop is stamped at the LAST activity, not when we gave up. */
  class SpeakingTracker {
    constructor(emit, opts) {
      this.emit = emit;
      this.opts = Object.assign({}, DEFAULTS, opts);
      this.active = new Map();
      this.seen = new Map(); // name -> total speaking ms (for the popup)
    }
    ping(source, id, name, self, now) {
      const key = source + '|' + id;
      const a = this.active.get(key);
      if (!a) {
        this.active.set(key, { source, id, name, self, start: now, last: now });
        this.emit({ t: now, type: 'speaking', state: 'start', name, id, self, source });
      } else {
        a.last = now;
      }
    }
    _stop(key, a) {
      this.active.delete(key);
      this.seen.set(a.name, (this.seen.get(a.name) || 0) + Math.max(0, a.last - a.start));
      this.emit({ t: a.last, type: 'speaking', state: 'stop', name: a.name, id: a.id, self: a.self, source: a.source });
    }
    tick(now) {
      for (const [key, a] of Array.from(this.active)) {
        const gap = a.source === 'caption' ? this.opts.captionGapMs : this.opts.tileGapMs;
        if (now - a.last > gap) this._stop(key, a);
      }
    }
    flush(now) {
      for (const [key, a] of Array.from(this.active)) { a.last = Math.max(a.last, Math.min(now, a.last + 300)); this._stop(key, a); }
    }
    speakingNow() {
      return Array.from(new Set(Array.from(this.active.values()).map((a) => a.name)));
    }
  }

  class TileWatcher {
    constructor(doc, tracker, opts, clock) {
      this.doc = doc; this.tracker = tracker; this.opts = Object.assign({}, DEFAULTS, opts); this.clock = clock;
      this.hits = new Map();       // tile id -> mutation timestamps
      this.mouse = new Map();      // tile id -> last mouse move over it
      this.rates = new Map();      // tile id -> mutations in the last window (for diagnostics)
      const MO = doc.defaultView.MutationObserver;
      this.observer = new MO((recs) => this.onMutations(recs));
      this._onMouse = (e) => {
        const t = e.target && e.target.closest ? e.target.closest(TILE_SELECTOR) : null;
        if (t) this.mouse.set(t.getAttribute('data-participant-id'), this.clock());
      };
    }
    start() {
      this.observer.observe(this.doc.body, {
        subtree: true, attributes: true, attributeFilter: ['class', 'style', 'data-audio-level', 'aria-hidden'],
      });
      this.doc.addEventListener('mousemove', this._onMouse, true);
    }
    stop() {
      this.observer.disconnect();
      this.doc.removeEventListener('mousemove', this._onMouse, true);
    }
    onMutations(recs) {
      const now = this.clock();
      for (const r of recs) {
        const el = r.target;
        if (!el || !el.closest) continue;
        const tag = el.tagName;
        if (tag === 'VIDEO' || tag === 'CANVAS' || tag === 'IMG') continue;
        const tile = el.closest(TILE_SELECTOR);
        if (!tile) continue;
        const id = tile.getAttribute('data-participant-id');
        const m = this.mouse.get(id);
        if (m && now - m < this.opts.hoverQuietMs) continue; // hover effects, not speech
        let arr = this.hits.get(id);
        if (!arr) { arr = []; this.hits.set(id, arr); }
        arr.push(now);
      }
    }
    scan(now) {
      const tiles = this.doc.querySelectorAll(TILE_SELECTOR);
      for (const tile of tiles) {
        const id = tile.getAttribute('data-participant-id');
        const arr = (this.hits.get(id) || []).filter((t) => now - t <= this.opts.windowMs);
        this.hits.set(id, arr);
        this.rates.set(id, arr.length);
        let speaking = arr.length >= this.opts.onCount;
        const lvl = tile.querySelector('[data-audio-level]'); // direct level, if Meet exposes it
        if (lvl) { const v = parseFloat(lvl.getAttribute('data-audio-level')); if (!isNaN(v) && v > 0.05) speaking = true; }
        if (!speaking) continue;
        const info = tileInfo(tile);
        if (info) this.tracker.ping('tile', id, info.name, info.self, now);
      }
    }
    count() { return this.doc.querySelectorAll(TILE_SELECTOR).length; }
  }

  /** [{name, text, self}] from the captions region(s). Structural: avatar <img> + first <span> (name) + last <div> (text). */
  function parseCaptions(doc) {
    const out = [];
    for (const region of doc.querySelectorAll('[role="region"]')) {
      const imgs = region.querySelectorAll('img');
      if (!imgs.length) continue;
      for (const img of imgs) {
        let block = img;
        while (block.parentElement && block.parentElement !== region && block.parentElement.querySelectorAll('img').length === 1) {
          block = block.parentElement;
        }
        const nameEl = block.querySelector('span');
        const divs = Array.from(block.querySelectorAll('div')).filter((d) => !d.querySelector('img') && d.textContent.trim());
        if (!nameEl || !divs.length) continue;
        const name = cleanName(nameEl.textContent);
        const text = divs[divs.length - 1].textContent.trim();
        if (name && text) out.push({ name, text, self: isSelfWord(name) });
      }
    }
    return out;
  }

  class CaptionWatcher {
    constructor(doc, tracker, selfName) {
      this.doc = doc; this.tracker = tracker; this.selfName = selfName; this.last = new Map(); this.found = false;
    }
    poll(now) {
      const blocks = parseCaptions(this.doc);
      this.found = blocks.length > 0;
      for (const b of blocks) {
        const prev = this.last.get(b.name);
        if (prev !== b.text) {
          this.last.set(b.name, b.text);
          const self = b.self || (this.selfName && b.name === this.selfName());
          this.tracker.ping('caption', b.name, b.name, !!self, now);
        }
      }
    }
  }

  function create(doc, emit, options) {
    const opts = Object.assign({}, DEFAULTS, options);
    const clock = opts.clock || (() => Date.now());
    const tracker = new SpeakingTracker(emit, opts);
    const tiles = new TileWatcher(doc, tracker, opts, clock);
    const captions = new CaptionWatcher(doc, tracker, () => {
      const t = doc.querySelector('[data-self-name]');
      return t ? cleanName(t.getAttribute('data-self-name')) : '';
    });
    let timer = null; let lastCaptionPoll = 0;
    function step() {
      const now = clock();
      tiles.scan(now);
      if (now - lastCaptionPoll >= opts.captionPollMs) { lastCaptionPoll = now; captions.poll(now); }
      tracker.tick(now);
    }
    return {
      start() { tiles.start(); timer = setInterval(step, opts.tickMs); },
      stop() { clearInterval(timer); tiles.stop(); tracker.flush(clock()); },
      step,
      tracker, tiles, captions,
      status() {
        const rates = Array.from(tiles.rates.entries()).sort((a, b) => b[1] - a[1]).slice(0, 5);
        return {
          tiles: tiles.count(),
          captionsFound: captions.found,
          speaking: tracker.speakingNow(),
          totals: Array.from(tracker.seen.entries()).map(([name, ms]) => ({ name, seconds: Math.round(ms / 100) / 10 })),
          topTileRates: rates,
        };
      },
    };
  }

  return { create, parseCaptions, tileInfo, cleanName, isSelfWord, SpeakingTracker, TileWatcher, CaptionWatcher, DEFAULTS, TILE_SELECTOR };
});
