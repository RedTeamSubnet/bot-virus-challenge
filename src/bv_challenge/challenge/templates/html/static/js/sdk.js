/**
 * Bot Virus — browser SDK (HBv6 non-behavioral runtime integrity).
 *
 * Responsibilities (only these):
 *   1. On load, collect raw NON-behavioral browser/runtime/session integrity
 *      signals (browserInfo, automation indicators, API availability, runtime
 *      integrity, permissions probe, WebGL/render + canvas/audio fingerprint
 *      summaries, display/viewport/DPR, navigator/userAgent/platform/vendor
 *      consistency, and session-binding fields).
 *   2. Build the payload via the pure BVCollector.
 *   3. Encrypt the payload for this session's public key and POST it to
 *      window.TASK_CONFIG.evalUrl (the /_eval endpoint) using browser fetch().
 *   4. Mark window.BV_SUBMITTED = true after a successful submit and surface a
 *      generic status ("Verification complete") on #status.
 *
 * It does NOT score anything and collects NO behavior (no mouse, keyboard,
 * scroll, click, hover, or task-progress signal). All scoring and detection
 * heuristics live in the private detector; this layer only reports raw,
 * observable browser facts.
 *
 * Encryption (hybrid, matches the server's private decryptor):
 *   blob = b64(RSA-OAEP-SHA256(aesKey)) "::" b64(RSA-OAEP-SHA256(iv))
 *          "::" b64(AES-256-CBC-PKCS7(plaintext))
 */
(function () {
  "use strict";

  var CFG =
    window.TASK_CONFIG && typeof window.TASK_CONFIG === "object"
      ? window.TASK_CONFIG
      : {};
  var SESSION =
    window.BV_SESSION && typeof window.BV_SESSION === "object"
      ? window.BV_SESSION
      : {};
  var EVAL_URL = CFG.evalUrl || "/_eval";
  var PAYLOAD_KEY = CFG.payloadKey || "data";
  var AUTO_SUBMIT = CFG.autoSubmit !== false;

  var factory = window.BVCollector;
  if (!factory || typeof factory.createCollector !== "function") {
    // collector.js failed to load; fail quietly rather than throwing on the page.
    return;
  }

  var collector = factory.createCollector({
    sessionId: SESSION.sessionId || null,
    schemaVersion: SESSION.schemaVersion || null
  });

  // --- helpers --------------------------------------------------------------
  function setStatus(text) {
    try {
      var el = document.getElementById("status");
      if (el) {
        el.textContent = text;
      }
    } catch (e) {
      /* status element optional */
    }
  }

  function safe(fn, fallback) {
    try {
      var v = fn();
      return v === undefined ? fallback : v;
    } catch (e) {
      return fallback;
    }
  }

  function isNativeFn(fn) {
    // A native function's source ends in "{ [native code] }". A tampered /
    // re-defined function (common in automation harnesses) typically does not.
    return safe(function () {
      return typeof fn === "function" && /\{\s*\[native code\]\s*\}/.test(
        Function.prototype.toString.call(fn)
      );
    }, false);
  }

  // --- raw signal collection (no analysis) ----------------------------------
  function collectBrowserInfo() {
    var nav = (typeof navigator !== "undefined" && navigator) || {};
    var scr = (typeof screen !== "undefined" && screen) || {};
    var tz = safe(function () {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
    }, null);
    var uaData = safe(function () {
      if (!nav.userAgentData) {
        return null;
      }
      return {
        mobile: !!nav.userAgentData.mobile,
        platform: nav.userAgentData.platform || null,
        brands: nav.userAgentData.brands || null
      };
    }, null);

    collector.setBrowserInfo({
      userAgent: typeof nav.userAgent === "string" ? nav.userAgent : null,
      platform: typeof nav.platform === "string" ? nav.platform : null,
      vendor: typeof nav.vendor === "string" ? nav.vendor : null,
      language: typeof nav.language === "string" ? nav.language : null,
      languages: safe(function () {
        return nav.languages ? Array.prototype.slice.call(nav.languages) : null;
      }, null),
      timezone: tz,
      webdriver: nav.webdriver === true,
      hardwareConcurrency:
        typeof nav.hardwareConcurrency === "number" ? nav.hardwareConcurrency : null,
      deviceMemory: typeof nav.deviceMemory === "number" ? nav.deviceMemory : null,
      maxTouchPoints: typeof nav.maxTouchPoints === "number" ? nav.maxTouchPoints : 0,
      touchSupport:
        "ontouchstart" in window ||
        (typeof nav.maxTouchPoints === "number" && nav.maxTouchPoints > 0),
      pluginsLength:
        nav.plugins && typeof nav.plugins.length === "number" ? nav.plugins.length : 0,
      mimeTypesLength:
        nav.mimeTypes && typeof nav.mimeTypes.length === "number"
          ? nav.mimeTypes.length
          : 0,
      mobile: uaData ? !!uaData.mobile : null,
      userAgentData: uaData,
      outerWidth: typeof window.outerWidth === "number" ? window.outerWidth : 0,
      outerHeight: typeof window.outerHeight === "number" ? window.outerHeight : 0,
      width: scr.width || 0,
      height: scr.height || 0,
      availWidth: scr.availWidth || 0,
      availHeight: scr.availHeight || 0,
      colorDepth: typeof scr.colorDepth === "number" ? scr.colorDepth : null,
      pixelDepth: typeof scr.pixelDepth === "number" ? scr.pixelDepth : null,
      devicePixelRatio:
        typeof window.devicePixelRatio === "number" ? window.devicePixelRatio : null,
      viewport: {
        width: window.innerWidth || 0,
        height: window.innerHeight || 0
      },
      screen: { width: scr.width || 0, height: scr.height || 0 },
      windowProps: safe(function () {
        // Genuine automation-hook names that leak onto window in automated
        // browsers. A clean browser exposes NONE of these — standard properties
        // (e.g. navigator.webdriver) are deliberately excluded so we never plant
        // an automation token into an otherwise-clean payload.
        var probes = [
          "callPhantom",
          "_phantom",
          "__nightmare",
          "domAutomation",
          "domAutomationController",
          "__webdriver_evaluate",
          "__selenium_evaluate",
          "__driver_evaluate",
          "_Selenium_IDE_Recorder",
          "__webdriverFunc",
          "__playwright",
          "__puppeteer_evaluation_script__"
        ];
        var present = [];
        for (var i = 0; i < probes.length; i++) {
          if (probes[i] in window) {
            present.push(probes[i]);
          }
        }
        return present;
      }, []),
      navigatorProps: safe(function () {
        // Automation-injected navigator leak props (dunder hooks). Standard
        // properties such as `webdriver` are NOT listed here.
        var probes = [
          "__webdriver_evaluate",
          "__selenium_unwrapped",
          "__webdriver_script_function",
          "__webdriver_script_fn",
          "__fxdriver_evaluate",
          "__driver_unwrapped",
          "__webdriver_unwrapped",
          "__driver_evaluate",
          "__selenium_evaluate",
          "__fxdriver_unwrapped"
        ];
        var present = [];
        for (var i = 0; i < probes.length; i++) {
          if (probes[i] in nav) {
            present.push(probes[i]);
          }
        }
        return present;
      }, [])
    });
  }

  function collectAutomation() {
    var nav = (typeof navigator !== "undefined" && navigator) || {};
    var ua = safe(function () {
      return (nav.userAgent || "").toLowerCase();
    }, "");
    collector.setSection("automation", {
      webdriver: nav.webdriver === true,
      dom:
        "domAutomation" in window ||
        "domAutomationController" in window,
      headlesschrome: ua.indexOf("headlesschrome") !== -1,
      phantom: "callPhantom" in window || "_phantom" in window,
      nightmare: "__nightmare" in window,
      puppeteer: "__puppeteer_evaluation_script__" in window,
      playwright: "__playwright" in window,
      selenium:
        "__selenium_evaluate" in window || "_Selenium_IDE_Recorder" in window
    });
  }

  function collectRuntimeIntegrity() {
    collector.setSection("runtimeIntegrity", {
      toStringNative: isNativeFn(Function.prototype.toString),
      functionToStringNative: isNativeFn(function () {}.constructor),
      permissionsQueryNative: safe(function () {
        return navigator.permissions && isNativeFn(navigator.permissions.query);
      }, false),
      getParameterNative: safe(function () {
        var gl = getGl();
        return !!gl && isNativeFn(gl.getParameter);
      }, false),
      toDataURLNative: safe(function () {
        return isNativeFn(HTMLCanvasElement.prototype.toDataURL);
      }, false),
      hasChrome: safe(function () {
        return !!window.chrome;
      }, false)
    });
  }

  function collectApiAvailability() {
    collector.setSection("apiAvailability", {
      webgl: safe(function () {
        return !!getGl();
      }, false),
      audioContext: "AudioContext" in window || "webkitAudioContext" in window,
      rtc: "RTCPeerConnection" in window,
      permissions: safe(function () {
        return !!(navigator.permissions && navigator.permissions.query);
      }, false),
      mimeTypes: safe(function () {
        return !!(navigator.mimeTypes && navigator.mimeTypes.length >= 0);
      }, false),
      serviceWorker: "serviceWorker" in navigator,
      webAssembly: "WebAssembly" in window,
      indexedDb: "indexedDB" in window
    });
  }

  var _glCache;
  function getGl() {
    if (_glCache !== undefined) {
      return _glCache;
    }
    _glCache = safe(function () {
      var canvas = document.createElement("canvas");
      return (
        canvas.getContext("webgl") ||
        canvas.getContext("experimental-webgl") ||
        null
      );
    }, null);
    return _glCache;
  }

  function collectFingerprint() {
    var webgl = safe(function () {
      var gl = getGl();
      if (!gl) {
        return null;
      }
      var dbg = gl.getExtension("WEBGL_debug_renderer_info");
      var vendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null;
      var renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null;
      var rl = (renderer || "").toLowerCase();
      return {
        vendor: vendor || null,
        renderer: renderer || null,
        maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE) || 0,
        extensionsCount: safe(function () {
          var ext = gl.getSupportedExtensions();
          return ext ? ext.length : 0;
        }, 0),
        software:
          rl.indexOf("swiftshader") !== -1 ||
          rl.indexOf("llvmpipe") !== -1 ||
          rl.indexOf("software") !== -1,
        swiftshader: rl.indexOf("swiftshader") !== -1
      };
    }, null);

    var canvas = safe(function () {
      var c = document.createElement("canvas");
      c.width = 64;
      c.height = 24;
      var ctx = c.getContext("2d");
      ctx.textBaseline = "top";
      ctx.font = "14px 'Arial'";
      ctx.fillStyle = "#069";
      ctx.fillText("bv-runtime", 2, 2);
      var url = c.toDataURL();
      return {
        toDataURLNative: isNativeFn(HTMLCanvasElement.prototype.toDataURL),
        hash: shortHash(url),
        length: url.length
      };
    }, null);

    var audio = safe(function () {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) {
        return null;
      }
      return { available: true, sampleRate: safe(function () {
        var c = new Ctx();
        var sr = c.sampleRate;
        if (c.close) {
          c.close();
        }
        return sr;
      }, null) };
    }, null);

    collector.setSection("fingerprint", {
      webgl: webgl,
      canvas: canvas,
      audio: audio
    });
  }

  function collectNavigator() {
    var nav = (typeof navigator !== "undefined" && navigator) || {};
    collector.setSection("navigator", {
      userAgent: typeof nav.userAgent === "string" ? nav.userAgent : null,
      vendor: typeof nav.vendor === "string" ? nav.vendor : null,
      platform: typeof nav.platform === "string" ? nav.platform : null,
      language: typeof nav.language === "string" ? nav.language : null,
      hardwareConcurrency:
        typeof nav.hardwareConcurrency === "number" ? nav.hardwareConcurrency : null
    });
  }

  function collectDisplay() {
    var scr = (typeof screen !== "undefined" && screen) || {};
    collector.setSection("display", {
      width: scr.width || 0,
      height: scr.height || 0,
      availWidth: scr.availWidth || 0,
      availHeight: scr.availHeight || 0,
      colorDepth: typeof scr.colorDepth === "number" ? scr.colorDepth : null,
      pixelDepth: typeof scr.pixelDepth === "number" ? scr.pixelDepth : null,
      devicePixelRatio:
        typeof window.devicePixelRatio === "number" ? window.devicePixelRatio : null,
      innerWidth: window.innerWidth || 0,
      innerHeight: window.innerHeight || 0,
      outerWidth: typeof window.outerWidth === "number" ? window.outerWidth : 0,
      outerHeight: typeof window.outerHeight === "number" ? window.outerHeight : 0
    });
  }

  function collectCorrelation() {
    var nav = (typeof navigator !== "undefined" && navigator) || {};
    var uaPlatform = safe(function () {
      return nav.userAgentData && nav.userAgentData.platform
        ? nav.userAgentData.platform
        : null;
    }, null);
    collector.setSection("correlation", {
      // Raw consistency facts — the private detector decides what they mean.
      uaHasLinux: safe(function () {
        return (nav.userAgent || "").toLowerCase().indexOf("linux") !== -1;
      }, false),
      platform: typeof nav.platform === "string" ? nav.platform : null,
      uaDataPlatform: uaPlatform,
      touchVsMaxTouchPoints: safe(function () {
        var hasTouchApi = "ontouchstart" in window;
        var mtp = typeof nav.maxTouchPoints === "number" ? nav.maxTouchPoints : 0;
        return { hasTouchApi: hasTouchApi, maxTouchPoints: mtp };
      }, null)
    });
  }

  function collectSessionBinding() {
    collector.setSection("sessionBinding", {
      sessionId: SESSION.sessionId || null,
      nonce: SESSION.nonce || null,
      publicKeyId: SESSION.publicKeyId || null,
      configHash: SESSION.configHash || null,
      schemaVersion: SESSION.schemaVersion || null,
      href: safe(function () {
        return window.location ? window.location.href : null;
      }, null),
      origin: safe(function () {
        return window.location ? window.location.origin : null;
      }, null)
    });
  }

  function capturePageLoad() {
    var loadMs = safe(function () {
      if (typeof performance === "undefined") {
        return null;
      }
      if (typeof performance.getEntriesByType === "function") {
        var nav = performance.getEntriesByType("navigation");
        if (nav && nav.length && typeof nav[0].duration === "number") {
          return nav[0].duration;
        }
      }
      if (performance.timing) {
        var t = performance.timing;
        if (t.loadEventEnd && t.navigationStart) {
          return t.loadEventEnd - t.navigationStart;
        }
      }
      return null;
    }, null);
    if (typeof loadMs === "number" && loadMs >= 0) {
      collector.setPageTiming("pageLoadMs", loadMs);
    }
  }

  // Tiny, non-cryptographic string digest for fingerprint summaries (so we
  // never ship a raw multi-KB data URL). Not used for any security decision.
  function shortHash(str) {
    var h = 5381;
    for (var i = 0; i < str.length; i++) {
      h = ((h << 5) + h + str.charCodeAt(i)) >>> 0;
    }
    return ("00000000" + h.toString(16)).slice(-8);
  }

  // --- encryption (hybrid RSA-OAEP + AES-256-CBC) ---------------------------
  function b64(bytes) {
    var bin = "";
    var arr = new Uint8Array(bytes);
    for (var i = 0; i < arr.length; i++) {
      bin += String.fromCharCode(arr[i]);
    }
    return window.btoa(bin);
  }

  function pemToDer(pem) {
    var body = pem
      .replace(/-----BEGIN [^-]+-----/, "")
      .replace(/-----END [^-]+-----/, "")
      .replace(/[\r\n\s]/g, "");
    var bin = window.atob(body);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) {
      bytes[i] = bin.charCodeAt(i);
    }
    return bytes.buffer;
  }

  function encryptPayload(plaintext, publicKeyPem) {
    var subtle = window.crypto && window.crypto.subtle;
    if (!subtle) {
      return Promise.reject(new Error("WebCrypto SubtleCrypto unavailable"));
    }
    var enc = new TextEncoder();
    var iv = window.crypto.getRandomValues(new Uint8Array(16));
    var rawAesKey = window.crypto.getRandomValues(new Uint8Array(32));
    var pubKey;

    return subtle
      .importKey(
        "spki",
        pemToDer(publicKeyPem),
        { name: "RSA-OAEP", hash: "SHA-256" },
        false,
        ["encrypt"]
      )
      .then(function (key) {
        pubKey = key;
        return subtle.importKey(
          "raw",
          rawAesKey,
          { name: "AES-CBC" },
          false,
          ["encrypt"]
        );
      })
      .then(function (aesKey) {
        return Promise.all([
          subtle.encrypt({ name: "AES-CBC", iv: iv }, aesKey, enc.encode(plaintext)),
          subtle.encrypt({ name: "RSA-OAEP" }, pubKey, rawAesKey),
          subtle.encrypt({ name: "RSA-OAEP" }, pubKey, iv)
        ]);
      })
      .then(function (parts) {
        var ct = parts[0];
        var encKey = parts[1];
        var encIv = parts[2];
        return b64(encKey) + "::" + b64(encIv) + "::" + b64(ct);
      });
  }

  // --- submit ---------------------------------------------------------------
  function submit(blob) {
    return window
      .fetch(EVAL_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ error: { data: blob } })
      })
      .then(function (res) {
        if (!res || !res.ok) {
          throw new Error("eval submit failed: " + (res && res.status));
        }
        window.BV_SUBMITTED = true;
        setStatus("Verification complete");
        return true;
      });
  }

  // --- main flow ------------------------------------------------------------
  function run() {
    capturePageLoad();
    collectBrowserInfo();
    collectAutomation();
    collectRuntimeIntegrity();
    collectApiAvailability();
    collectFingerprint();
    collectNavigator();
    collectDisplay();
    collectCorrelation();
    collectSessionBinding();
    collector.finalize();

    var payloadStr = JSON.stringify(collector.toJSON());

    // Keep a local copy for debugging/inspection; submission is the real path.
    safe(function () {
      window.localStorage.setItem(PAYLOAD_KEY, payloadStr);
    });

    if (!AUTO_SUBMIT) {
      return Promise.resolve(false);
    }

    var publicKeyPem = window.PUBLIC_KEY;
    if (!publicKeyPem) {
      setStatus("Verification unavailable");
      return Promise.resolve(false);
    }

    return encryptPayload(payloadStr, publicKeyPem)
      .then(submit)
      .catch(function (err) {
        setStatus("Verification could not be submitted");
        safe(function () {
          console.error("[BV-SDK] submit failed:", err && err.message);
        });
        return false;
      });
  }

  // Public surface for tests/automation. `export()` returns the payload string;
  // `submitNow()` re-runs the collect+encrypt+submit flow.
  window.BV_SDK = {
    collector: collector,
    export: function () {
      return JSON.stringify(collector.toJSON());
    },
    submitNow: run
  };

  // Auto-run on load.
  run();
})();
