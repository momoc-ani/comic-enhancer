import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("popup renders the installed manifest version", () => {
  const manifest = JSON.parse(
    readFileSync(new URL("./manifest.json", import.meta.url), "utf8"),
  );
  const html = readFileSync(new URL("./popup.html", import.meta.url), "utf8");
  const script = readFileSync(new URL("./popup.js", import.meta.url), "utf8");

  assert.equal(manifest.version, "0.6.0");
  assert.match(html, /id="extensionVersion"/);
  assert.match(html, /data-deployment="remote"/);
  assert.match(html, /data-deployment="local"/);
  assert.match(html, /value="cobra"/);
  assert.match(html, /value="flux2"/);
  assert.match(script, /chrome\.runtime\.getManifest\(\)\.version/);
  assert.match(script, /capabilities\.processing_modes/);
});
