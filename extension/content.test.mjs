import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

await import("./copy-manga.js");

class ClassList {
  // 方法说明：初始化当前对象及其运行状态。
  constructor(values = []) {
    this.values = new Set(values);
  }

  // 方法说明：判断测试类名集合是否包含指定值。
  contains(value) {
    return this.values.has(value);
  }

  // 方法说明：从测试类名集合中移除指定值。
  remove(...values) {
    values.forEach((value) => this.values.delete(value));
  }
}

class FakeWrapper {
  // 方法说明：初始化当前对象及其运行状态。
  constructor() {
    this.classList = new ClassList(["comic-enhancer-page"]);
    this.dataset = {};
    this.title = "";
    this.children = [];
    this.style = {};
  }

  // 方法说明：向测试元素追加子节点。
  append(child) {
    this.children.push(child);
    child.parentElement = this;
  }

  // 方法说明：在测试元素中查找匹配的子节点。
  querySelector(selector) {
    if (selector.includes("comic-enhancer-result")) {
      return this.children.find((child) => child.className === "comic-enhancer-result") || null;
    }
    return this.children.find((child) => child !== this.result) || null;
  }
}

class FakeImage {
  // 方法说明：初始化当前对象及其运行状态。
  constructor(url = "https://img.example/page-1.webp", bottom = 100) {
    this.dataset = { src: url };
    this.currentSrc = "https://img.example/loading.jpg";
    this.src = this.currentSrc;
    this.bottom = bottom;
    this.naturalWidth = 1124;
    this.naturalHeight = 1600;
    this.complete = true;
    this.classList = new ClassList(["lazyload"]);
    this.className = "lazyload";
    this.parentElement = null;
    this.alt = "";
  }

  // 方法说明：读取测试图片的指定属性。
  getAttribute(name) {
    if (name === "width" || name === "height" || name === "data-lazy-src") return null;
    return null;
  }

  // 方法说明：移除测试图片的指定属性。
  removeAttribute(name) {
    if (name === "data-src") delete this.dataset.src;
  }

  // 方法说明：返回测试图片的布局尺寸。
  getBoundingClientRect() {
    return { width: 1124, height: 1600, bottom: this.bottom };
  }

  // 方法说明：在测试图片前插入包装节点。
  before(wrapper) {
    wrapper.append(this);
  }

  // 方法说明：注册并模拟测试图片事件。
  addEventListener(type, listener) {
    if (type === "load") queueMicrotask(listener);
  }
}

// 方法说明：验证结果覆盖层尺寸不会被通用图片样式破坏。
test("result overlay sizing overrides generic page image sizing", () => {
  const css = readFileSync(new URL("./content.css", import.meta.url), "utf8");

  assert.match(css, /\.comic-enhancer-page\s*>\s*\.comic-enhancer-result\s*\{/);
  assert.match(css, /display:\s*grid/);
  assert.match(css, /position:\s*absolute/);
  assert.match(css, /inset:\s*0/);
  assert.match(css, /visibility:\s*hidden/);
  assert.match(css, /width:\s*100%\s*!important/);
  assert.match(css, /height:\s*100%\s*!important/);
  assert.match(css, /object-fit:\s*contain\s*!important/);
  assert.doesNotMatch(css, /object-fit:\s*fill/);
});

// 方法说明：验证设置变化后会使用真实懒加载地址重试失败页面。
test("retries failed pages with the real lazy-load URL after settings change", async () => {
  const image = new FakeImage();
  const runtimeListeners = [];
  const processUrls = [];
  let processAttempt = 0;
  let releaseSlowRequest;
  let markSlowRequestStarted;
  const slowRequestStarted = new Promise((resolve) => {
    markSlowRequestStarted = resolve;
  });
  globalThis.location = {
    hostname: "www.mangacopy.com",
    pathname: "/comic/work/chapter/one",
    href: "https://www.mangacopy.com/comic/work/chapter/one",
  };
  globalThis.document = {
    title: "测试漫画",
    documentElement: {},
    // 方法说明：在测试元素中查找匹配的子节点。
    querySelector() { return null; },
    // 方法说明：返回测试文档中的匹配节点集合。
    querySelectorAll(selector) {
      return selector.includes("img") ? [image] : [];
    },
    // 方法说明：创建指定标签的测试元素。
    createElement(tag) {
      return tag === "span" ? new FakeWrapper() : new FakeImage();
    },
  };
  globalThis.window = { addEventListener() {} };
  globalThis.IntersectionObserver = class {
    // 方法说明：初始化当前对象及其运行状态。
    constructor() {}
    // 方法说明：模拟浏览器观察器注册目标。
    observe() {}
  };
  globalThis.MutationObserver = class {
    // 方法说明：初始化当前对象及其运行状态。
    constructor() {}
    // 方法说明：模拟浏览器观察器注册目标。
    observe() {}
  };
  globalThis.chrome = {
    runtime: {
      onMessage: {
        // 方法说明：注册测试使用的运行时消息监听器。
        addListener(listener) { runtimeListeners.push(listener); },
      },
      // 方法说明：模拟发送扩展运行时消息。
      async sendMessage(message) {
        if (message.type === "COMIC_ENHANCER_SETTINGS") {
          return { enabled: true, mode: "fast", prefetchPages: 0 };
        }
        if (message.type === "COMIC_ENHANCER_PROCESS") {
          processUrls.push(message.payload.imageUrl);
          processAttempt += 1;
          if (processAttempt === 1) {
            return { ok: false, error: "推理服务失败：401" };
          }
          const success = {
            ok: true,
            result: {
              image_data_url: "data:image/webp;base64,AA==",
              reference_applied: false,
              processed_panels: 0,
              model_profile: "sd15-colorize",
            },
          };
          if (processAttempt === 3) {
            markSlowRequestStarted();
            return new Promise((resolve) => {
              releaseSlowRequest = () => resolve(success);
            });
          }
          return success;
        }
        return { ok: true };
      },
    },
  };

  await import(`./content.js?retry-test=${Date.now()}`);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(image.parentElement.dataset.state, "failed");
  assert.deepEqual(processUrls, ["https://img.example/page-1.webp"]);

  const refresh = runtimeListeners.find((listener) =>
    listener(
      { type: "IGNORED" },
      {},
      () => {},
    ) === false
  );
  refresh(
    {
      type: "COMIC_ENHANCER_REFRESH_SETTINGS",
      settings: { enabled: true, mode: "fast", prefetchPages: 0 },
    },
    {},
    () => {},
  );
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.deepEqual(processUrls, [
    "https://img.example/page-1.webp",
    "https://img.example/page-1.webp",
  ]);
  assert.equal(image.src, "https://img.example/page-1.webp");
  assert.equal(image.dataset.src, undefined);
  assert.equal(image.classList.contains("lazyload"), false);
  assert.equal(image.parentElement.dataset.state, "completed");
  assert.equal(image.parentElement.style.aspectRatio, "1124 / 1600");

  refresh(
    {
      type: "COMIC_ENHANCER_REFRESH_SETTINGS",
      settings: {
        enabled: true,
        apiBaseUrl: "http://enhancer.example",
        mode: "quality",
        prefetchPages: 0,
      },
    },
    {},
    () => {},
  );
  await slowRequestStarted;

  refresh(
    {
      type: "COMIC_ENHANCER_REFRESH_SETTINGS",
      settings: {
        enabled: true,
        apiBaseUrl: "http://enhancer.example",
        mode: "flux2",
        prefetchPages: 0,
      },
    },
    {},
    () => {},
  );
  releaseSlowRequest();
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.equal(processUrls.length, 4);
  assert.equal(image.parentElement.dataset.state, "completed");
});
