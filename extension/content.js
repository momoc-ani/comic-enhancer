(() => {
  const entriesByImage = new WeakMap();
  const pending = [];
  let active = false;
  let settings = null;
  let sequence = 0;

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
      return [...new Set(candidates)].filter((image) => {
        const url = this.imageUrl(image);
        const width = image.naturalWidth || Number(image.getAttribute("width")) || 0;
        const height = image.naturalHeight || Number(image.getAttribute("height")) || 0;
        return Boolean(url) && (height >= 500 || height > width * 1.1);
      });
    }

    imageUrl(image) {
      const raw =
        image.currentSrc ||
        image.dataset.src ||
        image.dataset.original ||
        image.getAttribute("data-lazy-src") ||
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
      this.prefetchAroundViewport(images);
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

    enqueue(image, priority) {
      const entry = entriesByImage.get(image);
      if (!entry || entry.state !== "idle") return;
      const imageUrl = this.adapter.imageUrl(image);
      if (!imageUrl) return;
      entry.state = "queued";
      pending.push({ ...entry, imageUrl, priority, sequence: sequence++ });
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
        const response = await chrome.runtime.sendMessage({
          type: "COMIC_ENHANCER_PROCESS",
          payload: {
            imageUrl: task.imageUrl,
            work: this.work,
            chapter: this.chapter,
            options: { page_index: task.index, palette_version: "default" },
          },
        });
        if (!response?.ok) throw new Error(response?.error || "Unknown error");
        await showResult(task.image, response.result);
        entry.state = "completed";
      } catch (error) {
        entry.state = "failed";
        markState(task.image, "failed", error instanceof Error ? error.message : String(error));
      } finally {
        active = false;
        this.drain();
      }
    }
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
    const scheduler = new Scheduler(adapter, adapter.getWork(), adapter.getChapter());
    scheduler.discover();

    const mutations = new MutationObserver(() => scheduler.discover());
    mutations.observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener("scroll", () => scheduler.discover(), { passive: true });
  }

  bootstrap().catch((error) => console.warn("Comic Enhancer:", error));
})();
