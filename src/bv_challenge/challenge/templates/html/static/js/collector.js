/**
 * Bot Virus — browser/runtime integrity collector (pure, DOM-free).
 *
 * HBv6 no longer scores behavior. This module only *accumulates* raw,
 * non-behavioral browser/runtime/session-integrity observations into a plain
 * payload object. It contains NO scoring logic and no detection heuristics —
 * those live server-side in the private detector. Keeping this layer dumb makes
 * it trivially unit-testable in Node and keeps the public frontend free of
 * anything a miner could game.
 *
 * Every field stored here is a *raw observation* (an environment string, a
 * capability boolean, a fingerprint summary, a display dimension) — never a
 * formula. No mouse/keyboard/scroll/click/task signal is collected.
 *
 * The advanced sections (automation, runtimeIntegrity, fingerprint,
 * apiAvailability, navigator, display, correlation, sessionBinding) are written
 * once each (first-write-wins). The legacy raw behavior series are retained as
 * EMPTY lists for backward compatibility with the public Layer 1 shape gate;
 * the page and SDK never populate them.
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

  // Advanced non-behavioral sections the collector accepts. Unknown section
  // names are ignored so the frontend can never inject arbitrary keys.
  var SECTION_KEYS = {
    automation: true,
    runtimeIntegrity: true,
    fingerprint: true,
    apiAvailability: true,
    navigator: true,
    display: true,
    correlation: true,
    sessionBinding: true
  };

  // Legacy raw behavior series. Retained as empty lists only so older payloads
  // and the public shape gate stay happy — never populated in HBv6.
  var LEGACY_SERIES = [
    "movements",
    "clicks",
    "mouseDowns",
    "mouseUps",
    "keydowns",
    "keyups",
    "scroll"
  ];

  function createCollector(options) {
    var opts = options || {};
    var now =
      typeof opts.now === "function"
        ? opts.now
        : function () {
            return Date.now();
          };

    var data = {
      schemaVersion: opts.schemaVersion || null,
      sessionId: opts.sessionId || null,
      startedAt: now(),
      endedAt: null,

      // --- raw browser/runtime integrity sections (first-write-wins) --------
      browserInfo: null,
      automation: null,
      runtimeIntegrity: null,
      fingerprint: null,
      apiAvailability: null,
      navigator: null,
      display: null,
      correlation: null,
      sessionBinding: null,

      // Page load timing (navigation timing API) — non-behavioral.
      pageTimings: { pageLoadMs: null }
    };

    // Legacy empty series (back-compat with the public Layer 1 shape gate).
    for (var i = 0; i < LEGACY_SERIES.length; i++) {
      data[LEGACY_SERIES[i]] = [];
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

    /**
     * Store one advanced non-behavioral section (first-write-wins). Unknown
     * section names are ignored.
     */
    function setSection(name, value) {
      if (
        SECTION_KEYS[name] &&
        data[name] === null &&
        value &&
        typeof value === "object"
      ) {
        data[name] = value;
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

    function finalize() {
      if (data.endedAt === null) {
        data.endedAt = now();
      }
      return data;
    }

    function toJSON() {
      return data;
    }

    return {
      data: data,
      setBrowserInfo: setBrowserInfo,
      setSection: setSection,
      setPageTiming: setPageTiming,
      finalize: finalize,
      toJSON: toJSON
    };
  }

  return { createCollector: createCollector, SECTION_KEYS: SECTION_KEYS };
});
