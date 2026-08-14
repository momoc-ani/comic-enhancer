import { describeModelTier } from "./model-status.js";
import {
  DEFAULT_SETTINGS,
  LOCAL_API_URL,
  MODE_OPTIONS,
  REMOTE_API_URL,
  activateDeployment,
  migrateSettings,
  normalizeMode,
  normalizeUrl,
} from "./settings.js";

const elements = {
  enabled: document.getElementById("enabled"),
  deploymentTabs: [...document.querySelectorAll(".deployment-tab")],
  mode: document.getElementById("mode"),
  apiBaseUrl: document.getElementById("apiBaseUrl"),
  apiBaseUrlLabel: document.getElementById("apiBaseUrlLabel"),
  apiToken: document.getElementById("apiToken"),
  save: document.getElementById("save"),
  status: document.getElementById("status"),
  modelTier: document.getElementById("modelTier"),
  modelDetail: document.getElementById("modelDetail"),
  extensionVersion: document.getElementById("extensionVersion"),
};

let activeDeployment = "remote";
let storedSettings = { ...DEFAULT_SETTINGS };
let drafts = {
  remote: { apiBaseUrl: REMOTE_API_URL, apiToken: "", mode: "fast" },
  local: { apiBaseUrl: LOCAL_API_URL, apiToken: "", mode: "fast" },
};
let lastModelExecution = null;
const capabilityCache = new Map();
let activeModeOptions = [...MODE_OPTIONS];

elements.extensionVersion.textContent = `v${chrome.runtime.getManifest().version}`;
elements.deploymentTabs.forEach((tab) => {
  tab.addEventListener("click", () => switchDeployment(tab.dataset.deployment));
});
elements.mode.addEventListener("change", renderSelection);
elements.apiBaseUrl.addEventListener("input", markConnectionDirty);
elements.apiToken.addEventListener("input", markConnectionDirty);
elements.save.addEventListener("click", save);
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes.lastModelExecution) return;
  lastModelExecution = changes.lastModelExecution.newValue || null;
  renderModelTier(resolveSettings(storedSettings));
});

load();

async function load() {
  const raw = await chrome.storage.local.get(null);
  storedSettings = migrateSettings(raw);
  lastModelExecution = raw.lastModelExecution || null;
  activeDeployment = storedSettings.deployment;
  drafts = {
    remote: {
      apiBaseUrl: storedSettings.remoteApiBaseUrl,
      apiToken: storedSettings.remoteApiToken,
      mode: storedSettings.remoteMode,
    },
    local: {
      apiBaseUrl: storedSettings.localApiBaseUrl,
      apiToken: storedSettings.localApiToken,
      mode: storedSettings.localMode,
    },
  };
  elements.enabled.checked = Boolean(storedSettings.enabled);
  populateActiveForm();
  await checkService(resolveSettings(storedSettings));
}

function snapshotActiveForm() {
  drafts[activeDeployment] = {
    apiBaseUrl: normalizeUrl(elements.apiBaseUrl.value),
    apiToken: elements.apiToken.value.trim(),
    mode: normalizeMode(elements.mode.value),
  };
}

function populateActiveForm() {
  const draft = drafts[activeDeployment];
  elements.mode.value = draft.mode;
  elements.apiBaseUrl.value = draft.apiBaseUrl;
  elements.apiToken.value = draft.apiToken;
  elements.apiBaseUrlLabel.textContent =
    activeDeployment === "remote" ? "远端服务地址" : "本地服务地址";
  elements.apiBaseUrl.placeholder =
    activeDeployment === "remote" ? REMOTE_API_URL : LOCAL_API_URL;
  elements.deploymentTabs.forEach((tab) => {
    const selected = tab.dataset.deployment === activeDeployment;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  renderSelection();
}

async function switchDeployment(deployment) {
  if (!["remote", "local"].includes(deployment) || deployment === activeDeployment) {
    return;
  }
  snapshotActiveForm();
  activeDeployment = deployment;
  populateActiveForm();
  await checkService(resolveSettings(storedSettings));
}

function renderSelection() {
  const settings = resolveSettings(storedSettings);
  const capabilities = cachedCapabilities(settings);
  applyModeAvailability(capabilities);
  renderModelTier(settings, capabilities);
}

function renderModelTier(settings, capabilities = cachedCapabilities(settings)) {
  const view = describeModelTier(settings, lastModelExecution, capabilities);
  elements.modelTier.textContent = view.title;
  elements.modelDetail.textContent = view.detail;
  elements.modelTier.dataset.state = view.state;
}

function applyModeAvailability(capabilities) {
  if (Array.isArray(capabilities?.mode_options)) {
    const dynamicOptions = capabilities.mode_options
      .filter((option) => option && option.value && option.label)
      .map((option) => ({
        value: String(option.value),
        label: String(option.label),
        prefetchPages: Number(option.prefetch_pages) || 1,
      }));
    if (dynamicOptions.length > 0) {
      activeModeOptions = dynamicOptions;
      const selected = elements.mode.value;
      elements.mode.replaceChildren(
        ...activeModeOptions.map((option) => {
          const element = document.createElement("option");
          element.value = option.value;
          element.textContent = option.label;
          return element;
        }),
      );
      elements.mode.value = activeModeOptions.some((option) => option.value === selected)
        ? selected
        : activeModeOptions[0].value;
    }
  }
  const available = new Set(capabilities?.processing_modes || []);
  for (const option of elements.mode.options) {
    option.disabled = Boolean(capabilities) && !available.has(option.value);
  }
}

function markConnectionDirty() {
  snapshotActiveForm();
  renderSelection();
  setStatus("配置已修改", "checking");
}

async function save() {
  elements.save.disabled = true;
  setStatus("正在连接服务", "checking");
  try {
    const settings = resolveSettings(storedSettings);
    if (!settings.apiBaseUrl) throw new Error("请输入漫画增强服务地址");
    if (!settings.apiToken) throw new Error("请输入 API Token");

    const granted = await chrome.permissions.request({
      origins: ["https://*/*", "http://*/*"],
    });
    if (!granted) throw new Error("需要漫画图片读取权限");

    await checkService(settings, { strict: true });
    await chrome.storage.local.set(settings);
    storedSettings = settings;
    await chrome.runtime.sendMessage({
      type: "COMIC_ENHANCER_REFRESH_TABS",
      settings: {
        enabled: settings.enabled,
        deployment: settings.deployment,
        profile: settings.profile,
        apiBaseUrl: settings.apiBaseUrl,
        mode: settings.mode,
        prefetchPages: settings.prefetchPages,
        preferWorkAdapter: settings.preferWorkAdapter,
        allowGenericAdapter: settings.allowGenericAdapter,
      },
    });
  } catch (error) {
    setStatus(error instanceof Error ? error.message : String(error), "failed");
  } finally {
    elements.save.disabled = false;
  }
}

function resolveSettings(current = DEFAULT_SETTINGS) {
  snapshotActiveForm();
  const merged = {
    ...current,
    enabled: elements.enabled.checked,
    remoteApiBaseUrl: drafts.remote.apiBaseUrl,
    remoteApiToken: drafts.remote.apiToken,
    remoteMode: drafts.remote.mode,
    localApiBaseUrl: drafts.local.apiBaseUrl,
    localApiToken: drafts.local.apiToken,
    localMode: drafts.local.mode,
  };
  return activateDeployment(merged, activeDeployment);
}

async function checkService(settings, { strict = false } = {}) {
  if (!settings.apiToken) {
    setStatus("等待填写 API Token", "failed");
    renderModelTier(settings, null);
    return null;
  }

  setStatus("正在检查推理服务", "checking");
  const started = performance.now();
  try {
    const response = await fetch(`${settings.apiBaseUrl}/v1/capabilities`, {
      headers: { Authorization: `Bearer ${settings.apiToken}` },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const capabilities = await response.json();
    capabilityCache.set(capabilityKey(settings), capabilities);
    if (!modeAvailable(capabilities, settings.mode)) {
      throw new Error(`当前服务未启用${modeLabel(settings.mode)}`);
    }
    if (settings.deployment === activeDeployment) {
      const elapsed = Math.round(performance.now() - started);
      setStatus(`已连接 · ${capabilities.backend} · ${elapsed}ms`, "connected");
      applyModeAvailability(capabilities);
      renderModelTier(settings, capabilities);
    }
    return capabilities;
  } catch (error) {
    if (settings.deployment === activeDeployment) {
      applyModeAvailability(cachedCapabilities(settings));
      renderModelTier(settings, cachedCapabilities(settings));
      setStatus(
        `连接失败 · ${error instanceof Error ? error.message : error}`,
        "failed",
      );
    }
    if (strict) throw error;
    return null;
  }
}

function cachedCapabilities(settings) {
  return capabilityCache.get(capabilityKey(settings)) || null;
}

function capabilityKey(settings) {
  return `${normalizeUrl(settings.apiBaseUrl)}\n${settings.apiToken || ""}`;
}

function modeAvailable(capabilities, mode) {
  return (capabilities.processing_modes || []).includes(normalizeMode(mode));
}

function modeLabel(mode) {
  return activeModeOptions.find((option) => option.value === normalizeMode(mode))?.label || mode;
}

function setStatus(message, state) {
  elements.status.textContent = message;
  elements.status.dataset.state = state;
}
