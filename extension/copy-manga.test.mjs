import test from "node:test";
import assert from "node:assert/strict";

await import("./copy-manga.js");

const {
  CopyMangaAdapter,
  decryptChapterImageUrls,
  extractEncryptedChapter,
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
