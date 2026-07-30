"use strict";

/**
 * Integration test for the HBv6 browser SDK against a minimal hand-rolled DOM +
 * WebCrypto + fetch shim (no jsdom/playwright needed). Proves the requirements:
 *   - on load the SDK auto-collects NON-behavioral integrity signals
 *     (browserInfo + automation/runtimeIntegrity/apiAvailability/fingerprint/
 *      navigator/display/correlation/sessionBinding),
 *   - it submits the encrypted payload to TASK_CONFIG.evalUrl via fetch(),
 *   - window.BV_SUBMITTED flips true after a successful submit,
 *   - submission happens with NO behavioral input (no mouse/keyboard/scroll),
 *   - the encrypted blob is the hybrid wire format the server decryptor expects
 *     (key "::" iv "::" text) and round-trips back to the exact payload,
 *   - the payload carries no behavioral fields beyond the empty legacy arrays.
 *
 *   node tests/web/sdk.test.js
 */

const assert = require("assert");
const path = require("path");
const { webcrypto } = require("crypto");

const subtle = webcrypto.subtle;
const JS_DIR = path.join(
  __dirname, "..", "..", "src", "bv_challenge", "challenge",
  "templates", "html", "static", "js"
);

// ALPHANUM_CUSTOM_REGEX from the server (api/core/constants/_regex.py).
const DATA_REGEX = /^[0-9a-zA-Z_\-:+/=]+$/;

function b64ToBytes(b64) {
  return new Uint8Array(Buffer.from(b64, "base64"));
}
function bytesToPemSpki(buf) {
  const b64 = Buffer.from(buf).toString("base64");
  const lines = b64.match(/.{1,64}/g).join("\n");
  return `-----BEGIN PUBLIC KEY-----\n${lines}\n-----END PUBLIC KEY-----`;
}

async function decryptBlob(blob, privateKey) {
  const [encKeyB64, encIvB64, ctB64] = blob.split("::");
  const rawKey = await subtle.decrypt({ name: "RSA-OAEP" }, privateKey, b64ToBytes(encKeyB64));
  const iv = await subtle.decrypt({ name: "RSA-OAEP" }, privateKey, b64ToBytes(encIvB64));
  const aesKey = await subtle.importKey("raw", rawKey, { name: "AES-CBC" }, false, ["decrypt"]);
  const ptBuf = await subtle.decrypt(
    { name: "AES-CBC", iv: new Uint8Array(iv) }, aesKey, b64ToBytes(ctB64)
  );
  return {
    rawKey: new Uint8Array(rawKey),
    iv: new Uint8Array(iv),
    plaintext: Buffer.from(ptBuf).toString("utf-8")
  };
}

async function main() {
  // --- generate the session RSA keypair (RSA-OAEP / SHA-256), as the server does
  const keyPair = await subtle.generateKey(
    { name: "RSA-OAEP", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["encrypt", "decrypt"]
  );
  const spki = await subtle.exportKey("spki", keyPair.publicKey);
  const publicKeyPem = bytesToPemSpki(spki);

  // --- minimal DOM / element shim -----------------------------------------
  const statusEl = { textContent: "Checking…" };
  const doc = {
    getElementById(id) {
      return id === "status" ? statusEl : null;
    },
    createElement() {
      // Canvas-like: no real 2d/webgl context in the shim.
      return { width: 0, height: 0, getContext() { return null; }, toDataURL() { return ""; } };
    }
  };

  const store = {};
  const localStorage = {
    setItem(k, v) { store[k] = String(v); },
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; }
  };

  const fetchCalls = [];
  function fakeFetch(url, init) {
    fetchCalls.push({ url, init });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  }

  const win = {
    PUBLIC_KEY: publicKeyPem,
    TASK_CONFIG: { evalUrl: "/_eval", payloadKey: "data", autoSubmit: true },
    BV_SESSION: {
      sessionId: "sess-1",
      nonce: "nonce-abc123",
      publicKeyId: "pk-deadbeef",
      configHash: "cfg-1234",
      schemaVersion: "bv-runtime-1"
    },
    crypto: webcrypto,
    btoa: (s) => Buffer.from(s, "binary").toString("base64"),
    atob: (s) => Buffer.from(s, "base64").toString("binary"),
    fetch: fakeFetch,
    localStorage,
    location: { href: "https://challenge/_web", origin: "https://challenge" },
    innerWidth: 1440,
    innerHeight: 812,
    outerWidth: 1440,
    outerHeight: 900,
    devicePixelRatio: 1
    // AudioContext / RTCPeerConnection / WebAssembly / indexedDB intentionally absent.
  };

  global.window = win;
  global.document = doc;
  Object.defineProperty(global, "navigator", {
    configurable: true,
    value: {
      userAgent:
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      platform: "Linux x86_64",
      vendor: "Google Inc.",
      language: "en-US",
      languages: ["en-US", "en"],
      webdriver: false,
      hardwareConcurrency: 8,
      maxTouchPoints: 0,
      plugins: { length: 3 },
      mimeTypes: { length: 2 },
      userAgentData: { mobile: false, platform: "Linux", brands: [{ brand: "Chromium" }] }
    }
  });
  global.screen = {
    width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24, pixelDepth: 24
  };
  Object.defineProperty(global, "performance", {
    configurable: true,
    value: { getEntriesByType: (k) => (k === "navigation" ? [{ duration: 800 }] : []) }
  });

  // Wire the collector factory, then run the SDK IIFE against the shim.
  win.BVCollector = require(path.join(JS_DIR, "collector.js"));
  require(path.join(JS_DIR, "sdk.js"));

  // The IIFE auto-runs on load; await an explicit run for a deterministic result.
  const submitted = await win.BV_SDK.submitNow();

  // Do all async crypto up front so the assertions below can stay synchronous.
  const lastBody = JSON.parse(fetchCalls[fetchCalls.length - 1].init.body);
  const blob = lastBody.error.data;
  const decoded = await decryptBlob(blob, keyPair.privateKey);
  const payload = JSON.parse(decoded.plaintext);

  let passed = 0;
  function test(name, fn) {
    fn();
    passed += 1;
    console.log("  ok - " + name);
  }

  test("auto-submit resolves true and flips window.BV_SUBMITTED", () => {
    assert.strictEqual(submitted, true);
    assert.strictEqual(win.BV_SUBMITTED, true);
  });

  test("updates #status to 'Verification complete'", () => {
    assert.strictEqual(statusEl.textContent, "Verification complete");
  });

  test("POSTs to TASK_CONFIG.evalUrl with the {error:{data}} envelope", () => {
    const call = fetchCalls[fetchCalls.length - 1];
    assert.strictEqual(call.url, "/_eval");
    assert.strictEqual(call.init.method, "POST");
    assert.ok(typeof blob === "string" && blob.length > 0, "blob must be a string");
    assert.ok(DATA_REGEX.test(blob), "blob must match the server data charset");
  });

  test("blob is the hybrid key::iv::text wire format (3 parts)", () => {
    assert.strictEqual(blob.split("::").length, 3, "expected encKey::encIv::cipherText");
  });

  test("blob round-trips: RSA-OAEP(key,iv) + AES-256-CBC(text) -> original payload", () => {
    assert.strictEqual(decoded.rawKey.length, 32, "AES-256 key");
    assert.strictEqual(decoded.iv.length, 16, "16-byte IV");
    // Must equal what the SDK stashed locally too.
    assert.strictEqual(decoded.plaintext, localStorage.getItem("data"));
  });

  test("collected payload carries the non-behavioral integrity sections", () => {
    assert.ok(payload.browserInfo, "browserInfo present");
    assert.strictEqual(payload.browserInfo.webdriver, false);
    assert.ok(payload.browserInfo.userAgent.indexOf("Chrome") !== -1);
    for (const k of [
      "automation", "runtimeIntegrity", "apiAvailability", "fingerprint",
      "navigator", "display", "correlation", "sessionBinding"
    ]) {
      assert.ok(payload[k] && typeof payload[k] === "object", k + " section present");
    }
  });

  test("automation indicators reflect a clean (non-automated) shim", () => {
    assert.strictEqual(payload.automation.webdriver, false);
    assert.strictEqual(payload.automation.headlesschrome, false);
  });

  test("apiAvailability reports real capability booleans", () => {
    assert.strictEqual(payload.apiAvailability.serviceWorker, false); // absent in shim navigator
    assert.strictEqual(typeof payload.apiAvailability.indexedDb, "boolean");
  });

  test("sessionBinding echoes the injected BV_SESSION fields", () => {
    assert.strictEqual(payload.sessionBinding.nonce, "nonce-abc123");
    assert.strictEqual(payload.sessionBinding.sessionId, "sess-1");
    assert.strictEqual(payload.sessionBinding.publicKeyId, "pk-deadbeef");
    assert.strictEqual(payload.sessionBinding.schemaVersion, "bv-runtime-1");
  });

  test("legacy behavior series are present but EMPTY (no behavior collected)", () => {
    for (const k of ["movements", "clicks", "mouseDowns", "mouseUps", "keydowns", "keyups", "scroll"]) {
      assert.deepStrictEqual(payload[k], [], k + " must be empty");
    }
  });

  test("NO behavioral fields leak into the payload", () => {
    const banned = ["eventSequence", "targets", "taskProgress", "trustedEventStats", "buttonHoverToClickTime"];
    const raw = localStorage.getItem("data");
    for (const k of banned) {
      assert.ok(raw.indexOf('"' + k + '"') === -1, "payload must not contain " + k);
    }
  });

  test("submission required NO behavioral input (no events were dispatched)", () => {
    // We never dispatched a mouse/key/scroll event; the SDK submitted anyway.
    assert.strictEqual(win.BV_SUBMITTED, true);
  });

  console.log("\nsdk.test.js: " + passed + " passed");
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exitCode = 1;
});
