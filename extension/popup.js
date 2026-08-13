import { describeModelTier } from "./model-status.js";

const REMOTE_API_URL = "http://192.168.38.226:8765";
const LOCAL_API_URL = "http://127.0.0.1:8765";

const PROFILES = Object.freeze({
  "remote-fast": {
    title: "远端增强服务 · 快速",
    detail: "10 步快速上色，预处理后续 3 页",
    endpoint: "remote",
    mode: "fast",
    prefetchPages: 3,
  },
  "remote-quality": {
    title: "远端增强服务 · 质量",
    detail: "16 步质量上色，优先作品 LoRA 并预处理后续 2 页",
    endpoint: "remote",
    mode: "quality",
    prefetchPages: 2,
  },
  "local-fast": {
    title: "本机服务 · 快速",
    detail: "连接本机 8765 端口，预处理后续 3 页",
    apiBaseUrl: LOCAL_API_URL,
    mode: "fast",
    prefetchPages: 3,
  },
  "local-quality": {
    title: "本机服务 · 质量",
    detail: "连接本机 8765 端口，预处理后续 2 页",
    apiBaseUrl: LOCAL_API_URL,
    mode: "quality",
    prefetchPages: 2,
  },
});

const DEFAULTS = Object.freeze({
  enabled: true,
  profile: "remote-fast",
  apiBaseUrl: REMOTE_API_URL,
  apiToken: "",
  mode: "fast",
  prefetchPages: 3,
  preferWorkAdapter: true,
  allowGenericAdapter: true,
  remoteApiBaseUrl: REMOTE_API_URL,
  customApiBaseUrl: "",
  customMode: "fast",
});

const elements = {
  enabled: document.getElementById("enabled"),
  profile: document.getElementById("profile"),
  apiToken: document.getElementById("apiToken"),
  remoteFields: document.getElementById("remoteFields"),
  remoteApiBaseUrl: document.getElementById("remoteApiBaseUrl"),
  customFields: document.getElementById("customFields"),
  customApiBaseUrl: document.getElementById("customApiBaseUrl"),
  customMode: document.getElementById("customMode"),
  profileSummary: document.getElementById("profileSummary"),
  save: document.getElementById("save"),
  status: document.getElementById("status"),
  modelTier: document.getElementById("modelTier"),
  modelDetail: document.getElementById("modelDetail"),
};

let storedSettings = { ...DEFAULTS };
let lastModelExecution = null;
let serviceCapabilities = null;

elements.profile.addEventListener("change", renderSelection);
elements.remoteApiBaseUrl.addEventListener("input", renderSelection);
elements.customMode.addEventListener("change", renderSelection);
elements.save.addEventListener("click", save);
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes.lastModelExecution) return;
  lastModelExecution = changes.lastModelExecution.newValue || null;
  renderModelTier(resolveSettings(storedSettings));
});
load();

async function load() {
  const raw = await chrome.storage.local.get(null);
  const stored = { ...DEFAULTS, ...raw };
  storedSettings = stored;
  lastModelExecution = raw.lastModelExecution || null;
  const profile = inferProfile(raw);
  elements.enabled.checked = Boolean(stored.enabled);
  elements.profile.value = profile;
  elements.apiToken.value = stored.apiToken || "";
  elements.remoteApiBaseUrl.value = stored.remoteApiBaseUrl || REMOTE_API_URL;
  elements.customApiBaseUrl.value =
    stored.customApiBaseUrl || (profile === "custom" ? stored.apiBaseUrl : "");
  elements.customMode.value = stored.customMode || stored.mode || "fast";
  renderSelection();
  await checkService(resolveSettings(stored));
}

function inferProfile(settings) {
  if (settings.profile && (settings.profile === "custom" || PROFILES[settings.profile])) {
    return settings.profile;
  }
  const url = normalizeUrl(settings.apiBaseUrl || "");
  const mode = settings.mode === "quality" ? "quality" : "fast";
  if (url === REMOTE_API_URL) return `remote-${mode}`;
  if (url === LOCAL_API_URL || url === "http://localhost:8765") return `local-${mode}`;
  return "custom";
}

function renderProfile() {
  const profileId = elements.profile.value;
  const custom = profileId === "custom";
  const remote = PROFILES[profileId]?.endpoint === "remote";
  elements.remoteFields.hidden = !remote;
  elements.customFields.hidden = !custom;
  const profile = custom
    ? {
        title: `自定义服务 · ${elements.customMode.value === "quality" ? "质量" : "快速"}`,
        detail: "使用指定服务地址，自动启用作品 LoRA 回退和页面预处理",
      }
    : PROFILES[profileId];
  elements.profileSummary.innerHTML = `<strong>${profile.title}</strong>${profile.detail}`;
}

function renderSelection() {
  renderProfile();
  renderModelTier(resolveSettings(storedSettings));
}

function renderModelTier(settings) {
  const view = describeModelTier(
    settings,
    lastModelExecution,
    serviceCapabilities,
  );
  elements.modelTier.textContent = view.title;
  elements.modelDetail.textContent = view.detail;
  elements.modelTier.dataset.state = view.state;
}

async function save() {
  elements.save.disabled = true;
  setStatus("正在保存配置", "checking");
  try {
    const current = await chrome.storage.local.get(DEFAULTS);
    const settings = resolveSettings(current);
    if (!settings.apiBaseUrl) throw new Error("请输入自定义服务地址");
    if (!settings.apiToken) throw new Error("请输入 API Token");

    const granted = await chrome.permissions.request({
      origins: ["https://*/*", "http://*/*"],
    });
    if (!granted) throw new Error("需要漫画图片读取权限");

    await chrome.storage.local.set(settings);
    storedSettings = settings;
    await checkService(settings);
  } catch (error) {
    setStatus(error instanceof Error ? error.message : String(error), "failed");
  } finally {
    elements.save.disabled = false;
  }
}

function resolveSettings(current = DEFAULTS) {
  const profileId = elements.profile.value;
  const preset = PROFILES[profileId];
  const customMode = elements.customMode.value === "quality" ? "quality" : "fast";
  const mode = preset?.mode || customMode;
  const apiBaseUrl = normalizeUrl(
    preset?.endpoint === "remote"
      ? elements.remoteApiBaseUrl.value
      : preset?.apiBaseUrl || elements.customApiBaseUrl.value,
  );
  return {
    ...current,
    enabled: elements.enabled.checked,
    profile: profileId,
    apiBaseUrl,
    apiToken: elements.apiToken.value.trim(),
    mode,
    prefetchPages: preset?.prefetchPages || (mode === "quality" ? 2 : 3),
    preferWorkAdapter: true,
    allowGenericAdapter: true,
    remoteApiBaseUrl: normalizeUrl(elements.remoteApiBaseUrl.value),
    customApiBaseUrl: normalizeUrl(elements.customApiBaseUrl.value),
    customMode,
  };
}

async function checkService(settings) {
  if (!settings.apiToken) {
    serviceCapabilities = null;
    setStatus("等待填写 API Token", "failed");
    renderModelTier(settings);
    return;
  }
  setStatus("正在检查推理服务", "checking");
  const started = performance.now();
  try {
    const response = await fetch(`${settings.apiBaseUrl}/v1/capabilities`, {
      headers: { Authorization: `Bearer ${settings.apiToken}` },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const capabilities = await response.json();
    serviceCapabilities = capabilities;
    const elapsed = Math.round(performance.now() - started);
    setStatus(`已连接 · ${capabilities.backend} · ${elapsed}ms`, "connected");
    renderModelTier(settings);
  } catch (error) {
    serviceCapabilities = null;
    setStatus(`连接失败 · ${error instanceof Error ? error.message : error}`, "failed");
    renderModelTier(settings);
  }
}

function setStatus(message, state) {
  elements.status.textContent = message;
  elements.status.dataset.state = state;
}

function normalizeUrl(value) {
  return String(value || "").trim().replace(/\/$/, "");
}
