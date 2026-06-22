/**
 * Bot Virus — browser SDK (thin DOM wiring around BVCollector).
 *
 * Responsibilities (only these):
 *   1. Wire DOM events to the collector (movements/clicks/mouse/keys/scroll,
 *      plus the ordered eventSequence, pointer/focus/visibility events).
 *   2. Snapshot the raw browser/environment (browserInfo) and page timings.
 *   3. Track boolean task-progress *intent* flags (never typed content).
 *   4. Capture click/pointer target geometry (id/class/rect — never values).
 *   5. Persist the collected payload to localStorage[TASK_CONFIG.payloadKey].
 *   6. Assign the per-session input ids from window.ACTIONS_LIST so the
 *      automation can target the username/password fields.
 *
 * It does NOT score anything and does NOT submit to /_eval. Encrypting the
 * payload with window.PUBLIC_KEY and POSTing it to /_eval is a deliberate later
 * step (see BV_SDK.export). All scoring and detection heuristics live in the
 * private detector — this layer only collects raw observations.
 *
 * Credential safety: key events record timing only; task-progress records only
 * *whether* a field was typed into. Input values are never read.
 */
(function () {
  "use strict";

  // Stable, machine-readable selectors. Prefer window.TASK_CONFIG (injected on
  // the page) so the automation never has to parse English page text; fall back
  // to the known defaults if it is missing.
  var CFG = (window.TASK_CONFIG && typeof window.TASK_CONFIG === "object")
    ? window.TASK_CONFIG
    : {};
  var SEL = {
    username: CFG.usernameInput || 'input[name="username"]',
    password: CFG.passwordInput || 'input[name="password"]',
    verify: CFG.verifyButton || "#login-button",
    scroll: CFG.scrollContainer || "#content",
    end: CFG.endSessionButton || ".end-session"
  };
  var STORAGE_KEY = CFG.payloadKey || "data";

  var factory = window.BVCollector;
  if (!factory || typeof factory.createCollector !== "function") {
    // collector.js failed to load; fail quietly rather than throwing on the page.
    return;
  }

  var collector = factory.createCollector({ appId: window.APP_ID || null });

  // --- timing helpers -------------------------------------------------------
  function perfNow() {
    try {
      if (typeof performance !== "undefined" && typeof performance.now === "function") {
        return performance.now();
      }
    } catch (e) {
      /* performance unavailable */
    }
    return null;
  }

  // Anchor wall-clock against the monotonic clock so the detector can check for
  // impossible / rewound timing without us doing any analysis here.
  collector.setClock({ dateNow: Date.now(), perfNow: perfNow() });

  var firstInteractionRecorded = false;
  function markFirstInteraction() {
    if (!firstInteractionRecorded) {
      firstInteractionRecorded = true;
      var pn = perfNow();
      if (pn !== null) {
        collector.setPageTiming("firstInteractionMs", pn);
      }
    }
  }

  var payloadWritten = false;
  function persist() {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(collector.toJSON()));
      if (!payloadWritten) {
        payloadWritten = true;
        var pn = perfNow();
        if (pn !== null) {
          collector.setPageTiming("payloadWrittenMs", pn);
        }
      }
    } catch (e) {
      /* storage may be unavailable; never break the page */
    }
  }

  function on(target, type, handler) {
    if (target && typeof target.addEventListener === "function") {
      target.addEventListener(type, handler, { passive: true });
    }
  }

  // Push an ordered, timing-stamped event (and update isTrusted tallies).
  function seq(type, e) {
    collector.recordEvent(type, {
      t: Date.now(),
      pt: perfNow(),
      trusted: !!(e && e.isTrusted)
    });
  }

  // --- browser / environment snapshot (raw, no analysis) --------------------
  (function snapshotBrowser() {
    try {
      var nav = (typeof navigator !== "undefined" && navigator) || {};
      var scr = (typeof screen !== "undefined" && screen) || {};
      var tz = null;
      try {
        tz = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
      } catch (e) {
        tz = null;
      }
      collector.setBrowserInfo({
        userAgent: typeof nav.userAgent === "string" ? nav.userAgent : null,
        platform: typeof nav.platform === "string" ? nav.platform : null,
        language: typeof nav.language === "string" ? nav.language : null,
        timezone: tz,
        viewport: {
          width: window.innerWidth || 0,
          height: window.innerHeight || 0
        },
        screen: {
          width: scr.width || 0,
          height: scr.height || 0
        },
        devicePixelRatio:
          typeof window.devicePixelRatio === "number" ? window.devicePixelRatio : null,
        webdriver: nav.webdriver === true,
        hardwareConcurrency:
          typeof nav.hardwareConcurrency === "number" ? nav.hardwareConcurrency : null,
        pluginsLength:
          nav.plugins && typeof nav.plugins.length === "number" ? nav.plugins.length : 0,
        touchSupport:
          "ontouchstart" in window ||
          (typeof nav.maxTouchPoints === "number" && nav.maxTouchPoints > 0)
      });
    } catch (e) {
      /* environment unavailable — leave browserInfo unset */
    }
  })();

  // --- page load timing (navigation timing API) -----------------------------
  (function capturePageLoad() {
    try {
      var loadMs = null;
      if (typeof performance !== "undefined") {
        if (typeof performance.getEntriesByType === "function") {
          var navEntries = performance.getEntriesByType("navigation");
          if (navEntries && navEntries.length && typeof navEntries[0].duration === "number") {
            loadMs = navEntries[0].duration;
          }
        }
        if (loadMs === null && performance.timing) {
          var tmg = performance.timing;
          if (tmg.loadEventEnd && tmg.navigationStart) {
            loadMs = tmg.loadEventEnd - tmg.navigationStart;
          }
        }
      }
      if (typeof loadMs === "number" && loadMs >= 0) {
        collector.setPageTiming("pageLoadMs", loadMs);
      }
    } catch (e) {
      /* navigation timing unavailable */
    }
  })();

  // --- Assign per-session input ids from ACTIONS_LIST -----------------------
  // window.ACTIONS_LIST is a JSON *string* (see the page template). It is a list
  // of challenge action-lists; each input action carries the dynamic element id
  // the automation will look up by name ("username" / "password").
  (function assignInputIds() {
    try {
      var actions = JSON.parse(window.ACTIONS_LIST || "[]");
      var flat = Array.isArray(actions[0]) ? actions[0] : actions;
      for (var i = 0; i < flat.length; i++) {
        var a = flat[i];
        if (a && a.type === "input" && a.selector && a.selector.id) {
          var el = document.querySelector('input[name="' + a.selector.name + '"]');
          if (el) {
            el.id = a.selector.id;
          }
        }
      }
    } catch (e) {
      /* malformed/absent ACTIONS_LIST — leave default ids in place */
    }
  })();

  // --- target geometry (id/class/rect only — never values) ------------------
  function describeTarget(el, kind, e) {
    if (!el || typeof el.getBoundingClientRect !== "function") {
      return;
    }
    var rect;
    try {
      rect = el.getBoundingClientRect();
    } catch (err) {
      rect = { left: 0, top: 0, width: 0, height: 0 };
    }
    var cx = e && typeof e.clientX === "number" ? e.clientX : null;
    var cy = e && typeof e.clientY === "number" ? e.clientY : null;
    var visible = !!(rect.width > 0 && rect.height > 0);
    collector.recordTarget({
      kind: kind,
      id: typeof el.id === "string" && el.id ? el.id : null,
      cls: typeof el.className === "string" && el.className ? el.className : null,
      rect: { x: rect.left || 0, y: rect.top || 0, w: rect.width || 0, h: rect.height || 0 },
      cx: cx,
      cy: cy,
      scrollY: window.scrollY || window.pageYOffset || 0,
      visible: visible,
      t: Date.now()
    });
  }

  // --- Behavior events ------------------------------------------------------
  on(document, "mousemove", function (e) {
    markFirstInteraction();
    collector.record("movements", { x: e.clientX, y: e.clientY });
    persist();
  });
  on(document, "mousedown", function (e) {
    collector.record("mouseDowns", { x: e.clientX, y: e.clientY });
    persist();
  });
  on(document, "mouseup", function (e) {
    collector.record("mouseUps", { x: e.clientX, y: e.clientY });
    persist();
  });
  // Pointer events feed the ordered sequence + trusted stats (mouse events keep
  // populating the legacy positional series above).
  on(document, "pointerdown", function (e) {
    markFirstInteraction();
    seq("pointerdown", e);
    describeTarget(e.target, "pointerdown", e);
    persist();
  });
  on(document, "pointerup", function (e) {
    seq("pointerup", e);
    persist();
  });
  on(document, "click", function (e) {
    markFirstInteraction();
    collector.record("clicks", { x: e.clientX, y: e.clientY });
    seq("click", e);
    describeTarget(e.target, "click", e);
    persist();
  });
  // Key events: timing only, never the character (avoids capturing credentials).
  on(document, "keydown", function (e) {
    markFirstInteraction();
    collector.record("keydowns", {});
    seq("keydown", e);
    // Intent only: did the user type while a credential field was focused?
    if (focusedField === "username") {
      collector.setProgress("usernameTyped", true);
    } else if (focusedField === "password") {
      collector.setProgress("passwordTyped", true);
    }
    persist();
  });
  on(document, "keyup", function (e) {
    collector.record("keyups", {});
    seq("keyup", e);
    persist();
  });
  on(window, "scroll", function (e) {
    markFirstInteraction();
    collector.record("scroll", { y: window.scrollY || window.pageYOffset || 0 });
    seq("scroll", e);
    collector.setProgress("contentScrolled", true);
    persist();
  });
  on(document, "visibilitychange", function (e) {
    seq("visibilitychange", e);
    persist();
  });

  // --- focus / blur on credential fields (intent flags only) ----------------
  var focusedField = null;
  var usernameEl = document.querySelector(SEL.username);
  var passwordEl = document.querySelector(SEL.password);

  function wireField(el, name) {
    if (!el) {
      return;
    }
    on(el, "focus", function (e) {
      focusedField = name;
      collector.setProgress(name + "Focused", true);
      seq("focus", e);
      persist();
    });
    on(el, "blur", function (e) {
      if (focusedField === name) {
        focusedField = null;
      }
      seq("blur", e);
      persist();
    });
  }
  wireField(usernameEl, "username");
  wireField(passwordEl, "password");

  // --- verify button: hover->click timing + intent flags --------------------
  var verifyBtn = document.querySelector(SEL.verify) || document.getElementById("login-button");
  if (verifyBtn) {
    var hoverAt = null;
    on(verifyBtn, "mouseenter", function () {
      if (hoverAt === null) {
        hoverAt = Date.now();
      }
      collector.setProgress("verifyHovered", true);
      persist();
    });
    on(verifyBtn, "click", function () {
      if (hoverAt !== null) {
        collector.setButtonHoverToClickTime(Date.now() - hoverAt);
      }
      collector.setProgress("verifyClicked", true);
      persist();
    });
  }

  // --- Finalize -------------------------------------------------------------
  function finalize() {
    collector.finalize();
    persist();
    try {
      var c = collector.counts();
      // Surface a non-sensitive summary; the bot mirrors browser console logs.
      console.log(
        "[BV-SDK] finalized: " +
          c.movements +
          " movements, " +
          c.clicks +
          " clicks, " +
          c.keydowns +
          " keydowns, scroll " +
          c.scroll
      );
    } catch (e) {
      /* console may be unavailable */
    }
  }

  var endBtn = document.querySelector(SEL.end);
  on(endBtn, "click", function (e) {
    collector.setProgress("endClicked", true);
    seq("click", e);
    finalize();
  });
  on(window, "beforeunload", finalize);

  // Write once up front so localStorage[payloadKey] exists even before interaction.
  persist();

  // Minimal debug/extension surface. `export()` returns the JSON payload string;
  // wiring encryption (window.PUBLIC_KEY) + POST to /_eval is the next step.
  window.BV_SDK = {
    collector: collector,
    persist: persist,
    finalize: finalize,
    export: function () {
      return JSON.stringify(collector.toJSON());
    }
  };
})();
