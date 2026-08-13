import test from "node:test";
import assert from "node:assert/strict";

class ClassList {
  constructor(values = []) {
    this.values = new Set(values);
  }
  contains(value) { return this.values.has(value); }
  remove(...values) { values.forEach((value) => this.values.delete(value)); }
}

class FakeWrapper {
  constructor() {
    this.classList = new ClassList(["comic-enhancer-page"]);
    this.dataset = {};
    this.title = "";
    this.children = [];
  }
  append(child) {
    this.children.push(child);
    child.parentElement = this;
  }
  querySelector(selector) {
    if (!selector.includes("comic-enhancer-result")) return null;
    return this.children.find((child) => child.className === "comic-enhancer-result") || null;
  }
}

class FakeImage {
  constructor(index = 0) {
    this.dataset = { src: `https://img.example/page-${index}.webp` };
    this.currentSrc = "https://img.example/loading.jpg";
    this.src = this.currentSrc;
    this.naturalWidth = 1124;
    this.naturalHeight = 1600;
    this.complete = true;
    this.classList = new ClassList(["lazyload"]);
    this.className = "lazyload";
    this.parentElement = null;
    this.alt = "";
    this.index = index;
  }
  getAttribute() { return null; }
  removeAttribute(name) {
    if (name === "data-src") delete this.dataset.src;
  }
  getBoundingClientRect() {
    return { width: 1124, height: 1600, bottom: this.index < 8 ? -100 : 100 };
  }
  before(wrapper) { wrapper.append(this); }
  remove() {
    if (!this.parentElement) return;
    this.parentElement.children = this.parentElement.children.filter(
      (child) => child !== this,
    );
    this.parentElement = null;
  }
  addEventListener(type, listener) {
    if (type === "load") queueMicrotask(listener);
  }
}

test("analyzes the eight-page window containing the current manga page", async () => {
  let viewportStart = 0;
  let scrollListener = null;
  const images = Array.from({ length: 10 }, (_, index) => new FakeImage(index));
  for (const image of images) {
    image.getBoundingClientRect = () => ({
      width: 1124,
      height: 1600,
      bottom: image.index < viewportStart ? -100 : 100,
    });
  }
  const events = [];
  globalThis.location = {
    hostname: "www.mangacopy.com",
    pathname: "/comic/work/chapter/one",
    href: "https://www.mangacopy.com/comic/work/chapter/one",
  };
  globalThis.window = {
    addEventListener(type, listener) {
      if (type === "scroll") scrollListener = listener;
    },
  };
  globalThis.document = {
    title: "测试漫画",
    documentElement: {},
    querySelector() { return null; },
    querySelectorAll(selector) { return selector.includes("img") ? images : []; },
    createElement(tag) { return tag === "span" ? new FakeWrapper() : new FakeImage(); },
  };
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
      onMessage: { addListener() {} },
      async sendMessage(message) {
        if (message.type === "COMIC_ENHANCER_SETTINGS") {
          return { enabled: true, mode: "manganinja", prefetchPages: 0 };
        }
        if (message.type === "COMIC_ENHANCER_ANALYZE") {
          events.push(["analyze", message.payload.imageUrls]);
          return { ok: true, result: {} };
        }
        if (message.type === "COMIC_ENHANCER_PROCESS") {
          events.push(["process", message.payload.options.page_index]);
          return {
            ok: true,
            result: {
              image_data_url: "data:image/webp;base64,AA==",
              adapter_source: "none",
              adapter_applied: false,
              reference_applied: true,
              processed_panels: 1,
              model_profile: "manganinja-reference",
            },
          };
        }
        return { ok: true };
      },
    },
  };

  await import("./content.js");
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.deepEqual(events, [
    [
      "analyze",
      images.slice(0, 8).map((image) => `https://img.example/page-${image.index}.webp`),
    ],
    ["process", 0],
  ]);
  assert.equal(images[0].parentElement.dataset.state, "completed");

  viewportStart = 8;
  scrollListener();
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.deepEqual(events.slice(2), [
    [
      "analyze",
      [
        "https://img.example/page-8.webp",
        "https://img.example/page-9.webp",
      ],
    ],
    ["process", 8],
  ]);
  assert.equal(images[8].parentElement.dataset.state, "completed");
  assert.equal(images[0].parentElement.dataset.state, undefined);
  assert.equal(
    images[0].parentElement.querySelector(":scope > .comic-enhancer-result"),
    null,
  );
});
