import test from "node:test";
import assert from "node:assert/strict";

class ClassList {
  // 方法说明：初始化测试类名集合。
  constructor(values = []) {
    this.values = new Set(values);
  }

  // 方法说明：判断测试类名是否存在。
  contains(value) {
    return this.values.has(value);
  }

  // 方法说明：移除测试元素的指定类名。
  remove(...values) {
    values.forEach((value) => this.values.delete(value));
  }
}

class FakeWrapper {
  // 方法说明：初始化测试漫画页容器。
  constructor() {
    this.classList = new ClassList(["comic-enhancer-page"]);
    this.dataset = {};
    this.title = "";
    this.children = [];
    this.style = {};
  }

  // 方法说明：向测试容器追加图片节点。
  append(child) {
    this.children.push(child);
    child.parentElement = this;
  }

  // 方法说明：查找测试容器中的增强结果图片。
  querySelector(selector) {
    if (!selector.includes("comic-enhancer-result")) return null;
    return this.children.find((child) => child.className === "comic-enhancer-result") || null;
  }
}

class FakeImage {
  // 方法说明：初始化指定地址和位置的测试漫画图片。
  constructor(url = "", bottom = 100) {
    this.dataset = url ? { src: url } : {};
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

  // 方法说明：读取测试图片的尺寸或懒加载属性。
  getAttribute(name) {
    if (name === "width" || name === "height" || name === "data-lazy-src") return null;
    return null;
  }

  // 方法说明：移除测试图片的懒加载属性。
  removeAttribute(name) {
    if (name === "data-src") delete this.dataset.src;
  }

  // 方法说明：返回测试图片的页面布局信息。
  getBoundingClientRect() {
    return { width: 1124, height: 1600, bottom: this.bottom };
  }

  // 方法说明：在测试图片前插入结果容器。
  before(wrapper) {
    wrapper.append(this);
  }

  // 方法说明：立即完成测试图片加载事件。
  addEventListener(type, listener) {
    if (type === "load") queueMicrotask(listener);
  }
}

// 方法说明：验证当前可视页完成后按页码顺序预生成当前话其余页面。
test("prefetches the current CopyManga chapter in page order", async () => {
  const images = [
    new FakeImage("https://img.example/page-1.webp", 100),
    new FakeImage("https://img.example/page-2.webp", 1700),
    new FakeImage("https://img.example/page-3.webp", 3300),
  ];
  const processCalls = [];
  globalThis.location = {
    hostname: "www.mangacopy.com",
    pathname: "/comic/work/chapter/ordered",
    href: "https://www.mangacopy.com/comic/work/chapter/ordered",
  };
  globalThis.document = {
    title: "顺序预生成测试",
    documentElement: {},
    // 方法说明：返回空的章节元数据与下一话链接。
    querySelector() { return null; },
    // 方法说明：返回测试漫画图片或空的内联脚本列表。
    querySelectorAll(selector) {
      return selector.includes("img") ? images : [];
    },
    // 方法说明：创建测试使用的结果容器或图片。
    createElement(tag) {
      return tag === "span" ? new FakeWrapper() : new FakeImage();
    },
  };
  globalThis.window = { addEventListener() {} };
  globalThis.IntersectionObserver = class {
    // 方法说明：初始化空的视口观察器。
    constructor() {}
    // 方法说明：接受测试图片观察注册。
    observe() {}
  };
  globalThis.MutationObserver = class {
    // 方法说明：初始化空的 DOM 变化观察器。
    constructor() {}
    // 方法说明：接受测试文档观察注册。
    observe() {}
  };
  globalThis.chrome = {
    runtime: {
      onMessage: { addListener() {} },
      // 方法说明：记录显示任务和缓存预热任务的实际发送顺序。
      async sendMessage(message) {
        if (message.type === "COMIC_ENHANCER_SETTINGS") {
          return { enabled: true, mode: "fast", prefetchPages: 0 };
        }
        if (message.type === "COMIC_ENHANCER_PROCESS") {
          processCalls.push({
            imageUrl: message.payload.imageUrl,
            prefetchOnly: message.payload.prefetchOnly,
          });
          return {
            ok: true,
            result: {
              image_data_url: "data:image/webp;base64,AA==",
              reference_applied: false,
              processed_panels: 0,
              model_profile: "sd15-colorize",
            },
          };
        }
        return { ok: true };
      },
    },
  };

  await import("./copy-manga.js");
  await import(`./content.js?ordered-prefetch-test=${Date.now()}`);
  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.deepEqual(processCalls, [
    { imageUrl: "https://img.example/page-1.webp", prefetchOnly: false },
    { imageUrl: "https://img.example/page-2.webp", prefetchOnly: true },
    { imageUrl: "https://img.example/page-3.webp", prefetchOnly: true },
  ]);
});
