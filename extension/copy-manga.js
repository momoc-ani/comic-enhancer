(() => {
  if (globalThis.ComicEnhancerCopyManga) return;

  const COPY_MANGA_HOST_PATTERN =
    /(^|\.)(copymanga\.(com|site|tv)|mangacopy\.com|copy3000\.com)$/i;
  const COPY_MANGA_CHAPTER_PATH_PATTERN =
    /^\/comic\/[^/]+\/chapter\/[^/]+\/?$/i;
  const CHAPTER_IMAGE_SELECTORS = [
    ".comicContent-list img",
    ".comicContent img",
    "[class*='comic-content'] img",
    "[class*='chapter-content'] img",
    "main img",
  ];

  class CopyMangaAdapter {
    // 方法说明：绑定当前拷贝漫画页面及其地址。
    constructor(pageDocument = globalThis.document, pageLocation = globalThis.location) {
      this.pageDocument = pageDocument;
      this.pageLocation = pageLocation;
    }

    // 方法说明：判断指定页面地址是否属于拷贝漫画章节阅读页。
    static matches(pageLocation = globalThis.location) {
      return (
        COPY_MANGA_HOST_PATTERN.test(pageLocation?.hostname || "") &&
        COPY_MANGA_CHAPTER_PATH_PATTERN.test(pageLocation?.pathname || "")
      );
    }

    // 方法说明：从当前页面提取稳定的作品身份信息。
    getWork() {
      const pathParts = this.pageLocation.pathname.split("/").filter(Boolean);
      const comicIndex = pathParts.indexOf("comic");
      const sourceWorkId =
        comicIndex >= 0 && pathParts[comicIndex + 1]
          ? decodeURIComponent(pathParts[comicIndex + 1])
          : this.meta("property", "og:url") || this.pageLocation.pathname;

      const rawTitle =
        this.meta("property", "og:title") ||
        this.pageDocument.querySelector("h1")?.textContent?.trim() ||
        this.pageDocument.title;
      return {
        source: "copy_manga",
        source_work_id: sourceWorkId,
        title: normalizeWorkTitle(rawTitle),
        title_aliases: [],
        author: this.findAuthor(),
        tags: this.findTags(),
        cover_url: this.meta("property", "og:image") || null,
      };
    }

    // 方法说明：读取作品目录页并补全稳定标题、别名、作者和封面。
    async getEnrichedWork() {
      const work = this.getWork();
      const detailDocument = await this.loadWorkDetail(work.source_work_id);
      if (!detailDocument) return work;
      const details = extractWorkDetails(detailDocument, this.pageLocation.href);
      return {
        ...work,
        title: details.title || work.title,
        title_aliases: details.title_aliases,
        author: details.author || work.author,
        tags: details.tags.length > 0 ? details.tags : work.tags,
        cover_url: details.cover_url || work.cover_url,
      };
    }

    // 方法说明：限时下载同站作品目录页，失败时允许继续使用章节页信息。
    async loadWorkDetail(sourceWorkId) {
      if (typeof globalThis.DOMParser !== "function") return null;
      const controller = new AbortController();
      const timer = globalThis.setTimeout(() => controller.abort(), 3000);
      try {
        const detailUrl = new URL(
          `/comic/${encodeURIComponent(sourceWorkId)}`,
          this.pageLocation.origin,
        );
        const response = await fetch(detailUrl, {
          credentials: "include",
          cache: "force-cache",
          signal: controller.signal,
        });
        if (!response.ok) return null;
        return new DOMParser().parseFromString(await response.text(), "text/html");
      } catch {
        return null;
      } finally {
        globalThis.clearTimeout(timer);
      }
    }

    // 方法说明：从当前页面及地址中提取章节身份信息。
    getChapter() {
      const parts = this.pageLocation.pathname.split("/").filter(Boolean);
      const chapterIndex = parts.indexOf("chapter");
      const heading = this.pageDocument
        .querySelector("[class*='chapter'] h1, [class*='chapter'] h2")
        ?.textContent?.trim();
      return {
        chapter_id:
          chapterIndex >= 0 && parts[chapterIndex + 1]
            ? decodeURIComponent(parts[chapterIndex + 1])
            : this.pageLocation.pathname,
        title: heading || extractChapterTitle(this.pageDocument.title),
      };
    }

    // 方法说明：查找并去重当前章节中已加载或已声明真实地址的漫画图片。
    findImages({ includeUnloaded = true } = {}) {
      const candidates = [];
      CHAPTER_IMAGE_SELECTORS.forEach((selector, selectorIndex) => {
        for (const image of this.pageDocument.querySelectorAll(selector)) {
          candidates.push({ image, selectorIndex });
        }
      });
      const bestByUrl = new Map();
      for (const { image, selectorIndex } of candidates) {
        const url = this.imageUrl(image);
        const width = image.naturalWidth || Number(image.getAttribute("width")) || 0;
        const height = image.naturalHeight || Number(image.getAttribute("height")) || 0;
        const hasDimensions = height >= 500 || height > width * 1.1;
        const knownChapterContainer = selectorIndex < CHAPTER_IMAGE_SELECTORS.length - 1;
        if (
          !url ||
          isPlaceholderImage(image, url) ||
          (!hasDimensions && (!includeUnloaded || !knownChapterContainer))
        ) {
          continue;
        }
        const previous = bestByUrl.get(url);
        const score = imageScore(image) + (knownChapterContainer ? 2 : 0);
        const previousScore = previous
          ? imageScore(previous.image) + (previous.knownChapterContainer ? 2 : 0)
          : -Infinity;
        if (!previous || score > previousScore) {
          bestByUrl.set(url, { image, knownChapterContainer });
        }
      }
      return [...bestByUrl.values()].map((entry) => entry.image);
    }

    // 方法说明：解析图片真实地址并过滤占位数据。
    imageUrl(image) {
      const raw =
        image.dataset.comicEnhancerSourceUrl ||
        image.dataset.src ||
        image.dataset.original ||
        image.getAttribute("data-lazy-src") ||
        image.currentSrc ||
        image.src;
      if (!raw || raw.startsWith("data:")) return "";
      return normalizeUrl(raw, this.pageLocation.href);
    }

    // 方法说明：解密当前章节的完整有序图片地址列表。
    async getChapterImageUrls() {
      const encrypted = extractEncryptedChapter(this.pageDocument);
      if (encrypted) {
        return decryptChapterImageUrls(
          encrypted.contentKey,
          encrypted.cipherKey,
          this.pageLocation.href,
        );
      }
      return this.findImages({ includeUnloaded: true })
        .map((image) => this.imageUrl(image))
        .filter(Boolean);
    }

    // 方法说明：查找拷贝漫画页面声明的紧邻下一话地址。
    getNextChapterUrl() {
      const link =
        this.pageDocument.querySelector(".comicContent-next a[href]") ||
        this.pageDocument.querySelector("a[rel='next'][href]") ||
        [...this.pageDocument.querySelectorAll("a[href]")].find((candidate) =>
          /^(下一話|下一话|下一章|下章)$/.test(candidate.textContent?.trim() || ""),
        );
      return link ? normalizeUrl(link.getAttribute("href") || link.href, this.pageLocation.href) : "";
    }

    // 方法说明：下载并解析紧邻下一话的章节身份和完整图片列表。
    async loadNextChapter() {
      return (await this.loadFollowingChapters(1))[0] || null;
    }

    // 方法说明：逐话抓取指定数量的后续章节并阻止循环或跨域跳转。
    async loadFollowingChapters(limit) {
      const count = Math.max(0, Math.min(20, Number.parseInt(String(limit), 10) || 0));
      const chapters = [];
      const initialUrl = new URL(this.pageLocation.href);
      const visitedUrls = new Set([initialUrl.href]);
      const visitedChapters = new Set([this.getChapter().chapter_id]);
      let currentAdapter = this;
      for (let offset = 0; offset < count; offset += 1) {
        const nextUrl = currentAdapter.getNextChapterUrl();
        if (!nextUrl) break;
        const requestedUrl = new URL(nextUrl);
        if (requestedUrl.origin !== initialUrl.origin) {
          throw new Error("下一话链接跨域，已停止预生成");
        }
        if (visitedUrls.has(requestedUrl.href)) break;
        const next = await this.loadFollowingChapterWithRetry(
          requestedUrl,
          initialUrl.origin,
        );
        const { resolvedUrl, chapter, imageUrls } = next;
        if (visitedUrls.has(resolvedUrl.href)) break;
        if (visitedChapters.has(chapter.chapter_id)) break;
        chapters.push({ url: resolvedUrl.href, chapter, imageUrls });
        visitedUrls.add(resolvedUrl.href);
        visitedChapters.add(chapter.chapter_id);
        currentAdapter = next.adapter;
      }
      return chapters;
    }

    // 方法说明：短暂网络错误或懒加载响应异常时重试下一话解析。
    async loadFollowingChapterWithRetry(requestedUrl, initialOrigin) {
      let lastError = null;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const response = await fetch(requestedUrl.href, {
            credentials: "include",
            cache: "no-cache",
          });
          if (!response.ok) {
            throw new Error(`下一话页面下载失败：${response.status}`);
          }
          const resolvedUrl = new URL(response.url || requestedUrl.href);
          if (resolvedUrl.origin !== initialOrigin) {
            throw new Error("下一话响应跨域，已停止预生成");
          }
          const parser = new DOMParser();
          const nextDocument = parser.parseFromString(await response.text(), "text/html");
          const adapter = new CopyMangaAdapter(nextDocument, resolvedUrl);
          const chapter = adapter.getChapter();
          const imageUrls = await adapter.getChapterImageUrls();
          if (imageUrls.length === 0) {
            throw new Error("下一话页面没有可预生成的漫画图片");
          }
          return { adapter, chapter, imageUrls, resolvedUrl };
        } catch (error) {
          lastError = error;
          if (attempt >= 2) break;
          await delay((attempt + 1) * 250);
        }
      }
      throw lastError || new Error("下一话解析失败");
    }

    // 方法说明：读取当前页面指定元标签的内容。
    meta(attribute, value) {
      return this.pageDocument
        .querySelector(`meta[${attribute}="${value}"]`)
        ?.getAttribute("content")
        ?.trim();
    }

    // 方法说明：从当前页面标注中提取作者名称。
    findAuthor(pageDocument = this.pageDocument) {
      const labelled = [...pageDocument.querySelectorAll("a, span, div")].find(
        (node) => /^(作者|作者：|作者:)/.test(node.textContent?.trim() || ""),
      );
      return labelled?.textContent?.replace(/^作者[：:]?/, "").trim() || "";
    }

    // 方法说明：从当前页面中提取作品标签。
    findTags(pageDocument = this.pageDocument) {
      return [
        ...pageDocument.querySelectorAll("[class*='tag'] a, [class*='theme'] a"),
      ]
        .map((node) => node.textContent?.trim())
        .filter(Boolean)
        .slice(0, 20);
    }
  }

  // 方法说明：从页面标题中提取“第 N 话/章/回”作为可读章节名称。
  function extractChapterTitle(value) {
    return String(value || "").match(/第\s*[^|｜/\\-]{1,40}(?:话|話|章|回)/u)?.[0]?.trim() || "";
  }

  // 方法说明：从作品目录页提取可用于跨元数据源检索的稳定作品信息。
  function extractWorkDetails(pageDocument, baseUrl) {
    const titleNode = pageDocument.querySelector(".comicParticulars-title h6");
    const title = titleNode
      ? normalizeWorkTitle(
          titleNode.getAttribute("title") || titleNode.textContent,
        )
      : "";
    const aliasRow = [
      ...pageDocument.querySelectorAll(".comicParticulars-title li"),
    ].find((item) => /^(別名|别名)[：:]?/.test(item.textContent?.trim() || ""));
    const aliases = (aliasRow?.querySelector(".comicParticulars-right-txt")?.textContent || "")
      .split(/[,，、]/)
      .map((value) => normalizeWorkTitle(value))
      .filter(Boolean);
    const titleAliases = [...new Set(aliases)].filter((value) => value !== title);
    const authorRow = [
      ...pageDocument.querySelectorAll(".comicParticulars-title li"),
    ].find((item) => /^(作者)[：:]?/.test(item.textContent?.trim() || ""));
    const author = [...(authorRow?.querySelectorAll("a") || [])]
      .map((item) => item.textContent?.trim())
      .filter(Boolean)
      .join("、");
    const tags = [
      ...pageDocument.querySelectorAll(".comicParticulars-tag a, [class*='theme'] a"),
    ]
      .map((item) => item.textContent?.trim()?.replace(/^#/, ""))
      .filter(Boolean)
      .slice(0, 20);
    const coverNode = pageDocument.querySelector(".comicParticulars-left-img img");
    const coverUrl = normalizeUrl(
      coverNode?.dataset?.src || coverNode?.currentSrc || coverNode?.src,
      baseUrl,
    );
    return {
      title,
      title_aliases: titleAliases,
      author,
      tags,
      cover_url: coverUrl || null,
    };
  }

  // 方法说明：移除章节编号和站点后缀以得到稳定作品标题。
  function normalizeWorkTitle(value) {
    return String(value || "")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\s*[-|｜]\s*(?:拷貝漫畫|拷贝漫画).*$/u, "")
      .replace(/\s*[-|｜]\s*第[^-|｜]*(?:话|話|章).*$/u, "")
      .replace(/\/\s*第[^/]*(?:话|話|章).*$/u, "")
      .trim();
  }

  // 方法说明：从章节内联脚本中提取图片密文和 AES 密钥。
  function extractEncryptedChapter(pageDocument) {
    let contentKey = "";
    let cipherKey = "";
    for (const script of pageDocument.querySelectorAll("script:not([src])")) {
      const source = script.textContent || "";
      contentKey ||= source.match(/\bcontentKey\s*=\s*['"]([^'"]+)['"]/)?.[1] || "";
      cipherKey ||= source.match(/\bcct\s*=\s*['"]([^'"]+)['"]/)?.[1] || "";
    }
    return contentKey && cipherKey ? { contentKey, cipherKey } : null;
  }

  // 方法说明：使用拷贝漫画页面密钥解密完整的有序图片地址。
  async function decryptChapterImageUrls(contentKey, cipherKey, baseUrl) {
    if (contentKey.length <= 16) throw new Error("拷贝漫画章节密文长度无效");
    const iv = new TextEncoder().encode(contentKey.slice(0, 16));
    const keyBytes = new TextEncoder().encode(cipherKey);
    if (![16, 24, 32].includes(keyBytes.length)) {
      throw new Error("拷贝漫画章节密钥长度无效");
    }
    const cipherBytes = hexToBytes(contentKey.slice(16));
    const cryptoKey = await globalThis.crypto.subtle.importKey(
      "raw",
      keyBytes,
      { name: "AES-CBC" },
      false,
      ["decrypt"],
    );
    const plaintext = await globalThis.crypto.subtle.decrypt(
      { name: "AES-CBC", iv },
      cryptoKey,
      cipherBytes,
    );
    const entries = JSON.parse(new TextDecoder().decode(plaintext));
    if (!Array.isArray(entries)) throw new Error("拷贝漫画章节图片数据格式无效");
    return deduplicateUrls(
      entries.map((entry) => normalizeUrl(entry?.url, baseUrl)).filter(Boolean),
    );
  }

  // 方法说明：将偶数长度十六进制密文转换为字节数组。
  function hexToBytes(value) {
    if (!/^(?:[0-9a-f]{2})+$/i.test(value)) {
      throw new Error("拷贝漫画章节密文不是有效十六进制");
    }
    const bytes = new Uint8Array(value.length / 2);
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Number.parseInt(value.slice(index * 2, index * 2 + 2), 16);
    }
    return bytes;
  }

  // 方法说明：基于章节地址规范化并限制为 HTTP 图片地址。
  function normalizeUrl(value, baseUrl) {
    if (!value) return "";
    try {
      const url = new URL(value, baseUrl);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch {
      return "";
    }
  }

  // 方法说明：按首次出现顺序去重章节图片地址。
  function deduplicateUrls(urls) {
    return [...new Set(urls)];
  }

  // 方法说明：计算候选漫画图片的选择分数。
  function imageScore(image) {
    const box = image.getBoundingClientRect?.() || { width: 0, height: 0 };
    return (
      (image.classList.contains("blank") ? -1000 : 0) +
      (box.width > 0 && box.height > 0 ? 100 : 0) +
      (image.complete && image.naturalWidth > 0 ? 10 : 0)
    );
  }

  // 方法说明：过滤明显的空白图、占位图和加载图。
  function isPlaceholderImage(image, url) {
    if (image.classList?.contains("blank")) return true;
    return /(?:blank|placeholder|loading|spacer)(?:[._/-]|$)|\/(?:static\/)?ads?(?:[._/-]|$)|(?:banner|promotion)(?:[._/-]|$)/i.test(url);
  }

  // 方法说明：等待懒加载器或网络请求完成一次短暂重试间隔。
  function delay(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  globalThis.ComicEnhancerCopyManga = Object.freeze({
    CopyMangaAdapter,
    decryptChapterImageUrls,
    extractEncryptedChapter,
    extractWorkDetails,
    normalizeWorkTitle,
  });
})();
