const TRANSPORT_VERSION = "gzip-v1";

// 方法说明：把 FormData 序列化成可压缩的 multipart 字节并保留边界声明。
async function serializeFormData(form) {
  const request = new Request("http://comic-enhancer.invalid/transport", {
    method: "POST",
    body: form,
  });
  return {
    body: new Uint8Array(await request.arrayBuffer()),
    contentType: request.headers.get("content-type") || "multipart/form-data",
  };
}

// 方法说明：使用浏览器原生 gzip 流压缩字节，失败时返回空值让调用方回退。
async function gzipBytes(bytes) {
  if (typeof CompressionStream !== "function") return null;
  try {
    const stream = new Blob([bytes])
      .stream()
      .pipeThrough(new CompressionStream("gzip"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  } catch {
    return null;
  }
}

// 方法说明：编码 multipart 请求并在压缩有效时附加传输层标识。
export async function encodeMultipartForm(form) {
  const serialized = await serializeFormData(form);
  const compressed = await gzipBytes(serialized.body);
  if (!compressed || compressed.byteLength >= serialized.body.byteLength) {
    return {
      body: form,
      headers: {},
      compressed: false,
      originalBytes: serialized.body.byteLength,
      transmittedBytes: serialized.body.byteLength,
      transportVersion: "plain",
    };
  }
  return {
    body: compressed,
    headers: {
      "Content-Type": serialized.contentType,
      "Content-Encoding": "gzip",
      "X-Comic-Enhancer-Transport": TRANSPORT_VERSION,
    },
    compressed: true,
    originalBytes: serialized.body.byteLength,
    transmittedBytes: compressed.byteLength,
    transportVersion: TRANSPORT_VERSION,
  };
}

// 方法说明：发送使用独立传输层编码的 multipart 请求并保留调用方请求头。
export async function postMultipart(url, form, options = {}) {
  const encoded = await encodeMultipartForm(form);
  const headers = new Headers(options.headers || {});
  for (const [name, value] of Object.entries(encoded.headers)) {
    headers.set(name, value);
  }
  return fetch(url, {
    ...options,
    method: "POST",
    headers,
    body: encoded.body,
  });
}

export { TRANSPORT_VERSION };
