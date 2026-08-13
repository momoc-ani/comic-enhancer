import test from "node:test";
import assert from "node:assert/strict";

const listeners = {};
globalThis.chrome = {
  runtime: {
    onInstalled: { addListener(listener) { listeners.installed = listener; } },
    onStartup: { addListener(listener) { listeners.startup = listener; } },
    onMessage: { addListener(listener) { listeners.message = listener; } },
  },
  tabs: {
    onUpdated: { addListener(listener) { listeners.updated = listener; } },
    async query() { return []; },
  },
  scripting: {
    async insertCSS() {},
    async executeScript() { return []; },
  },
  storage: {
    local: {
      async get() { return {}; },
      async set() {},
    },
  },
};

const { isSupportedPage } = await import("./background.js");

test("recognizes supported CopyManga chapter URLs", () => {
  assert.equal(
    isSupportedPage(
      "https://www.mangacopy.com/comic/work/chapter/123",
    ),
    true,
  );
  assert.equal(isSupportedPage("https://www.copymanga.com/comic/work"), true);
  assert.equal(isSupportedPage("https://example.com/comic/work"), false);
  assert.equal(isSupportedPage("not a URL"), false);
});

test("registers completed-navigation reinjection", () => {
  assert.equal(typeof listeners.updated, "function");
  assert.equal(typeof listeners.installed, "function");
  assert.equal(typeof listeners.startup, "function");
});
