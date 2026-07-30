"use strict";

/**
 * Unit tests for the pure browser/runtime-integrity collector. No DOM, no deps —
 * runs under plain `node tests/web/collector.test.js`.
 *
 * HBv6 is non-behavioral: the collector accumulates raw integrity sections
 * (browserInfo + automation / runtimeIntegrity / fingerprint / apiAvailability /
 * navigator / display / correlation / sessionBinding) and keeps the legacy raw
 * behavior series only as EMPTY lists for shape back-compat.
 */

const assert = require("assert");
const path = require("path");

const { createCollector, SECTION_KEYS } = require(path.join(
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

const LEGACY = ["movements", "clicks", "mouseDowns", "mouseUps", "keydowns", "keyups", "scroll"];
const SECTIONS = [
  "automation",
  "runtimeIntegrity",
  "fingerprint",
  "apiAvailability",
  "navigator",
  "display",
  "correlation",
  "sessionBinding"
];

test("starts with legacy behavior series present and EMPTY (shape back-compat)", () => {
  const d = createCollector({ sessionId: "s1" }).toJSON();
  for (const k of LEGACY) {
    assert.deepStrictEqual(d[k], [], k + " should start empty");
  }
});

test("starts with all integrity sections null and a pageTimings container", () => {
  const d = createCollector().toJSON();
  assert.strictEqual(d.browserInfo, null);
  for (const k of SECTIONS) {
    assert.strictEqual(d[k], null, k + " should start null");
  }
  assert.deepStrictEqual(d.pageTimings, { pageLoadMs: null });
});

test("carries sessionId and schemaVersion through to the payload", () => {
  const d = createCollector({ sessionId: "abc", schemaVersion: "bv-runtime-1" }).toJSON();
  assert.strictEqual(d.sessionId, "abc");
  assert.strictEqual(d.schemaVersion, "bv-runtime-1");
});

test("setBrowserInfo is first-write-wins", () => {
  const c = createCollector();
  c.setBrowserInfo({ userAgent: "ua-1" });
  c.setBrowserInfo({ userAgent: "ua-2" });
  assert.strictEqual(c.toJSON().browserInfo.userAgent, "ua-1");
});

test("setSection stores a known section once (first-write-wins)", () => {
  const c = createCollector();
  c.setSection("automation", { webdriver: false });
  c.setSection("automation", { webdriver: true });
  assert.deepStrictEqual(c.toJSON().automation, { webdriver: false });
});

test("setSection accepts every advertised section key", () => {
  const c = createCollector();
  for (const k of SECTIONS) {
    c.setSection(k, { ok: true });
  }
  const d = c.toJSON();
  for (const k of SECTIONS) {
    assert.deepStrictEqual(d[k], { ok: true }, k);
  }
});

test("setSection ignores unknown section names", () => {
  const c = createCollector();
  c.setSection("bogusSection", { x: 1 });
  assert.ok(!("bogusSection" in c.toJSON()), "unknown sections must not be injected");
});

test("setSection ignores non-object values", () => {
  const c = createCollector();
  c.setSection("automation", "nope");
  c.setSection("automation", 5);
  assert.strictEqual(c.toJSON().automation, null);
});

test("setPageTiming sets pageLoadMs once", () => {
  const c = createCollector();
  c.setPageTiming("pageLoadMs", 800);
  c.setPageTiming("pageLoadMs", 999);
  assert.strictEqual(c.toJSON().pageTimings.pageLoadMs, 800);
});

test("finalize sets endedAt once and is idempotent", () => {
  let n = 0;
  const c = createCollector({ now: () => ++n });
  const first = c.finalize().endedAt;
  const second = c.finalize().endedAt;
  assert.strictEqual(first, second);
});

test("SECTION_KEYS exposes exactly the known sections", () => {
  assert.deepStrictEqual(Object.keys(SECTION_KEYS).sort(), SECTIONS.slice().sort());
});

console.log("\ncollector.test.js: " + passed + " passed");
