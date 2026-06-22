"use strict";

/**
 * Unit tests for the pure behavior-metrics collector. No DOM, no deps —
 * runs under plain `node tests/web/collector.test.js`.
 */

const assert = require("assert");
const path = require("path");

const { createCollector } = require(path.join(
  __dirname,
  "..",
  "..",
  "src",
  "bv_challenge",
  "challenge",
  "templates",
  "html",
  "static",
  "js",
  "collector.js"
));

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log("  ok - " + name);
}

test("starts with all metric series present and empty", () => {
  const c = createCollector({ appId: "abc" });
  const d = c.toJSON();
  for (const k of [
    "movements",
    "clicks",
    "mouseDowns",
    "mouseUps",
    "keydowns",
    "keyups",
    "scroll"
  ]) {
    assert.deepStrictEqual(d[k], [], k + " should start empty");
  }
  assert.strictEqual(d.appId, "abc");
  assert.strictEqual(d.buttonHoverToClickTime, null);
});

test("records positional samples for movement-like series", () => {
  const c = createCollector();
  c.record("movements", { x: 10, y: 20, t: 5 });
  c.record("clicks", { x: 1, y: 2, t: 6 });
  assert.deepStrictEqual(c.toJSON().movements[0], { x: 10, y: 20, t: 5 });
  assert.deepStrictEqual(c.toJSON().clicks[0], { x: 1, y: 2, t: 6 });
});

test("key events store ONLY timing, never characters", () => {
  const c = createCollector();
  c.record("keydowns", { t: 7, key: "s" }); // key must be dropped
  const entry = c.toJSON().keydowns[0];
  assert.deepStrictEqual(Object.keys(entry), ["t"]);
  assert.strictEqual(entry.t, 7);
});

test("buttonHoverToClickTime: first write wins, rejects negatives", () => {
  const c = createCollector();
  c.setButtonHoverToClickTime(-1);
  assert.strictEqual(c.toJSON().buttonHoverToClickTime, null);
  c.setButtonHoverToClickTime(123);
  c.setButtonHoverToClickTime(999);
  assert.strictEqual(c.toJSON().buttonHoverToClickTime, 123);
});

test("series are capped at maxSamples", () => {
  const c = createCollector({ maxSamples: 3 });
  for (let i = 0; i < 10; i++) c.record("movements", { x: i, y: i, t: i });
  assert.strictEqual(c.toJSON().movements.length, 3);
});

test("finalize sets endedAt once and is idempotent", () => {
  let n = 0;
  const c = createCollector({ now: () => ++n });
  const first = c.finalize().endedAt;
  const second = c.finalize().endedAt;
  assert.strictEqual(first, second);
});

test("unknown event type is ignored", () => {
  const c = createCollector();
  assert.strictEqual(c.record("bogus", { x: 1 }), false);
});

// --- advanced raw-signal containers ---------------------------------------

test("starts with advanced raw-signal containers present and empty/default", () => {
  const d = createCollector().toJSON();
  assert.strictEqual(d.browserInfo, null);
  assert.deepStrictEqual(d.pageTimings, {
    pageLoadMs: null,
    firstInteractionMs: null,
    payloadWrittenMs: null
  });
  assert.deepStrictEqual(d.eventSequence, []);
  assert.deepStrictEqual(d.targets, []);
  assert.deepStrictEqual(d.trustedEventStats, {});
  assert.strictEqual(d.clock, null);
  for (const k of Object.keys(d.taskProgress)) {
    assert.strictEqual(d.taskProgress[k], false, k + " should default false");
  }
});

test("recordEvent appends to eventSequence and tallies isTrusted", () => {
  const c = createCollector();
  c.recordEvent("click", { t: 1, pt: 0.5, trusted: true });
  c.recordEvent("click", { t: 2, pt: 1.5, trusted: false });
  const d = c.toJSON();
  assert.strictEqual(d.eventSequence.length, 2);
  assert.deepStrictEqual(d.eventSequence[0], { type: "click", t: 1, pt: 0.5, trusted: true });
  assert.deepStrictEqual(d.trustedEventStats.click, { trusted: 1, untrusted: 1 });
});

test("recordEvent ignores unknown event types", () => {
  const c = createCollector();
  assert.strictEqual(c.recordEvent("bogus", { t: 1 }), false);
  assert.strictEqual(c.toJSON().eventSequence.length, 0);
});

test("eventSequence keydown carries NO typed character", () => {
  const c = createCollector();
  c.recordEvent("keydown", { t: 1, pt: 0, trusted: true, key: "s" });
  const entry = c.toJSON().eventSequence[0];
  assert.deepStrictEqual(Object.keys(entry).sort(), ["pt", "t", "trusted", "type"]);
});

test("recordTarget stores geometry only — never a value field", () => {
  const c = createCollector();
  c.recordTarget({
    kind: "click",
    id: "login-button",
    cls: "btn",
    rect: { x: 1, y: 2, w: 3, h: 4 },
    cx: 2,
    cy: 3,
    scrollY: 0,
    visible: true,
    value: "secret" // must be dropped
  });
  const t = c.toJSON().targets[0];
  assert.ok(!("value" in t), "target must not carry a value");
  assert.deepStrictEqual(t.rect, { x: 1, y: 2, w: 3, h: 4 });
  assert.strictEqual(t.id, "login-button");
  assert.strictEqual(t.visible, true);
});

test("setBrowserInfo / setClock are first-write-wins", () => {
  const c = createCollector();
  c.setBrowserInfo({ userAgent: "ua-1" });
  c.setBrowserInfo({ userAgent: "ua-2" });
  assert.strictEqual(c.toJSON().browserInfo.userAgent, "ua-1");
  c.setClock({ dateNow: 100, perfNow: 1 });
  c.setClock({ dateNow: 999, perfNow: 9 });
  assert.deepStrictEqual(c.toJSON().clock, { dateNow: 100, perfNow: 1 });
});

test("setPageTiming sets each field once", () => {
  const c = createCollector();
  c.setPageTiming("pageLoadMs", 800);
  c.setPageTiming("pageLoadMs", 999);
  assert.strictEqual(c.toJSON().pageTimings.pageLoadMs, 800);
});

test("setProgress flips known flags and ignores unknown ones", () => {
  const c = createCollector();
  c.setProgress("usernameFocused");
  c.setProgress("bogusFlag", true);
  const p = c.toJSON().taskProgress;
  assert.strictEqual(p.usernameFocused, true);
  assert.ok(!("bogusFlag" in p), "unknown flags must not be injected");
});

console.log("\ncollector.test.js: " + passed + " passed");
