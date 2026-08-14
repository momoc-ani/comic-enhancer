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
    // 方法说明：返回测试使用的浏览器标签页列表。
    async query() { return []; },
    // 方法说明：模拟发送扩展运行时消息。
    async sendMessage() {},
  },
  scripting: {
    // 方法说明：模拟向标签页注入扩展样式。
    async insertCSS() {},
    // 方法说明：模拟向标签页执行扩展脚本。
    async executeScript() { return []; },
  },
  storage: {
    local: {
      // 方法说明：读取测试存储中的指定设置。
      async get() { return {}; },
      // 方法说明：写入测试存储并更新模拟状态。
      async set() {},
    },
  },
};

const { isSupportedPage } = await import("./background.js");

// 方法说明：验证拷贝漫画章节地址会被识别为受支持页面。
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

// 方法说明：验证标签页加载完成后会触发内容脚本重新注入。
test("registers completed-navigation reinjection", () => {
  assert.equal(typeof listeners.updated, "function");
  assert.equal(typeof listeners.installed, "function");
  assert.equal(typeof listeners.startup, "function");
});
