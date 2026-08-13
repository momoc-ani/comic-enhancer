import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("popup renders the installed manifest version", () => {
  const manifest = JSON.parse(
    readFileSync(new URL("./manifest.json", import.meta.url), "utf8"),
  );
  const html = readFileSync(new URL("./popup.html", import.meta.url), "utf8");
  const script = readFileSync(new URL("./popup.js", import.meta.url), "utf8");

  assert.equal(manifest.version, "0.4.4");
  assert.match(html, /id="extensionVersion"/);
  assert.match(script, /chrome\.runtime\.getManifest\(\)\.version/);
});
