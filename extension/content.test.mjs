import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

class ClassList {
  constructor(values = []) {
    this.values = new Set(values);
  }

  contains(value) {
    return this.values.has(value);
  }

  remove(...values) {
    values.forEach((value) => this.values.delete(value));
  }
}

class FakeWrapper {
  constructor() {
    this.classList = new ClassList(["comic-enhancer-page"]);
    this.dataset = {};
    this.title = "";
    this.children = [];
    this.style = {};
  }

  append(child) {
    this.children.push(child);
    child.parentElement = this;
  }

  querySelector(selector) {
    if (selector.includes("comic-enhancer-result")) {
      return this.children.find((child) => child.className === "comic-enhancer-result") || null;
    }
    return this.children.find((child) => child !== this.result) || null;
  }
}

class FakeImage {
  constructor() {
    this.dataset = { src: "https://img.example/page-1.webp" };
    this.currentSrc = "https://img.example/loading.jpg";
    this.src = this.currentSrc;
    this.naturalWidth = 1124;
    this.naturalHeight = 1600;
    this.complete = true;
    this.classList = new ClassList(["lazyload"]);
    this.className = "lazyload";
    this.parentElement = null;
    this.alt = "";
  }

  getAttribute(name) {
    if (name === "width" || name === "height" || name === "data-lazy-src") return null;
    return null;
  }

  removeAttribute(name) {
    if (name === "data-src") delete this.dataset.src;
  }

  getBoundingClientRect() {
    return { width: 1124, height: 1600, bottom: 100 };
  }

  before(wrapper) {
    wrapper.append(this);
  }

  addEventListener(type, listener) {
    if (type === "load") queueMicrotask(listener);
  }
}

test("result overlay sizing overrides generic page image sizing", () => {
  const css = readFileSync(new URL("./content.css", import.meta.url), "utf8");

  assert.match(css, /\.comic-enhancer-page\s*>\s*\.comic-enhancer-result\s*\{/);
  assert.match(css, /display:\s*grid/);
  assert.match(css, /visibility:\s*hidden/);
  assert.match(css, /width:\s*100%\s*!important/);
});

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
    querySelector() { return null; },
    querySelectorAll(selector) {
      return selector.includes("img") ? [image] : [];
    },
    createElement(tag) {
      return tag === "span" ? new FakeWrapper() : new FakeImage();
    },
  };
  globalThis.window = { addEventListener() {} };
  globalThis.IntersectionObserver = class {
    constructor() {}
    observe() {}
  };
  globalThis.MutationObserver = class {
    constructor() {}
    observe() {}
  };
  globalThis.chrome = {
    runtime: {
      onMessage: {
        addListener(listener) { runtimeListeners.push(listener); },
      },
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
              adapter_source: "none",
              adapter_applied: false,
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
