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
