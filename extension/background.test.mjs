import test from "node:test";
import assert from "node:assert/strict";

const listeners = {};
let storedSettings = {};
const storageWrites = [];
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
  permissions: {
    // 方法说明：允许后台测试访问漫画图片来源。
    async contains() { return true; },
  },
  storage: {
    local: {
      // 方法说明：读取测试存储中的指定设置。
      async get() { return storedSettings; },
      // 方法说明：写入测试存储并更新模拟状态。
      async set(value) { storageWrites.push(value); },
    },
  },
};

const { isSupportedPage, processPage } = await import("./background.js");

// 方法说明：验证拷贝漫画章节地址会被识别为受支持页面。
test("recognizes supported CopyManga chapter URLs", () => {
  assert.equal(
    isSupportedPage(
      "https://www.mangacopy.com/comic/work/chapter/123",
    ),
    true,
  );
  assert.equal(
    isSupportedPage("https://www.copy3000.com/comic/work/chapter/456"),
    true,
  );
  assert.equal(isSupportedPage("https://www.copymanga.com/comic/work"), false);
  assert.equal(
    isSupportedPage("https://www.mangacopy.com/rank?type=male&table=month"),
    false,
  );
  assert.equal(isSupportedPage("https://www.copy3000.com/"), false);
  assert.equal(isSupportedPage("https://example.com/comic/work"), false);
  assert.equal(isSupportedPage("not a URL"), false);
});

// 方法说明：验证标签页加载完成后会触发内容脚本重新注入。
test("registers completed-navigation reinjection", () => {
  assert.equal(typeof listeners.updated, "function");
  assert.equal(typeof listeners.installed, "function");
  assert.equal(typeof listeners.startup, "function");
});

// 方法说明：验证预生成请求只写入服务缓存且不下载结果图片。
test("prefetch processing skips result image download", async () => {
  storedSettings = {
    enabled: true,
    deployment: "local",
    localApiBaseUrl: "http://127.0.0.1:8765",
    localApiToken: "test-token",
    localMode: "quality",
  };
  storageWrites.length = 0;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).startsWith("https://img.example/")) {
      return new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { "content-type": "image/webp" },
      });
    }
    if (String(url) === "http://127.0.0.1:8765/v1/pregeneration/cache/resolve") {
      return new Response("not found", { status: 404 });
    }
    if (String(url) === "http://127.0.0.1:8765/v1/pregeneration/source/resolve") {
      return new Response("not found", { status: 404 });
    }
    if (String(url) === "http://127.0.0.1:8765/v1/pregeneration/pages") {
      return new Response(
        JSON.stringify({
          result_url: "/v1/results/cached.webp",
          model_profile: "sd15-colorize",
          cached: false,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    throw new Error(`不应下载预生成结果：${url}`);
  };

  const result = await processPage({
    imageUrl: "https://img.example/page-1.webp",
    work: { source: "copy_manga", source_work_id: "work" },
    chapter: { chapter_id: "next" },
    options: { page_index: 0, palette_version: "default" },
    prefetchOnly: true,
  });

  assert.equal(result.result_url, "/v1/results/cached.webp");
  assert.deepEqual(
    requests.map((request) => request.url),
    [
      "http://127.0.0.1:8765/v1/pregeneration/cache/resolve",
      "http://127.0.0.1:8765/v1/pregeneration/source/resolve",
      "https://img.example/page-1.webp",
      "http://127.0.0.1:8765/v1/pregeneration/pages",
    ],
  );
  assert.equal(storageWrites.length, 0);
});

// 方法说明：验证章节缓存命中时同时使用本地原图且不访问漫画图片域名。
test("uses completed chapter and source caches without remote image access", async () => {
  storedSettings = {
    enabled: true,
    deployment: "local",
    localApiBaseUrl: "http://127.0.0.1:8765",
    localApiToken: "test-token",
    localMode: "quality",
  };
  storageWrites.length = 0;
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    if (String(url).endsWith("/v1/pregeneration/cache/resolve")) {
      return new Response(
        JSON.stringify({
          result_url: "/v1/results/chapter-cache.webp",
          model_profile: "passthrough",
          cached: true,
          reference_applied: false,
          processed_panels: 0,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    if (String(url).endsWith("/v1/pregeneration/source/resolve")) {
      return new Response(
        JSON.stringify({ source_url: "/v1/pregeneration/source/source-id" }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    if (String(url).endsWith("/v1/pregeneration/source/source-id")) {
      return new Response(new Uint8Array([9, 8, 7]), {
        status: 200,
        headers: { "content-type": "image/webp" },
      });
    }
    if (String(url).endsWith("/v1/results/chapter-cache.webp")) {
      return new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { "content-type": "image/webp" },
      });
    }
    throw new Error(`不应访问未命中路径：${url}`);
  };

  const result = await processPage({
    imageUrl: "https://img.example/page-1.webp",
    work: { source: "copy_manga", source_work_id: "work" },
    chapter: { chapter_id: "chapter-1" },
    options: { page_index: 0, palette_version: "default" },
    prefetchOnly: false,
  });

  assert.equal(result.cached, true);
  assert.match(result.source_image_data_url, /^data:image\/webp;base64,/);
  assert.deepEqual(requests, [
    "http://127.0.0.1:8765/v1/pregeneration/cache/resolve",
    "http://127.0.0.1:8765/v1/pregeneration/source/resolve",
    "http://127.0.0.1:8765/v1/pregeneration/source/source-id",
    "http://127.0.0.1:8765/v1/results/chapter-cache.webp",
  ]);
  assert.equal(storageWrites.length, 1);
});

// 方法说明：验证增强缓存失效时会用本地原图重新推理而不访问图片 CDN。
test("reprocesses a local source cache when the result cache misses", async () => {
  storedSettings = {
    enabled: true,
    deployment: "local",
    localApiBaseUrl: "http://127.0.0.1:8765",
    localApiToken: "test-token",
    localMode: "quality",
  };
  storageWrites.length = 0;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    const value = String(url);
    requests.push(value);
    if (value.endsWith("/v1/pregeneration/cache/resolve")) {
      return new Response("not found", { status: 404 });
    }
    if (value.endsWith("/v1/pregeneration/source/resolve")) {
      return new Response(
        JSON.stringify({ source_url: "/v1/pregeneration/source/source-id" }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    if (value.endsWith("/v1/pregeneration/source/source-id")) {
      return new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { "content-type": "image/webp" },
      });
    }
    if (value.endsWith("/v1/pages/process")) {
      assert.equal(options.body.get("chapter_json"), '{"chapter_id":"chapter-1"}');
      return new Response(
        JSON.stringify({
          result_url: "/v1/results/reprocessed.webp",
          model_profile: "passthrough",
          cached: false,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    if (value.endsWith("/v1/results/reprocessed.webp")) {
      return new Response(new Uint8Array([4, 5, 6]), {
        status: 200,
        headers: { "content-type": "image/webp" },
      });
    }
    throw new Error(`不应访问漫画图片域名：${url}`);
  };

  const result = await processPage({
    imageUrl: "https://img.example/page-1.webp",
    work: { source: "copy_manga", source_work_id: "work" },
    chapter: { chapter_id: "chapter-1" },
    options: { page_index: 0, palette_version: "default" },
    prefetchOnly: false,
  });

  assert.equal(result.cached, false);
  assert.match(result.source_image_data_url, /^data:image\/webp;base64,/);
  assert.equal(requests.includes("https://img.example/page-1.webp"), false);
});
