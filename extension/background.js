import { buildModelExecution } from "./model-status.js";
import { DEFAULT_SETTINGS, migrateSettings } from "./settings.js";

const SUPPORTED_PAGE_PATTERNS = Object.freeze([
  "*://*.copymanga.com/comic/*/chapter/*",
  "*://*.copymanga.site/comic/*/chapter/*",
  "*://*.copymanga.tv/comic/*/chapter/*",
  "*://*.mangacopy.com/comic/*/chapter/*",
  "*://*.copy3000.com/comic/*/chapter/*",
]);

const COPY_MANGA_HOST_PATTERN =
  /(^|\.)(copymanga\.(com|site|tv)|mangacopy\.com|copy3000\.com)$/i;
const COPY_MANGA_CHAPTER_PATH_PATTERN =
  /^\/comic\/[^/]+\/chapter\/[^/]+\/?$/i;

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

// 方法说明：向所有已打开的受支持漫画页注入内容脚本。
async function injectIntoOpenMangaTabs() {
  const tabs = await chrome.tabs.query({ url: [...SUPPORTED_PAGE_PATTERNS] });
  await Promise.allSettled(tabs.map((tab) => injectContent(tab.id)));
}

// 方法说明：避免重复地向指定标签页注入扩展资源。
async function injectContent(tabId) {
  if (!Number.isInteger(tabId)) return;
  const existing = await chrome.scripting.executeScript({
    target: { tabId },
    func: () =>
      Boolean(
        globalThis.__comicEnhancerInjected && globalThis.ComicEnhancerCopyManga,
      ),
  });
  if (existing.some((result) => result.result === true)) return;
  await chrome.scripting.insertCSS({ target: { tabId }, files: ["content.css"] });
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["copy-manga.js", "content.js"],
  });
}

// 方法说明：判断地址是否属于受支持的拷贝漫画章节阅读页。
function isSupportedPage(url) {
  if (!url) return false;
  try {
    const pageUrl = new URL(url);
    return (
      COPY_MANGA_HOST_PATTERN.test(pageUrl.hostname) &&
      COPY_MANGA_CHAPTER_PATH_PATTERN.test(pageUrl.pathname)
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

// 方法说明：向已打开的漫画页广播最新设置。
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

// 方法说明：下载原图、调用增强 API 并返回可显示结果。
async function processPage(payload) {
  const settings = await getActiveSettings();
  if (!settings.enabled) {
    throw new Error("漫画增强功能已关闭");
  }

  const options = {
    ...payload.options,
    mode: settings.mode,
  };
  let cachedResult = null;
  const cacheStarted = performance.now();
  try {
    cachedResult = await resolveChapterCache(payload, options, settings);
  } catch (error) {
    console.warn(
      "功能=章节缓存查询 参数=" +
        JSON.stringify({
          章节: payload.chapter?.chapter_id || "",
          章节标题: payload.chapter?.title || "",
          页码: options.page_index + 1,
          模式: settings.mode,
        }) +
        " 结果=" + JSON.stringify({ 状态: "回退生成", 错误: normalizeError(error) }) +
        ` 耗时_ms=${Math.round(performance.now() - cacheStarted)}`,
    );
  }
  if (cachedResult) {
    if (payload.prefetchOnly) return cachedResult;
    return downloadResult(cachedResult, settings);
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
  if (payload.prefetchOnly) {
    form.append("chapter_json", JSON.stringify(payload.chapter || {}));
    form.append("page_count", String(payload.pageCount || 1));
    form.append("priority", String(payload.priority ?? 100));
  }
  form.append(
    "options_json",
    JSON.stringify(options),
  );

  const apiBaseUrl = settings.apiBaseUrl.replace(/\/$/, "");
  const endpoint = payload.prefetchOnly
    ? "/v1/pregeneration/pages"
    : "/v1/pages/process";
  const response = await fetch(`${apiBaseUrl}${endpoint}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${settings.apiToken}` },
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`推理服务失败：${response.status} ${detail}`);
  }

  const result = await response.json();
  if (payload.prefetchOnly) return result;

  return downloadResult(result, settings);
}

// 方法说明：按作品、章节、页码和模式查询服务端已完成缓存。
async function resolveChapterCache(payload, options, settings) {
  const started = performance.now();
  const form = new FormData();
  form.append("work_json", JSON.stringify(payload.work));
  form.append("chapter_json", JSON.stringify(payload.chapter || {}));
  form.append("options_json", JSON.stringify(options));
  const apiBaseUrl = settings.apiBaseUrl.replace(/\/$/, "");
  const response = await fetch(`${apiBaseUrl}/v1/pregeneration/cache/resolve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${settings.apiToken}` },
    body: form,
  });
  if (response.status === 404) {
    logCacheLookup(payload, options, "未命中", performance.now() - started);
    return null;
  }
  if (!response.ok) {
    throw new Error(`缓存服务失败：${response.status}`);
  }
  const result = await response.json();
  logCacheLookup(payload, options, "命中", performance.now() - started);
  return result;
}

// 方法说明：输出不包含图片地址和令牌的缓存查询日志。
function logCacheLookup(payload, options, status, elapsedMs) {
  console.info(
    `功能=章节缓存查询 参数=${JSON.stringify({
      章节: payload.chapter?.chapter_id || "",
      章节标题: payload.chapter?.title || "",
      页码: Number(options.page_index) + 1,
      模式: options.mode,
    })} 结果=${JSON.stringify({ 状态: status })} 耗时_ms=${Math.round(elapsedMs)}`,
  );
}

// 方法说明：鉴权下载命中的增强结果并转换为页面可显示的数据地址。
async function downloadResult(result, settings) {
  const apiBaseUrl = settings.apiBaseUrl.replace(/\/$/, "");

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

// 方法说明：将鉴权响应转换为页面可用的数据地址。
async function responseToDataUrl(response) {
  const bytes = new Uint8Array(await response.arrayBuffer());
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return `data:${response.headers.get("content-type") || "image/webp"};base64,${btoa(binary)}`;
}

// 方法说明：将未知异常规范化为可展示消息。
function normalizeError(error) {
  if (error instanceof Error) return error.message;
  return String(error);
}

// 方法说明：读取并迁移当前生效的扩展设置。
async function getActiveSettings() {
  return migrateSettings(await chrome.storage.local.get(null));
}

export { DEFAULT_SETTINGS, isSupportedPage, processPage };
