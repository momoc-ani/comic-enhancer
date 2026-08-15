(() => {
  if (globalThis.__comicEnhancerInjected) return;
  const CopyMangaAdapter = globalThis.ComicEnhancerCopyManga?.CopyMangaAdapter;
  if (!CopyMangaAdapter) {
    console.warn("Comic Enhancer: 拷贝漫画适配器未加载");
    return;
  }
  globalThis.__comicEnhancerInjected = true;

  const entriesByImage = new WeakMap();
  const entriesByUrl = new Map();
  const warmedUrls = new Set();
  const queuedPrefetchUrls = new Set();
  const pending = [];
  let active = false;
  let settings = null;
  let sequence = 0;
  let scheduler = null;
  let settingsVersion = 0;
  const retainedPagesBehind = 2;
  const minimumRetainedPagesAhead = 3;
  const currentChapterPrefetchPriority = 100;
  const nextChapterPrefetchPriority = 200;

  class Scheduler {
    // 方法说明：初始化当前对象及其运行状态。
    constructor(adapter, work, chapter) {
      this.adapter = adapter;
      this.work = work;
      this.chapter = chapter;
      this.prefetchVersion = -1;
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
        const imageUrl = this.adapter.imageUrl(image);
        if (!entriesByImage.has(image)) {
          entriesByImage.set(image, { image, imageUrl, index, state: "idle" });
          this.observer.observe(image);
        } else {
          const entry = entriesByImage.get(image);
          entry.index = index;
          entry.imageUrl = imageUrl;
        }
        if (imageUrl) entriesByUrl.set(imageUrl, entriesByImage.get(image));
      });
      this.prefetchAroundViewport(images);
    }

    // 方法说明：为当前设置版本启动当前话和下一话的顺序缓存预热。
    startOrderedPrefetch() {
      if (this.prefetchVersion === settingsVersion) return;
      const version = settingsVersion;
      this.prefetchVersion = version;
      this.scheduleOrderedPrefetch(version).catch((error) => {
        if (version !== settingsVersion) return;
        console.warn(
          "Comic Enhancer 顺序预生成失败:",
          error instanceof Error ? error.message : String(error),
        );
      });
    }

    // 方法说明：先按页码加入当前话，再按页码加入紧邻下一话。
    async scheduleOrderedPrefetch(version) {
      const currentUrls = await this.adapter.getChapterImageUrls();
      if (version !== settingsVersion) return;
      currentUrls.forEach((imageUrl, index) => {
        this.enqueuePrefetch(
          imageUrl,
          index,
          this.chapter,
          currentChapterPrefetchPriority,
          "current",
        );
      });

      const nextChapter = await this.adapter.loadNextChapter();
      if (version !== settingsVersion || !nextChapter) return;
      nextChapter.imageUrls.forEach((imageUrl, index) => {
        this.enqueuePrefetch(
          imageUrl,
          index,
          nextChapter.chapter,
          nextChapterPrefetchPriority,
          "next",
        );
      });
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
      warmedUrls.clear();
      queuedPrefetchUrls.clear();
      this.prefetchVersion = -1;
      for (const image of this.adapter.findImages()) {
        const entry = entriesByImage.get(image);
        if (!entry) continue;
        entry.state = "idle";
        const wrapper = ensureWrapper(image);
        delete wrapper.dataset.state;
        wrapper.title = "";
      }
      this.discover();
      this.startOrderedPrefetch();
    }

    // 方法说明：停用增强功能时废弃所有尚未执行的跨章节任务。
    cancelPending() {
      settingsVersion += 1;
      pending.length = 0;
      warmedUrls.clear();
      queuedPrefetchUrls.clear();
      this.prefetchVersion = -1;
    }

    // 方法说明：按优先级将漫画页加入处理队列。
    enqueue(image, priority) {
      const entry = entriesByImage.get(image);
      if (!entry || entry.state !== "idle") return;
      const imageUrl = this.adapter.imageUrl(image);
      if (!imageUrl) return;
      entry.imageUrl = imageUrl;
      entriesByUrl.set(imageUrl, entry);
      entry.state = "queued";
      pending.push({
        ...entry,
        kind: "display",
        imageUrl,
        priority,
        sequence: sequence++,
        settingsVersion,
      });
      pending.sort(compareTasks);
      this.drain();
    }

    // 方法说明：将无页面覆盖层的缓存预热任务加入统一优先级队列。
    enqueuePrefetch(imageUrl, index, chapter, priority, chapterScope) {
      if (!imageUrl || warmedUrls.has(imageUrl) || queuedPrefetchUrls.has(imageUrl)) return;
      const displayEntry = entriesByUrl.get(imageUrl);
      if (displayEntry && displayEntry.state !== "idle") return;
      queuedPrefetchUrls.add(imageUrl);
      pending.push({
        kind: "prefetch",
        imageUrl,
        index,
        priority,
        chapter,
        chapterScope,
        sequence: sequence++,
        settingsVersion,
      });
      pending.sort(compareTasks);
      this.drain();
    }

    // 方法说明：串行消费显示与缓存预热任务并保持跨章节优先级。
    async drain() {
      if (active || pending.length === 0) return;
      const task = pending.shift();
      if (task.settingsVersion !== settingsVersion) {
        queuedPrefetchUrls.delete(task.imageUrl);
        this.drain();
        return;
      }
      active = true;
      try {
        if (task.kind === "prefetch") await this.processPrefetchTask(task);
        else await this.processDisplayTask(task);
      } finally {
        active = false;
        this.drain();
      }
    }

    // 方法说明：处理可见页面任务并在成功后显示增强结果。
    async processDisplayTask(task) {
      const entry = entriesByImage.get(task.image);
      if (!entry || entry.state !== "queued") return;
      entry.state = "processing";
      markState(task.image, "processing");
      try {
        const response = await this.requestProcessing(task, false);
        if (task.settingsVersion !== settingsVersion) return;
        await ensureSourceImageLoaded(task.image, task.imageUrl);
        if (task.settingsVersion !== settingsVersion) return;
        await showResult(task.image, response.result);
        entry.state = "completed";
        warmedUrls.add(task.imageUrl);
      } catch (error) {
        if (task.settingsVersion !== settingsVersion) return;
        entry.state = "failed";
        markState(
          task.image,
          "failed",
          error instanceof Error ? error.message : String(error),
        );
      }
    }

    // 方法说明：处理缓存预热任务且不向当前页面下载增强结果。
    async processPrefetchTask(task) {
      try {
        if (warmedUrls.has(task.imageUrl)) return;
        await this.requestProcessing(task, true);
        if (task.settingsVersion === settingsVersion) warmedUrls.add(task.imageUrl);
      } catch (error) {
        if (task.settingsVersion !== settingsVersion) return;
        console.warn(
          `Comic Enhancer ${task.chapterScope === "next" ? "下一话" : "当前话"}` +
            `第 ${task.index + 1} 页预生成失败:`,
          error instanceof Error ? error.message : String(error),
        );
      } finally {
        queuedPrefetchUrls.delete(task.imageUrl);
      }
    }

    // 方法说明：向后台发送显示或仅缓存的统一页面处理请求。
    async requestProcessing(task, prefetchOnly) {
      if (task.settingsVersion !== settingsVersion) {
        throw new Error("页面处理设置已变化");
      }
      const response = await chrome.runtime.sendMessage({
        type: "COMIC_ENHANCER_PROCESS",
        payload: {
          imageUrl: task.imageUrl,
          work: this.work,
          chapter: task.chapter || this.chapter,
          options: { page_index: task.index, palette_version: "default" },
          prefetchOnly,
        },
      });
      if (!response?.ok) throw new Error(response?.error || "Unknown error");
      return response;
    }
  }

  // 方法说明：按任务层级、页码和入队顺序稳定排列处理任务。
  function compareTasks(left, right) {
    return (
      left.priority - right.priority ||
      left.index - right.index ||
      left.sequence - right.sequence
    );
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
    scheduler.startOrderedPrefetch();

    const mutations = new MutationObserver(() => scheduler.discover());
    mutations.observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener("scroll", () => scheduler.discover(), { passive: true });
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "COMIC_ENHANCER_REFRESH_SETTINGS") return false;
    const previouslyEnabled = settings?.enabled;
    const runChanged =
      settings?.mode !== message.settings.mode ||
      settings?.apiBaseUrl !== message.settings.apiBaseUrl ||
      settings?.apiToken !== message.settings.apiToken;
    settings = { ...settings, ...message.settings };
    if (settings.enabled) {
      if (!scheduler) {
        bootstrap().catch((error) => console.warn("Comic Enhancer:", error));
      } else if (runChanged || !previouslyEnabled) scheduler.resetForSettingsChange();
      else scheduler?.retryFailed();
    } else scheduler?.cancelPending();
    sendResponse({ ok: true });
    return false;
  });

  bootstrap().catch((error) => console.warn("Comic Enhancer:", error));
})();
