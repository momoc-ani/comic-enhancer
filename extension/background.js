const DEFAULT_SETTINGS = Object.freeze({
  enabled: true,
  profile: "remote-fast",
  apiBaseUrl: "http://192.168.38.226:8765",
  apiToken: "",
  mode: "fast",
  prefetchPages: 3,
  preferWorkAdapter: true,
  allowGenericAdapter: true,
});

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get(null);
  const profile = stored.profile || inferLegacyProfile(stored);
  await chrome.storage.local.set({ ...DEFAULT_SETTINGS, ...stored, profile });
});

function inferLegacyProfile(settings) {
  const url = String(settings.apiBaseUrl || "").replace(/\/$/, "");
  const mode = settings.mode === "quality" ? "quality" : "fast";
  if (url === "http://192.168.38.226:8765") return `remote-${mode}`;
  if (url === "http://127.0.0.1:8765" || url === "http://localhost:8765") {
    return `local-${mode}`;
  }
  return url ? "custom" : "remote-fast";
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "COMIC_ENHANCER_SETTINGS") {
    chrome.storage.local.get(DEFAULT_SETTINGS).then(sendResponse);
    return true;
  }

  if (message?.type === "COMIC_ENHANCER_PROCESS") {
    processPage(message.payload).then(
      (result) => sendResponse({ ok: true, result }),
      (error) => sendResponse({ ok: false, error: normalizeError(error) }),
    );
    return true;
  }

  if (message?.type === "COMIC_ENHANCER_ANALYZE") {
    analyzePages(message.payload).then(
      (result) => sendResponse({ ok: true, result }),
      (error) => sendResponse({ ok: false, error: normalizeError(error) }),
    );
    return true;
  }

  return false;
});

async function processPage(payload) {
  const settings = await chrome.storage.local.get(DEFAULT_SETTINGS);
  if (!settings.enabled) {
    throw new Error("漫画增强功能已关闭");
  }

  const imageOrigin = new URL(payload.imageUrl).origin;
  const hasImageAccess = await chrome.permissions.contains({
    origins: [`${imageOrigin}/*`],
  });
  if (!hasImageAccess) {
    throw new Error("缺少漫画图片域名权限，请打开插件并保存设置");
  }

  const sourceResponse = await fetch(payload.imageUrl, {
    credentials: "include",
    cache: "force-cache",
  });
  if (!sourceResponse.ok) {
    throw new Error(`原图下载失败：${sourceResponse.status}`);
  }

  const sourceBlob = await sourceResponse.blob();
  const form = new FormData();
  form.append("image", sourceBlob, `page-${payload.options.page_index}.img`);
  form.append("work_json", JSON.stringify(payload.work));
  form.append(
    "options_json",
    JSON.stringify({
      ...payload.options,
      mode: settings.mode,
      prefer_work_adapter: settings.preferWorkAdapter,
      allow_generic_adapter: settings.allowGenericAdapter,
    }),
  );

  const apiBaseUrl = settings.apiBaseUrl.replace(/\/$/, "");
  const response = await fetch(`${apiBaseUrl}/v1/pages/process`, {
    method: "POST",
    headers: { Authorization: `Bearer ${settings.apiToken}` },
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`推理服务失败：${response.status} ${detail}`);
  }

  const result = await response.json();
  const imageResponse = await fetch(`${apiBaseUrl}${result.result_url}`, {
    headers: { Authorization: `Bearer ${settings.apiToken}` },
  });
  if (!imageResponse.ok) {
    throw new Error(`结果图片下载失败：${imageResponse.status}`);
  }

  return {
    ...result,
    image_data_url: await responseToDataUrl(imageResponse),
  };
}

async function analyzePages(payload) {
  const settings = await chrome.storage.local.get(DEFAULT_SETTINGS);
  if (!settings.enabled || settings.mode !== "quality") return null;
  const form = new FormData();
  for (const [index, imageUrl] of payload.imageUrls.entries()) {
    const sourceResponse = await fetch(imageUrl, {
      credentials: "include",
      cache: "force-cache",
    });
    if (!sourceResponse.ok) {
      throw new Error(`分析原图下载失败：${sourceResponse.status}`);
    }
    form.append("pages", await sourceResponse.blob(), `page-${index}.img`);
  }
  form.append("work_json", JSON.stringify(payload.work));
  const apiBaseUrl = settings.apiBaseUrl.replace(/\/$/, "");
  const response = await fetch(`${apiBaseUrl}/v1/pages/analyze`, {
    method: "POST",
    headers: { Authorization: `Bearer ${settings.apiToken}` },
    body: form,
  });
  if (response.status === 409) return null;
  if (!response.ok) {
    throw new Error(`人物分析失败：${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function responseToDataUrl(response) {
  const bytes = new Uint8Array(await response.arrayBuffer());
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return `data:${response.headers.get("content-type") || "image/webp"};base64,${btoa(binary)}`;
}

function normalizeError(error) {
  if (error instanceof Error) return error.message;
  return String(error);
}

export { DEFAULT_SETTINGS };
