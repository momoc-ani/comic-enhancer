import test from "node:test";
import assert from "node:assert/strict";

await import("./copy-manga.js");

const {
  CopyMangaAdapter,
  decryptChapterImageUrls,
  extractEncryptedChapter,
  extractWorkDetails,
  normalizeWorkTitle,
} = globalThis.ComicEnhancerCopyManga;

// 方法说明：将测试密文字节编码为拷贝漫画使用的十六进制格式。
function bytesToHex(bytes) {
  return [...new Uint8Array(bytes)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

// 方法说明：使用浏览器原生 AES-CBC 生成章节解密测试数据。
async function encryptChapterFixture(entries, cipherKey, ivText) {
  const encoder = new TextEncoder();
  const key = await globalThis.crypto.subtle.importKey(
    "raw",
    encoder.encode(cipherKey),
    { name: "AES-CBC" },
    false,
    ["encrypt"],
  );
  const encrypted = await globalThis.crypto.subtle.encrypt(
    { name: "AES-CBC", iv: encoder.encode(ivText) },
    key,
    encoder.encode(JSON.stringify(entries)),
  );
  return `${ivText}${bytesToHex(encrypted)}`;
}

// 方法说明：创建只提供内联脚本的最小测试文档。
function scriptDocument(...sources) {
  return {
    // 方法说明：返回测试章节中的内联脚本集合。
    querySelectorAll(selector) {
      return selector === "script:not([src])"
        ? sources.map((textContent) => ({ textContent }))
        : [];
    },
  };
}

// 方法说明：创建可供逐话发现测试解析的最小章节文档。
function chapterDocument(chapterId, nextHref = "") {
  const image = {
    dataset: { src: `/images/${chapterId}.webp` },
    naturalWidth: 1000,
    naturalHeight: 1600,
    classList: { contains() { return false; } },
    // 方法说明：返回测试图片属性。
    getAttribute() { return null; },
    // 方法说明：返回测试图片布局尺寸。
    getBoundingClientRect() { return { width: 1000, height: 1600 }; },
  };
  const nextLink = nextHref
    ? {
        textContent: "下一话",
        href: nextHref,
        // 方法说明：返回下一话测试地址。
        getAttribute(name) { return name === "href" ? nextHref : null; },
      }
    : null;
  return {
    title: chapterId,
    // 方法说明：返回章节标题或下一话链接。
    querySelector(selector) {
      if (selector === ".comicContent-next a[href]") return nextLink;
      if (selector.includes("[class*='chapter']")) {
        return { textContent: chapterId };
      }
      return null;
    },
    // 方法说明：返回章节图片、空脚本或链接集合。
    querySelectorAll(selector) {
      if (selector === "script:not([src])" || selector === "a[href]") return [];
      return selector.includes("img") ? [image] : [];
    },
  };
}

// 方法说明：验证章节标题缺少独立标题节点时可从页面标题提取话数。
test("extracts readable chapter title from document title", () => {
  const pageDocument = {
    title: "测试漫画 - 第 12 话 - 拷贝漫画",
    // 方法说明：模拟页面没有独立章节标题节点。
    querySelector() { return null; },
  };
  const adapter = new CopyMangaAdapter(pageDocument, {
    pathname: "/comic/work/chapter/chapter-id",
  });

  assert.deepEqual(adapter.getChapter(), {
    chapter_id: "chapter-id",
    title: "第 12 话",
  });
});

// 方法说明：验证章节密文解密后保持图片原始顺序并去重。
test("decrypts ordered CopyManga chapter image URLs", async () => {
  const cipherKey = "op0zzpvv.nmn.00p";
  const ivText = "1234567890abcdef";
  const contentKey = await encryptChapterFixture(
    [
      { url: "/images/page-1.webp" },
      { url: "https://img.example/page-2.webp" },
      { url: "/images/page-1.webp" },
    ],
    cipherKey,
    ivText,
  );

  const urls = await decryptChapterImageUrls(
    contentKey,
    cipherKey,
    "https://www.mangacopy.com/comic/work/chapter/current",
  );

  assert.deepEqual(urls, [
    "https://www.mangacopy.com/images/page-1.webp",
    "https://img.example/page-2.webp",
  ]);
});

// 方法说明：验证未产生自然尺寸的懒加载图片仍能按内容区域被发现。
test("discovers unloaded chapter images with real lazy-load URLs", async () => {
  const image = {
    dataset: { src: "/images/unloaded.webp" },
    naturalWidth: 0,
    naturalHeight: 0,
    complete: false,
    classList: { contains() { return false; } },
    // 方法说明：模拟尚未设置尺寸属性的懒加载图片。
    getAttribute() { return null; },
    // 方法说明：模拟尚未参与布局的图片。
    getBoundingClientRect() { return { width: 0, height: 0 }; },
  };
  const document = {
    // 方法说明：只在漫画内容容器选择器中返回测试图片。
    querySelectorAll(selector) {
      return selector === ".comicContent-list img" ? [image] : [];
    },
  };
  const adapter = new CopyMangaAdapter(
    document,
    new URL("https://www.mangacopy.com/comic/work/chapter/current"),
  );

  assert.deepEqual(await adapter.getChapterImageUrls(), [
    "https://www.mangacopy.com/images/unloaded.webp",
  ]);
});

// 方法说明：验证章节容器中的站点广告图不会被误识别为漫画页。
test("filters CopyManga advertisement images from chapter pages", async () => {
  const comicImage = {
    dataset: { src: "/images/page-1.webp" },
    naturalWidth: 1125,
    naturalHeight: 1600,
    complete: true,
    classList: { contains() { return false; } },
    getAttribute() { return null; },
    getBoundingClientRect() { return { width: 1125, height: 1600 }; },
  };
  const adImage = {
    dataset: { src: "https://s3.mangafunb.fun/static/ads/tsugumomo800_130.jpg" },
    naturalWidth: 800,
    naturalHeight: 130,
    complete: true,
    classList: { contains() { return false; } },
    getAttribute() { return null; },
    getBoundingClientRect() { return { width: 800, height: 130 }; },
  };
  const document = {
    querySelectorAll(selector) {
      return selector === ".comicContent-list img" ? [comicImage, adImage] : [];
    },
  };
  const adapter = new CopyMangaAdapter(
    document,
    new URL("https://www.mangacopy.com/comic/work/chapter/current"),
  );

  assert.deepEqual(adapter.findImages(), [comicImage]);
});

// 方法说明：验证密文与密钥可以位于不同的章节内联脚本中。
test("extracts CopyManga encrypted chapter fields", () => {
  const document = scriptDocument(
    "var cct = 'op0zzpvv.nmn.00p';",
    "var contentKey = '1234567890abcdef0011';",
  );

  assert.deepEqual(extractEncryptedChapter(document), {
    contentKey: "1234567890abcdef0011",
    cipherKey: "op0zzpvv.nmn.00p",
  });
});

// 方法说明：验证镜像站仅在章节阅读路径中启用适配器。
test("matches CopyManga mirror chapter pages only", () => {
  assert.equal(
    CopyMangaAdapter.matches(
      new URL("https://www.copy3000.com/comic/work/chapter/current"),
    ),
    true,
  );
  assert.equal(
    CopyMangaAdapter.matches(
      new URL("https://www.mangacopy.com/rank?type=male&table=month"),
    ),
    false,
  );
  assert.equal(
    CopyMangaAdapter.matches(new URL("https://www.copy3000.com/comic/work")),
    false,
  );
});

// 方法说明：验证章节号和站点后缀不会污染作品规范标题。
test("normalizes CopyManga chapter titles", () => {
  assert.equal(
    normalizeWorkTitle("劍姬神聖譚 - 第02话 - 拷貝漫畫 拷贝漫画"),
    "劍姬神聖譚",
  );
  assert.equal(normalizeWorkTitle("劍姬神聖譚/第02話"), "劍姬神聖譚");
});

// 方法说明：验证作品目录页会提供规范标题、简体别名和作品属性。
test("extracts CopyManga work aliases from the detail page", () => {
  const titleNode = {
    textContent: "劍姬神聖譚",
    getAttribute(name) { return name === "title" ? "劍姬神聖譚" : null; },
  };
  const aliasText = { textContent: "劍姬神聖譚,剑姬神圣谭,剑姬" };
  const aliasRow = {
    textContent: "別名：劍姬神聖譚,剑姬神圣谭,剑姬",
    querySelector() { return aliasText; },
    querySelectorAll() { return []; },
  };
  const authorLink = { textContent: "大森藤ノ" };
  const authorRow = {
    textContent: "作者：大森藤ノ",
    querySelector() { return null; },
    querySelectorAll() { return [authorLink]; },
  };
  const tagLink = { textContent: "#冒險" };
  const cover = {
    dataset: { src: "/cover.webp" },
    currentSrc: "",
    src: "",
  };
  const document = {
    title: "污染标题",
    querySelector(selector) {
      if (selector === ".comicParticulars-title h6") return titleNode;
      if (selector === ".comicParticulars-left-img img") return cover;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".comicParticulars-title li") return [aliasRow, authorRow];
      if (selector.includes(".comicParticulars-tag")) return [tagLink];
      return [];
    },
  };

  assert.deepEqual(
    extractWorkDetails(document, "https://www.copy3000.com/comic/work"),
    {
      title: "劍姬神聖譚",
      title_aliases: ["剑姬神圣谭", "剑姬"],
      author: "大森藤ノ",
      tags: ["冒險"],
      cover_url: "https://www.copy3000.com/cover.webp",
    },
  );
});

// 方法说明：验证适配器优先使用拷贝漫画的稳定下一话链接。
test("resolves the immediate next CopyManga chapter", () => {
  const nextLink = {
    textContent: "下一話",
    href: "",
    // 方法说明：返回测试链接的相对章节地址。
    getAttribute(name) {
      return name === "href" ? "/comic/work/chapter/next" : null;
    },
  };
  const document = {
    // 方法说明：返回下一话测试链接。
    querySelector(selector) {
      return selector === ".comicContent-next a[href]" ? nextLink : null;
    },
    // 方法说明：返回空的候选链接集合。
    querySelectorAll() {
      return [];
    },
  };
  const adapter = new CopyMangaAdapter(
    document,
    new URL("https://www.mangacopy.com/comic/work/chapter/current"),
  );

  assert.equal(
    adapter.getNextChapterUrl(),
    "https://www.mangacopy.com/comic/work/chapter/next",
  );
});

// 方法说明：验证适配器能连续发现指定数量的后续章节。
test("loads multiple following CopyManga chapters", async () => {
  const documents = {
    "chapter-2": chapterDocument("chapter-2", "/comic/work/chapter/chapter-3"),
    "chapter-3": chapterDocument("chapter-3"),
  };
  globalThis.DOMParser = class {
    // 方法说明：按测试响应标识返回对应章节文档。
    parseFromString(value) { return documents[value]; }
  };
  globalThis.fetch = async (url) => {
    const chapterId = String(url).split("/").at(-1);
    return {
      ok: true,
      status: 200,
      url: String(url),
      // 方法说明：返回供测试解析器定位章节的响应正文。
      async text() { return chapterId; },
    };
  };
  const adapter = new CopyMangaAdapter(
    chapterDocument("chapter-1", "/comic/work/chapter/chapter-2"),
    new URL("https://www.mangacopy.com/comic/work/chapter/chapter-1"),
  );

  const chapters = await adapter.loadFollowingChapters(2);

  assert.deepEqual(chapters.map((entry) => entry.chapter.chapter_id), [
    "chapter-2",
    "chapter-3",
  ]);
});

// 方法说明：验证下一话首次返回空图片列表时会短暂重试并恢复。
test("retries a following chapter when images are temporarily missing", async () => {
  const emptyDocument = {
    title: "chapter-2",
    // 方法说明：返回章节标题且没有下一话链接。
    querySelector(selector) {
      if (selector.includes("[class*='chapter']")) {
        return { textContent: "chapter-2" };
      }
      return null;
    },
    // 方法说明：模拟暂时缺少图片和章节脚本的响应。
    querySelectorAll() { return []; },
  };
  const readyDocument = chapterDocument("chapter-2");
  globalThis.DOMParser = class {
    // 方法说明：根据响应正文返回空页面或完整章节。
    parseFromString(value) {
      return value === "empty" ? emptyDocument : readyDocument;
    }
  };
  let attempts = 0;
  globalThis.fetch = async (url) => {
    attempts += 1;
    return {
      ok: true,
      status: 200,
      url: String(url),
      // 方法说明：第一次缺图，第二次返回完整章节。
      async text() { return attempts === 1 ? "empty" : "ready"; },
    };
  };
  const adapter = new CopyMangaAdapter(
    chapterDocument("chapter-1", "/comic/work/chapter/chapter-2"),
    new URL("https://www.mangacopy.com/comic/work/chapter/chapter-1"),
  );

  const chapters = await adapter.loadFollowingChapters(1);

  assert.equal(attempts, 2);
  assert.deepEqual(chapters[0].imageUrls, [
    "https://www.mangacopy.com/images/chapter-2.webp",
  ]);
});

// 方法说明：验证循环下一话链接会停止而不会重复抓取。
test("stops following chapters when a link loops", async () => {
  globalThis.DOMParser = class {
    // 方法说明：返回指回当前话的循环测试文档。
    parseFromString() {
      return chapterDocument("chapter-2", "/comic/work/chapter/chapter-1");
    }
  };
  globalThis.fetch = async (url) => ({
    ok: true,
    status: 200,
    url: String(url),
    // 方法说明：返回循环章节测试正文。
    async text() { return "chapter-2"; },
  });
  const adapter = new CopyMangaAdapter(
    chapterDocument("chapter-1", "/comic/work/chapter/chapter-2"),
    new URL("https://www.mangacopy.com/comic/work/chapter/chapter-1"),
  );

  assert.equal((await adapter.loadFollowingChapters(5)).length, 1);
});

// 方法说明：验证跨域下一话链接在发起请求前被拒绝。
test("rejects cross-origin following chapter links", async () => {
  const adapter = new CopyMangaAdapter(
    chapterDocument("chapter-1", "https://example.com/chapter-2"),
    new URL("https://www.mangacopy.com/comic/work/chapter/chapter-1"),
  );

  await assert.rejects(
    adapter.loadFollowingChapters(1),
    /跨域/,
  );
});
