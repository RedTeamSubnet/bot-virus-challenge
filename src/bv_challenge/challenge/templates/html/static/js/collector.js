/**
 * Bot Virus — behavior metrics collector (pure, DOM-free).
 *
 * This module only *accumulates* raw browser-behavior samples into a plain
 * payload object. It deliberately contains NO scoring logic and no detection
 * heuristics — those live server-side in the private detector. Keeping this
 * layer dumb makes it trivially unit-testable in Node and keeps the public
 * frontend free of anything a miner could game.
 *
 * Every field stored here is a *raw observation* (a count, a coordinate, a
 * timestamp, an environment string, a boolean intent flag) — never a formula.
 * The advanced behavior analysis happens entirely in the private scorer.
 *
 * Credential safety: key events store ONLY timing — never the typed character —
 * and `taskProgress` records only *whether* a field was typed into, never the
 * value. Input values are never read or persisted anywhere in this layer.
 *
 * UMD wrapper: usable as a classic browser script (window.BVCollector) and as a
 * CommonJS module (require) so the same file is exercised by the Node tests.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.BVCollector = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Cap every series so a long-lived (or adversarial) session cannot grow the
  // payload without bound.
  var DEFAULT_MAX_SAMPLES = 5000;

  // The set of event types we track in the ordered eventSequence / trusted
  // stats. Keeping it explicit avoids unbounded key growth from junk types.
  var SEQUENCE_TYPES = {
    focus: true,
    blur: true,
    keydown: true,
    keyup: true,
    pointerdown: true,
    pointerup: true,
    click: true,
    scroll: true,
    visibilitychange: true
  };

  // Known task-progress intent flags. Unknown flags are ignored so the frontend
  // can never inject arbitrary keys into the payload.
  var PROGRESS_FLAGS = {
    usernameFocused: true,
    usernameTyped: true,
    passwordFocused: true,
    passwordTyped: true,
    verifyHovered: true,
    verifyClicked: true,
    contentScrolled: true,
    endClicked: true
  };

  function createCollector(options) {
    var opts = options || {};
    var maxSamples =
      typeof opts.maxSamples === "number" ? opts.maxSamples : DEFAULT_MAX_SAMPLES;
    var now =
      typeof opts.now === "function"
        ? opts.now
        : function () {
            return Date.now();
          };

    var data = {
      appId: opts.appId || null,
      startedAt: now(),
      endedAt: null,

      // --- legacy raw series (entry shapes unchanged for back-compat) --------
      movements: [],
      clicks: [],
      mouseDowns: [],
      mouseUps: [],
      keydowns: [],
      keyups: [],
      scroll: [],
      buttonHoverToClickTime: null,

      // --- raw environment / session signals (additive) ---------------------
      browserInfo: null,
      pageTimings: {
        pageLoadMs: null,
        firstInteractionMs: null,
        payloadWrittenMs: null
      },
      // Ordered list of important events: { type, t, pt, trusted }.
      // `t` is Date.now (wall clock); `pt` is performance.now (monotonic).
      eventSequence: [],
      // Click/pointer target geometry (no input values — id/class/rect only).
      targets: [],
      // isTrusted tallies per important event type: { type: {trusted, untrusted} }.
      trustedEventStats: {},
      // Boolean intent flags only (never the typed content).
      taskProgress: {
        usernameFocused: false,
        usernameTyped: false,
        passwordFocused: false,
        passwordTyped: false,
        verifyHovered: false,
        verifyClicked: false,
        contentScrolled: false,
        endClicked: false
      },
      // Clock anchor for monotonicity checks (wall vs monotonic at start).
      clock: null
    };

    // type -> series name on `data`
    var SERIES = {
      movements: "movements",
      clicks: "clicks",
      mouseDowns: "mouseDowns",
      mouseUps: "mouseUps",
      keydowns: "keydowns",
      keyups: "keyups",
      scroll: "scroll"
    };

    function push(series, sample) {
      if (series.length < maxSamples) {
        series.push(sample);
      }
    }

    /**
     * Record one behavior sample.
     *
     * Positional fields are kept minimal on purpose. Key events store ONLY a
     * timestamp — never the typed character — so credentials entered in the
     * login form are never captured or persisted.
     */
    function record(type, sample) {
      var seriesName = SERIES[type];
      if (!seriesName) {
        return false;
      }

      var s = sample || {};
      var t = typeof s.t === "number" ? s.t : now();
      var entry;

      switch (type) {
        case "keydowns":
        case "keyups":
          entry = { t: t };
          break;
        case "scroll":
          entry = { y: s.y || 0, t: t };
          break;
        default: // movements, clicks, mouseDowns, mouseUps
          entry = { x: s.x || 0, y: s.y || 0, t: t };
      }

      push(data[seriesName], entry);
      return true;
    }

    /**
     * Record an ordered, high-signal event with timing-integrity fields and an
     * isTrusted flag. Updates trustedEventStats as a side effect. Unknown event
     * types are ignored.
     */
    function recordEvent(type, sample) {
      if (!SEQUENCE_TYPES[type]) {
        return false;
      }
      var s = sample || {};
      var t = typeof s.t === "number" ? s.t : now();
      var pt = typeof s.pt === "number" ? s.pt : null;
      var trusted = s.trusted === true;

      push(data.eventSequence, { type: type, t: t, pt: pt, trusted: trusted });

      var stat = data.trustedEventStats[type];
      if (!stat) {
        stat = { trusted: 0, untrusted: 0 };
        data.trustedEventStats[type] = stat;
      }
      if (trusted) {
        stat.trusted += 1;
      } else {
        stat.untrusted += 1;
      }
      return true;
    }

    /**
     * Record a click/pointer target's geometry. Stores element id/class, its
     * bounding box, the pointer coordinates, scroll position and visibility.
     * Never reads or stores the element's value.
     */
    function recordTarget(sample) {
      var s = sample || {};
      var rect = s.rect || {};
      push(data.targets, {
        kind: typeof s.kind === "string" ? s.kind : null,
        id: typeof s.id === "string" ? s.id : null,
        cls: typeof s.cls === "string" ? s.cls : null,
        rect: {
          x: rect.x || 0,
          y: rect.y || 0,
          w: rect.w || 0,
          h: rect.h || 0
        },
        cx: typeof s.cx === "number" ? s.cx : null,
        cy: typeof s.cy === "number" ? s.cy : null,
        scrollY: typeof s.scrollY === "number" ? s.scrollY : 0,
        visible: s.visible === true,
        t: typeof s.t === "number" ? s.t : now()
      });
      return true;
    }

    /**
     * Store the raw browser/environment snapshot. First write wins so a single
     * session reports one consistent environment.
     */
    function setBrowserInfo(info) {
      if (data.browserInfo === null && info && typeof info === "object") {
        data.browserInfo = info;
      }
    }

    /** Set a single pageTimings field once (first write wins). */
    function setPageTiming(key, value) {
      if (
        Object.prototype.hasOwnProperty.call(data.pageTimings, key) &&
        data.pageTimings[key] === null &&
        typeof value === "number"
      ) {
        data.pageTimings[key] = value;
      }
    }

    /** Store the clock anchor { dateNow, perfNow } once. */
    function setClock(anchor) {
      if (data.clock === null && anchor && typeof anchor === "object") {
        data.clock = {
          dateNow: typeof anchor.dateNow === "number" ? anchor.dateNow : null,
          perfNow: typeof anchor.perfNow === "number" ? anchor.perfNow : null
        };
      }
    }

    /** Flip a known taskProgress flag. Unknown flags are ignored. */
    function setProgress(flag, value) {
      if (PROGRESS_FLAGS[flag]) {
        data.taskProgress[flag] = value === undefined ? true : !!value;
      }
    }

    /**
     * Set the elapsed time (ms) between first hovering a button and clicking it.
     * First write wins; later calls are ignored.
     */
    function setButtonHoverToClickTime(ms) {
      if (data.buttonHoverToClickTime === null && typeof ms === "number" && ms >= 0) {
        data.buttonHoverToClickTime = ms;
      }
    }

    function finalize() {
      if (data.endedAt === null) {
        data.endedAt = now();
      }
      return data;
    }

    function toJSON() {
      return data;
    }

    function counts() {
      return {
        movements: data.movements.length,
        clicks: data.clicks.length,
        mouseDowns: data.mouseDowns.length,
        mouseUps: data.mouseUps.length,
        keydowns: data.keydowns.length,
        keyups: data.keyups.length,
        scroll: data.scroll.length,
        eventSequence: data.eventSequence.length,
        targets: data.targets.length
      };
    }

    return {
      data: data,
      record: record,
      recordEvent: recordEvent,
      recordTarget: recordTarget,
      setBrowserInfo: setBrowserInfo,
      setPageTiming: setPageTiming,
      setClock: setClock,
      setProgress: setProgress,
      setButtonHoverToClickTime: setButtonHoverToClickTime,
      finalize: finalize,
      toJSON: toJSON,
      counts: counts
    };
  }

  return { createCollector: createCollector, DEFAULT_MAX_SAMPLES: DEFAULT_MAX_SAMPLES };
});
