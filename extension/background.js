import { buildModelExecution } from "./model-status.js";
import { DEFAULT_SETTINGS, migrateSettings } from "./settings.js";

const SUPPORTED_PAGE_PATTERNS = Object.freeze([
  "*://*.copymanga.com/*",
  "*://*.copymanga.site/*",
  "*://*.copymanga.tv/*",
  "*://*.mangacopy.com/*",
]);

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get(null);
  await chrome.storage.local.set(migrateSettings(stored));
  await injectIntoOpenMangaTabs();
});

chrome.runtime.onStartup.addListener(() => {
  injectIntoOpenMangaTabs().catch((error) => {
    console.warn("Comic Enhancer startup injection failed:", normalizeError(error));
  });
});

injectIntoOpenMangaTabs().catch((error) => {
  console.warn("Comic Enhancer initial injection failed:", normalizeError(error));
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" || !isSupportedPage(tab.url)) return;
  injectContent(tabId).catch((error) => {
    console.warn("Comic Enhancer injection failed:", normalizeError(error));
  });
});

async function injectIntoOpenMangaTabs() {
  const tabs = await chrome.tabs.query({ url: [...SUPPORTED_PAGE_PATTERNS] });
  await Promise.allSettled(tabs.map((tab) => injectContent(tab.id)));
}

async function injectContent(tabId) {
  if (!Number.isInteger(tabId)) return;
  const existing = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => Boolean(globalThis.__comicEnhancerInjected),
  });
  if (existing.some((result) => result.result === true)) return;
  await chrome.scripting.insertCSS({ target: { tabId }, files: ["content.css"] });
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
}

function isSupportedPage(url) {
  if (!url) return false;
  try {
    return /(^|\.)(copymanga\.(com|site|tv)|mangacopy\.com)$/i.test(
      new URL(url).hostname,
    );
  } catch {
    return false;
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "COMIC_ENHANCER_REFRESH_TABS") {
    refreshOpenMangaTabs(message.settings).then(
      () => sendResponse({ ok: true }),
      (error) => sendResponse({ ok: false, error: normalizeError(error) }),
    );
    return true;
  }

  if (message?.type === "COMIC_ENHANCER_SETTINGS") {
    getActiveSettings().then(sendResponse);
    return true;
  }

  if (message?.type === "COMIC_ENHANCER_PROCESS") {
    processPage(message.payload).then(
      (result) => sendResponse({ ok: true, result }),
      (error) => sendResponse({ ok: false, error: normalizeError(error) }),
    );
    return true;
  }

  return false;
});

async function refreshOpenMangaTabs(settings) {
  const tabs = await chrome.tabs.query({ url: [...SUPPORTED_PAGE_PATTERNS] });
  await Promise.allSettled(
    tabs.map((tab) =>
      Number.isInteger(tab.id)
        ? chrome.tabs.sendMessage(tab.id, {
            type: "COMIC_ENHANCER_REFRESH_SETTINGS",
            settings,
          })
        : Promise.resolve(),
    ),
  );
}

async function processPage(payload) {
  const settings = await getActiveSettings();
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

  await chrome.storage.local.set({
    lastModelExecution: buildModelExecution(result, settings),
  });

  return {
    ...result,
    image_data_url: await responseToDataUrl(imageResponse),
  };
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

async function getActiveSettings() {
  return migrateSettings(await chrome.storage.local.get(null));
}

export { DEFAULT_SETTINGS, isSupportedPage };
