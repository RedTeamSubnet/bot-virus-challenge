"use strict";

// Hash payloads exactly like the browser SDK does. The canonicalize and
// payloadForHash functions are loaded from the real sdk.js source, so this
// breaks if the SDK serialization changes.
// Usage: node sdk_payload_hash.js <path/to/sdk.js>  (JSON array on stdin)

const fs = require("fs");
const crypto = require("crypto");

const src = fs.readFileSync(process.argv[2], "utf8");

function extract(name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error("not found in sdk.js: " + name);
  let depth = 0;
  for (let i = src.indexOf("{", start); i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error("unbalanced: " + name);
}

const payloadForHash = new Function(
  extract("canonicalize") + "\n" + extract("payloadForHash") + "\nreturn payloadForHash;"
)();

const payloads = JSON.parse(fs.readFileSync(0, "utf8"));
const hashes = payloads.map((p) =>
  crypto
    .createHash("sha256")
    .update(Buffer.from(new TextEncoder().encode(JSON.stringify(payloadForHash(p)))))
    .digest("hex")
);
process.stdout.write(JSON.stringify(hashes));
