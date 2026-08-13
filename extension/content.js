(() => {
  if (globalThis.__comicEnhancerInjected) return;
  globalThis.__comicEnhancerInjected = true;

  const entriesByImage = new WeakMap();
  const pending = [];
  let active = false;
  let settings = null;
  let sequence = 0;
  let analysisStarted = false;
  let analysisPromise = null;
  let scheduler = null;
  let settingsVersion = 0;

  class CopyMangaAdapter {
    static matches() {
      return /(^|\.)(copymanga\.(com|site|tv)|mangacopy\.com)$/i.test(
        location.hostname,
      );
    }

    getWork() {
      const pathParts = location.pathname.split("/").filter(Boolean);
      const comicIndex = pathParts.indexOf("comic");
      const sourceWorkId =
        comicIndex >= 0 && pathParts[comicIndex + 1]
          ? decodeURIComponent(pathParts[comicIndex + 1])
          : this.meta("property", "og:url") || location.pathname;

      return {
        source: "copy_manga",
        source_work_id: sourceWorkId,
        title:
          this.meta("property", "og:title") ||
          document.querySelector("h1")?.textContent?.trim() ||
          document.title,
        author: this.findAuthor(),
        tags: this.findTags(),
        cover_url: this.meta("property", "og:image") || null,
      };
    }

    getChapter() {
      const parts = location.pathname.split("/").filter(Boolean);
      const chapterIndex = parts.indexOf("chapter");
      return {
        chapter_id:
          chapterIndex >= 0 && parts[chapterIndex + 1]
            ? decodeURIComponent(parts[chapterIndex + 1])
            : location.pathname,
        title:
          document.querySelector("[class*='chapter'] h1, [class*='chapter'] h2")
            ?.textContent?.trim() || "",
      };
    }

    findImages() {
      const selectors = [
        ".comicContent-list img",
        ".comicContent img",
        "[class*='comic-content'] img",
        "[class*='chapter-content'] img",
        "main img",
      ];
      const candidates = [...document.querySelectorAll(selectors.join(","))];
      const bestByUrl = new Map();
      for (const image of candidates) {
        const url = this.imageUrl(image);
        const width = image.naturalWidth || Number(image.getAttribute("width")) || 0;
        const height = image.naturalHeight || Number(image.getAttribute("height")) || 0;
        if (!url || !(height >= 500 || height > width * 1.1)) continue;
        const previous = bestByUrl.get(url);
        if (!previous || imageScore(image) > imageScore(previous)) {
          bestByUrl.set(url, image);
        }
      }
      return [...bestByUrl.values()];
    }

    imageUrl(image) {
      const raw =
        image.dataset.src ||
        image.dataset.original ||
        image.getAttribute("data-lazy-src") ||
        image.currentSrc ||
        image.src;
      if (!raw || raw.startsWith("data:")) return "";
      try {
        return new URL(raw, location.href).href;
      } catch {
        return "";
      }
    }

    meta(attribute, value) {
      return document
        .querySelector(`meta[${attribute}="${value}"]`)
        ?.getAttribute("content")
        ?.trim();
    }

    findAuthor() {
      const labelled = [...document.querySelectorAll("a, span, div")].find((node) =>
        /^(作者|作者：|作者:)/.test(node.textContent?.trim() || ""),
      );
      return labelled?.textContent?.replace(/^作者[：:]?/, "").trim() || "";
    }

    findTags() {
      return [...document.querySelectorAll("[class*='tag'] a, [class*='theme'] a")]
        .map((node) => node.textContent?.trim())
        .filter(Boolean)
        .slice(0, 20);
    }
  }

  function imageScore(image) {
    const box = image.getBoundingClientRect();
    return (
      (image.classList.contains("blank") ? -1000 : 0) +
      (box.width > 0 && box.height > 0 ? 100 : 0) +
      (image.complete && image.naturalWidth > 0 ? 10 : 0)
    );
  }

  class Scheduler {
    constructor(adapter, work, chapter) {
      this.adapter = adapter;
      this.work = work;
      this.chapter = chapter;
      this.observer = new IntersectionObserver(
        (observations) => {
          for (const observation of observations) {
            if (observation.isIntersecting) this.enqueue(observation.target, 0);
          }
        },
        { rootMargin: "150% 0px 150% 0px", threshold: 0.01 },
      );
    }

    discover() {
      const images = this.adapter.findImages();
      images.forEach((image, index) => {
        if (!entriesByImage.has(image)) {
          entriesByImage.set(image, { image, index, state: "idle" });
          this.observer.observe(image);
        }
      });
      this.analyzeWindow(images);
      this.prefetchAroundViewport(images);
    }

    analyzeWindow(images) {
      if (analysisStarted || settings.mode !== "manganinja") return;
      const imageUrls = images
        .slice(0, 8)
        .map((image) => this.adapter.imageUrl(image))
        .filter(Boolean);
      if (imageUrls.length === 0) return;
      analysisStarted = true;
      analysisPromise = chrome.runtime
        .sendMessage({
          type: "COMIC_ENHANCER_ANALYZE",
          payload: { imageUrls, work: this.work },
        })
        .then((response) => {
          if (!response?.ok) console.warn("Comic Enhancer analysis:", response?.error);
        })
        .catch((error) => console.warn("Comic Enhancer analysis:", error));
    }

    prefetchAroundViewport(images) {
      const firstBelowViewport = images.findIndex(
        (image) => image.getBoundingClientRect().bottom >= 0,
      );
      const start = firstBelowViewport < 0 ? 0 : firstBelowViewport;
      const count = Math.max(0, Number(settings.prefetchPages) || 0);
      images.slice(start, start + count + 1).forEach((image, offset) => {
        this.enqueue(image, offset);
      });
    }

    retryFailed() {
      for (const image of this.adapter.findImages()) {
        const entry = entriesByImage.get(image);
        if (!entry || entry.state !== "failed") continue;
        entry.state = "idle";
        const wrapper = ensureWrapper(image);
        delete wrapper.dataset.state;
        wrapper.title = "";
      }
      this.discover();
    }

    resetForSettingsChange() {
      settingsVersion += 1;
      pending.length = 0;
      analysisStarted = false;
      analysisPromise = null;
      for (const image of this.adapter.findImages()) {
        const entry = entriesByImage.get(image);
        if (!entry) continue;
        entry.state = "idle";
        const wrapper = ensureWrapper(image);
        delete wrapper.dataset.state;
        wrapper.title = "";
      }
      this.discover();
    }

    enqueue(image, priority) {
      const entry = entriesByImage.get(image);
      if (!entry || entry.state !== "idle") return;
      const imageUrl = this.adapter.imageUrl(image);
      if (!imageUrl) return;
      entry.state = "queued";
      pending.push({
        ...entry,
        imageUrl,
        priority,
        sequence: sequence++,
        settingsVersion,
      });
      pending.sort((a, b) => a.priority - b.priority || a.sequence - b.sequence);
      this.drain();
    }

    async drain() {
      if (active || pending.length === 0) return;
      active = true;
      const task = pending.shift();
      const entry = entriesByImage.get(task.image);
      if (!entry || entry.state !== "queued") {
        active = false;
        this.drain();
        return;
      }

      entry.state = "processing";
      markState(task.image, "processing");
      try {
        if (settings.mode === "manganinja" && analysisPromise) {
          await analysisPromise;
        }
        if (task.settingsVersion !== settingsVersion) return;
        const response = await chrome.runtime.sendMessage({
          type: "COMIC_ENHANCER_PROCESS",
          payload: {
            imageUrl: task.imageUrl,
            work: this.work,
            chapter: this.chapter,
            options: { page_index: task.index, palette_version: "default" },
          },
        });
        if (task.settingsVersion !== settingsVersion) return;
        if (!response?.ok) throw new Error(response?.error || "Unknown error");
        await ensureSourceImageLoaded(task.image, task.imageUrl);
        if (task.settingsVersion !== settingsVersion) return;
        await showResult(task.image, response.result);
        entry.state = "completed";
      } catch (error) {
        if (task.settingsVersion !== settingsVersion) return;
        entry.state = "failed";
        markState(task.image, "failed", error instanceof Error ? error.message : String(error));
      } finally {
        active = false;
        this.drain();
      }
    }
  }

  async function ensureSourceImageLoaded(image, imageUrl) {
    const currentUrl = image.currentSrc || image.src;
    if (
      currentUrl === imageUrl &&
      image.complete &&
      image.naturalWidth > 0 &&
      image.naturalHeight > 0
    ) {
      pinSourceImage(image, imageUrl);
      return;
    }
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(
        () => reject(new Error("漫画原图加载超时")),
        15000,
      );
      const complete = (callback) => () => {
        clearTimeout(timeout);
        callback();
      };
      image.addEventListener("load", complete(resolve), { once: true });
      image.addEventListener(
        "error",
        complete(() => reject(new Error("漫画原图加载失败"))),
        { once: true },
      );
      pinSourceImage(image, imageUrl);
    });
  }

  function pinSourceImage(image, imageUrl) {
    for (const attribute of [
      "data-src",
      "data-original",
      "data-lazy-src",
      "data-srcset",
      "srcset",
    ]) {
      image.removeAttribute(attribute);
    }
    image.classList.remove("lazyload", "lazyloading");
    image.src = imageUrl;
  }

  async function showResult(image, result) {
    const wrapper = ensureWrapper(image);
    let overlay = wrapper.querySelector(":scope > .comic-enhancer-result");
    if (!overlay) {
      overlay = document.createElement("img");
      overlay.className = "comic-enhancer-result";
      overlay.alt = image.alt || "Enhanced manga page";
      wrapper.append(overlay);
    }
    overlay.src = result.image_data_url;
    overlay.dataset.adapterSource = result.adapter_source;
    overlay.dataset.adapterId = result.adapter_id || "none";
    overlay.dataset.adapterApplied = String(result.adapter_applied);
    overlay.dataset.modelProfile = result.model_profile || "unknown";
    overlay.dataset.referenceApplied = String(result.reference_applied);
    overlay.dataset.processedPanels = String(result.processed_panels || 0);
    overlay.addEventListener("load", () => markState(image, "completed"), { once: true });
  }

  function ensureWrapper(image) {
    if (image.parentElement?.classList.contains("comic-enhancer-page")) {
      return image.parentElement;
    }
    const wrapper = document.createElement("span");
    wrapper.className = "comic-enhancer-page";
    image.before(wrapper);
    wrapper.append(image);
    return wrapper;
  }

  function markState(image, state, message = "") {
    const wrapper = ensureWrapper(image);
    wrapper.dataset.state = state;
    wrapper.title = message;
  }

  async function bootstrap() {
    if (!CopyMangaAdapter.matches()) return;
    settings = await chrome.runtime.sendMessage({ type: "COMIC_ENHANCER_SETTINGS" });
    if (!settings?.enabled) return;

    const adapter = new CopyMangaAdapter();
    scheduler = new Scheduler(adapter, adapter.getWork(), adapter.getChapter());
    scheduler.discover();

    const mutations = new MutationObserver(() => scheduler.discover());
    mutations.observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener("scroll", () => scheduler.discover(), { passive: true });
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "COMIC_ENHANCER_REFRESH_SETTINGS") return false;
    const runChanged =
      settings?.mode !== message.settings.mode ||
      settings?.apiBaseUrl !== message.settings.apiBaseUrl;
    settings = { ...settings, ...message.settings };
    if (settings.enabled) {
      if (runChanged) scheduler?.resetForSettingsChange();
      else scheduler?.retryFailed();
    }
    sendResponse({ ok: true });
    return false;
  });

  bootstrap().catch((error) => console.warn("Comic Enhancer:", error));
})();
