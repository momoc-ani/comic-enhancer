(() => {
  if (globalThis.__comicEnhancerInjected) return;
  globalThis.__comicEnhancerInjected = true;

  const entriesByImage = new WeakMap();
  const pending = [];
  let active = false;
  let settings = null;
  let sequence = 0;
  let scheduler = null;
  let settingsVersion = 0;
  const retainedPagesBehind = 2;
  const minimumRetainedPagesAhead = 3;

  class CopyMangaAdapter {
    // 方法说明：判断当前页面是否属于支持的漫画站点。
    static matches() {
      return /(^|\.)(copymanga\.(com|site|tv)|mangacopy\.com)$/i.test(
        location.hostname,
      );
    }

    // 方法说明：从页面中提取稳定的作品身份信息。
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

    // 方法说明：从页面中提取当前章节信息。
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

    // 方法说明：查找并去重页面中的有效漫画图片。
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

    // 方法说明：解析图片真实地址并过滤占位数据。
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

    // 方法说明：读取指定页面元标签的内容。
    meta(attribute, value) {
      return document
        .querySelector(`meta[${attribute}="${value}"]`)
        ?.getAttribute("content")
        ?.trim();
    }

    // 方法说明：从页面标注中提取作者名称。
    findAuthor() {
      const labelled = [...document.querySelectorAll("a, span, div")].find((node) =>
        /^(作者|作者：|作者:)/.test(node.textContent?.trim() || ""),
      );
      return labelled?.textContent?.replace(/^作者[：:]?/, "").trim() || "";
    }

    // 方法说明：从页面中提取作品标签。
    findTags() {
      return [...document.querySelectorAll("[class*='tag'] a, [class*='theme'] a")]
        .map((node) => node.textContent?.trim())
        .filter(Boolean)
        .slice(0, 20);
    }
  }

  // 方法说明：计算候选漫画图片的选择分数。
  function imageScore(image) {
    const box = image.getBoundingClientRect();
    return (
      (image.classList.contains("blank") ? -1000 : 0) +
      (box.width > 0 && box.height > 0 ? 100 : 0) +
      (image.complete && image.naturalWidth > 0 ? 10 : 0)
    );
  }

  class Scheduler {
    // 方法说明：初始化当前对象及其运行状态。
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

    // 方法说明：发现新漫画页并注册可视区域观察。
    discover() {
      const images = this.adapter.findImages();
      images.forEach((image, index) => {
        if (!entriesByImage.has(image)) {
          entriesByImage.set(image, { image, index, state: "idle" });
          this.observer.observe(image);
        } else {
          entriesByImage.get(image).index = index;
        }
      });
      this.prefetchAroundViewport(images);
    }

    // 方法说明：调度当前视口附近的漫画页预取。
    prefetchAroundViewport(images) {
      const firstBelowViewport = images.findIndex(
        (image) => image.getBoundingClientRect().bottom >= 0,
      );
      const start = firstBelowViewport < 0 ? 0 : firstBelowViewport;
      const count = Math.max(0, Number(settings.prefetchPages) || 0);
      images.slice(start, start + count + 1).forEach((image, offset) => {
        this.enqueue(image, offset);
      });
      this.releaseDistantResults(images, start, count);
    }

    // 方法说明：释放远离视口的增强图以控制内存。
    releaseDistantResults(images, start, prefetchCount) {
      const keepStart = Math.max(0, start - retainedPagesBehind);
      const keepEnd = start + Math.max(prefetchCount, minimumRetainedPagesAhead);
      images.forEach((image, index) => {
        if (index >= keepStart && index <= keepEnd) return;
        const entry = entriesByImage.get(image);
        if (!entry || entry.state !== "completed") return;
        const wrapper = image.parentElement;
        wrapper?.querySelector(":scope > .comic-enhancer-result")?.remove();
        if (wrapper) {
          delete wrapper.dataset.state;
          wrapper.title = "";
        }
        entry.state = "idle";
      });
    }

    // 方法说明：重置失败页面并重新加入调度。
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

    // 方法说明：在推理设置变化后重置页面任务。
    resetForSettingsChange() {
      settingsVersion += 1;
      pending.length = 0;
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

    // 方法说明：按优先级将漫画页加入处理队列。
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

    // 方法说明：串行消费队列并更新页面处理状态。
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

  // 方法说明：确保真实漫画原图已经加载完成。
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

  // 方法说明：移除懒加载属性并固定真实原图地址。
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

  // 方法说明：加载增强结果并覆盖显示在原图上方。
  async function showResult(image, result) {
    const wrapper = ensureWrapper(image);
    let overlay = wrapper.querySelector(":scope > .comic-enhancer-result");
    if (!overlay) {
      overlay = document.createElement("img");
      overlay.className = "comic-enhancer-result";
      overlay.alt = image.alt || "Enhanced manga page";
      wrapper.append(overlay);
    }
    // 结果图先完成加载，再切换可见状态，避免站点 lazy-load/响应式样式
    // 在加载中途改变叠加层的几何尺寸。
    const loaded = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("增强结果加载超时")), 15000);
      overlay.addEventListener("load", () => {
        clearTimeout(timeout);
        resolve();
      }, { once: true });
      overlay.addEventListener("error", () => {
        clearTimeout(timeout);
        reject(new Error("增强结果加载失败"));
      }, { once: true });
    });
    overlay.src = result.image_data_url;
    overlay.dataset.adapterSource = result.adapter_source;
    overlay.dataset.adapterId = result.adapter_id || "none";
    overlay.dataset.adapterApplied = String(result.adapter_applied);
    overlay.dataset.modelProfile = result.model_profile || "unknown";
    overlay.dataset.referenceApplied = String(result.reference_applied);
    overlay.dataset.processedPanels = String(result.processed_panels || 0);
    await loaded;
    markState(image, "completed");
  }

  // 方法说明：创建或复用漫画页结果容器。
  function ensureWrapper(image) {
    if (image.parentElement?.classList.contains("comic-enhancer-page")) {
      const wrapper = image.parentElement;
      syncWrapperGeometry(image, wrapper);
      return wrapper;
    }
    const wrapper = document.createElement("span");
    wrapper.className = "comic-enhancer-page";
    image.before(wrapper);
    wrapper.append(image);
    syncWrapperGeometry(image, wrapper);
    return wrapper;
  }

  // 方法说明：同步结果容器与原图的宽高比例。
  function syncWrapperGeometry(image, wrapper) {
    if (!wrapper.style) return;
    const width = Number(image.naturalWidth) || 0;
    const height = Number(image.naturalHeight) || 0;
    if (width > 0 && height > 0) {
      wrapper.style.aspectRatio = `${width} / ${height}`;
    }
  }

  // 方法说明：更新漫画页的处理状态和提示。
  function markState(image, state, message = "") {
    const wrapper = ensureWrapper(image);
    wrapper.dataset.state = state;
    wrapper.title = message;
  }

  // 方法说明：识别页面并启动漫画页调度器。
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
