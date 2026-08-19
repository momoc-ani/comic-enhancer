import test from "node:test";
import assert from "node:assert/strict";
import { gunzipSync } from "node:zlib";

import { encodeMultipartForm } from "./transport.js";

// 方法说明：读取 multipart 序列化结果并验证压缩层只改变传输表示。
test("gzip multipart transport preserves exact request bytes", async () => {
  const image = new Uint8Array([0, 255, 17, 34, 128, 9]);
  const form = new FormData();
  form.append("image", new Blob([image], { type: "image/png" }), "page.png");
  form.append("options_json", '{"mode":"fast"}');

  const encoded = await encodeMultipartForm(form);
  assert.equal(encoded.compressed, true);
  assert.equal(encoded.headers["Content-Encoding"], "gzip");

  const compressed = new Uint8Array(encoded.body);
  const restored = new Uint8Array(gunzipSync(compressed));
  assert.equal(restored.byteLength, encoded.originalBytes);
  const text = new TextDecoder().decode(restored);
  assert.match(text, /name="options_json"/);
  assert.match(text, /\{"mode":"fast"\}/);
  assert.match(text, /name="image"; filename="page\.png"/);
  assert.ok(Buffer.from(restored).includes(Buffer.from(image)));
});

// 方法说明：验证没有压缩流能力时仍返回标准 FormData 请求。
test("multipart transport falls back when CompressionStream is unavailable", async () => {
  const original = globalThis.CompressionStream;
  globalThis.CompressionStream = undefined;
  try {
    const form = new FormData();
    form.append("value", "plain");
    const encoded = await encodeMultipartForm(form);
    assert.equal(encoded.compressed, false);
    assert.equal(encoded.body, form);
    assert.deepEqual(encoded.headers, {});
  } finally {
    globalThis.CompressionStream = original;
  }
});
