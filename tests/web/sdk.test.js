"use strict";

/**
 * Integration test for the browser SDK against a minimal hand-rolled DOM +
 * localStorage shim (no jsdom/playwright needed). Proves the core requirements:
 *   - after interaction the SDK writes a well-formed payload to localStorage,
 *   - the advanced raw signals (browserInfo, eventSequence, targets,
 *     trustedEventStats, taskProgress, pageTimings) are collected,
 *   - NO typed characters / credential values are ever captured.
 *
 *   node tests/web/sdk.test.js
 */

const assert = require("assert");
const path = require("path");

const JS_DIR = path.join(
  __dirname,
  "..",
  "..",
  "src",
  "bv_challenge",
  "challenge",
  "templates",
  "html",
  "static",
  "js"
);

// --- minimal event-target / DOM shim --------------------------------------
function makeTarget(extra) {
  const handlers = {};
  return Object.assign(
    {
      addEventListener(type, fn) {
        (handlers[type] = handlers[type] || []).push(fn);
      },
      dispatch(type, evt) {
        (handlers[type] || []).forEach((fn) => fn(evt || {}));
      },
      getBoundingClientRect() {
        return { left: 100, top: 400, width: 200, height: 40 };
      }
    },
    extra || {}
  );
}

const usernameEl = makeTarget({ id: "username", name: "username" });
const passwordEl = makeTarget({ id: "password", name: "password" });
const loginBtn = makeTarget({ id: "login-button" });
const endBtn = makeTarget({ className: "end-session" });

const doc = makeTarget({
  getElementById(id) {
    return id === "login-button" ? loginBtn : null;
  },
  querySelector(sel) {
    if (sel === ".end-session") return endBtn;
    if (sel === 'input[name="username"]') return usernameEl;
    if (sel === 'input[name="password"]') return passwordEl;
    return null; // "#login-button" falls back to getElementById in the SDK
  }
});

const store = {};
const localStorage = {
  setItem(k, v) {
    store[k] = String(v);
  },
  getItem(k) {
    return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
  }
};

const win = makeTarget({
  APP_ID: "app-123",
  TASK_CONFIG: {
    usernameInput: 'input[name="username"]',
    passwordInput: 'input[name="password"]',
    verifyButton: "#login-button",
    scrollContainer: "#content",
    endSessionButton: ".end-session",
    payloadKey: "data"
  },
  ACTIONS_LIST: JSON.stringify([
    [
      { type: "click", args: { location: { x: 10, y: 20 } } },
      { type: "input", selector: { name: "username", id: "u_dynamic" }, args: { text: "x" } },
      { type: "input", selector: { name: "password", id: "p_dynamic" }, args: { text: "y" } }
    ]
  ]),
  localStorage: localStorage,
  scrollY: 0,
  innerWidth: 1280,
  innerHeight: 800,
  devicePixelRatio: 2
});

// Environment globals the SDK snapshots into browserInfo. `navigator` and
// `performance` are read-only globals in modern Node, so define over them.
let perfClock = 0;
Object.defineProperty(global, "navigator", {
  configurable: true,
  value: {
    userAgent: "node-test-agent",
    platform: "linux",
    language: "en-US",
    webdriver: false,
    hardwareConcurrency: 8,
    plugins: { length: 3 },
    maxTouchPoints: 0
  }
});
Object.defineProperty(global, "performance", {
  configurable: true,
  value: {
    now() {
      perfClock += 1;
      return perfClock;
    },
    getEntriesByType(kind) {
      return kind === "navigation" ? [{ duration: 800 }] : [];
    }
  }
});
global.screen = { width: 1920, height: 1080 };

// Wire the collector factory onto the fake window, then expose globals so the
// classic-script SDK (which references bare `window`/`document`) can run.
win.BVCollector = require(path.join(JS_DIR, "collector.js"));
global.window = win;
global.document = doc;

// Executing the module runs the SDK IIFE against our shim.
require(path.join(JS_DIR, "sdk.js"));

function read() {
  const raw = localStorage.getItem("data");
  assert.ok(raw, 'localStorage["data"] must exist');
  return JSON.parse(raw);
}

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log("  ok - " + name);
}

test('writes localStorage["data"] on load, before any interaction', () => {
  const d = read();
  assert.strictEqual(d.appId, "app-123");
  for (const k of ["movements", "clicks", "keydowns", "keyups", "scroll", "eventSequence", "targets"]) {
    assert.deepStrictEqual(d[k], []);
  }
});

test("snapshots browserInfo from navigator/screen/window on load", () => {
  const b = read().browserInfo;
  assert.ok(b, "browserInfo should be populated");
  assert.strictEqual(b.userAgent, "node-test-agent");
  assert.strictEqual(b.webdriver, false);
  assert.strictEqual(b.hardwareConcurrency, 8);
  assert.strictEqual(b.pluginsLength, 3);
  assert.deepStrictEqual(b.viewport, { width: 1280, height: 800 });
  assert.strictEqual(b.devicePixelRatio, 2);
});

test("captures pageLoadMs from the navigation timing API", () => {
  assert.strictEqual(read().pageTimings.pageLoadMs, 800);
});

test("assigns per-session input ids from ACTIONS_LIST", () => {
  assert.strictEqual(usernameEl.id, "u_dynamic");
  assert.strictEqual(passwordEl.id, "p_dynamic");
});

test("records behavior events into localStorage after interaction", () => {
  doc.dispatch("mousemove", { clientX: 5, clientY: 6, isTrusted: true });
  doc.dispatch("mousedown", { clientX: 5, clientY: 6, isTrusted: true });
  doc.dispatch("mouseup", { clientX: 5, clientY: 6, isTrusted: true });
  doc.dispatch("pointerdown", { clientX: 5, clientY: 6, isTrusted: true, target: loginBtn });
  doc.dispatch("pointerup", { clientX: 5, clientY: 6, isTrusted: true });
  doc.dispatch("click", { clientX: 205, clientY: 419, isTrusted: true, target: loginBtn });
  doc.dispatch("keydown", { isTrusted: true, key: "a" });
  doc.dispatch("keyup", { isTrusted: true, key: "a" });
  win.dispatch("scroll", { isTrusted: true });

  const d = read();
  assert.strictEqual(d.movements.length, 1);
  assert.strictEqual(d.mouseDowns.length, 1);
  assert.strictEqual(d.mouseUps.length, 1);
  assert.strictEqual(d.clicks.length, 1);
  assert.strictEqual(d.keydowns.length, 1);
  assert.strictEqual(d.keyups.length, 1);
  assert.strictEqual(d.scroll.length, 1);
});

test("builds an ordered eventSequence with timing + isTrusted", () => {
  const d = read();
  assert.ok(d.eventSequence.length >= 5, "sequence should contain the dispatched events");
  const types = d.eventSequence.map((e) => e.type);
  for (const t of ["pointerdown", "pointerup", "click", "keydown", "keyup", "scroll"]) {
    assert.ok(types.includes(t), "eventSequence should include " + t);
  }
  const click = d.eventSequence.find((e) => e.type === "click");
  assert.strictEqual(click.trusted, true);
  assert.strictEqual(typeof click.t, "number");
  assert.strictEqual(typeof click.pt, "number");
});

test("tallies isTrusted counts per event type", () => {
  doc.dispatch("click", { clientX: 1, clientY: 2, isTrusted: false, target: loginBtn });
  const stats = read().trustedEventStats.click;
  assert.ok(stats.trusted >= 1 && stats.untrusted >= 1, "both trusted and synthetic clicks tallied");
});

test("captures click target geometry (id/class/rect) — never a value", () => {
  const t = read().targets.find((x) => x.id === "login-button");
  assert.ok(t, "a target for the login button should be recorded");
  assert.deepStrictEqual(t.rect, { x: 100, y: 400, w: 200, h: 40 });
  assert.ok(!("value" in t), "target must not carry a value");
});

test("eventSequence key events never carry the typed character", () => {
  const d = read();
  for (const e of d.eventSequence) {
    if (e.type === "keydown" || e.type === "keyup") {
      assert.deepStrictEqual(Object.keys(e).sort(), ["pt", "t", "trusted", "type"]);
    }
  }
});

test("does not persist typed characters for key events", () => {
  const d = read();
  assert.deepStrictEqual(Object.keys(d.keydowns[0]), ["t"]);
});

test("focus + type sets username task-progress flags (intent only)", () => {
  usernameEl.dispatch("focus", { isTrusted: true });
  doc.dispatch("keydown", { isTrusted: true, key: "h" });
  usernameEl.dispatch("blur", { isTrusted: true });
  const p = read().taskProgress;
  assert.strictEqual(p.usernameFocused, true);
  assert.strictEqual(p.usernameTyped, true);
});

test("focus + type sets password task-progress flags", () => {
  passwordEl.dispatch("focus", { isTrusted: true });
  doc.dispatch("keydown", { isTrusted: true, key: "s" });
  passwordEl.dispatch("blur", { isTrusted: true });
  const p = read().taskProgress;
  assert.strictEqual(p.passwordFocused, true);
  assert.strictEqual(p.passwordTyped, true);
});

test("scroll / verify / end set their intent flags", () => {
  win.dispatch("scroll", { isTrusted: true });
  loginBtn.dispatch("mouseenter", {});
  loginBtn.dispatch("click", { isTrusted: true });
  const p1 = read().taskProgress;
  assert.strictEqual(p1.contentScrolled, true);
  assert.strictEqual(p1.verifyHovered, true);
  assert.strictEqual(p1.verifyClicked, true);
});

test("captures buttonHoverToClickTime on hover-then-click", () => {
  const d = read();
  assert.strictEqual(typeof d.buttonHoverToClickTime, "number");
  assert.ok(d.buttonHoverToClickTime >= 0);
});

test("finalizes (endedAt set) and flags endClicked on end-session click", () => {
  assert.strictEqual(read().endedAt, null);
  endBtn.dispatch("click", { isTrusted: true });
  const d = read();
  assert.strictEqual(typeof d.endedAt, "number");
  assert.strictEqual(d.taskProgress.endClicked, true);
});

test("CREDENTIAL SAFETY: no typed value appears anywhere in the payload", () => {
  // We dispatched key events carrying key: "h"/"s"/"a"; none may be persisted.
  const raw = localStorage.getItem("data");
  const parsed = JSON.parse(raw);
  // No event/series entry may carry a `key`, `value`, `text`, or `char` field.
  const banned = ["key", "value", "text", "char", "code"];
  (function walk(node) {
    if (Array.isArray(node)) {
      node.forEach(walk);
    } else if (node && typeof node === "object") {
      for (const k of Object.keys(node)) {
        assert.ok(banned.indexOf(k) === -1, "payload must not contain field: " + k);
        walk(node[k]);
      }
    }
  })(parsed);
});

console.log("\nsdk.test.js: " + passed + " passed");
