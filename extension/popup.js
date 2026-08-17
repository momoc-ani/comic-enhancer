import { describeModelTier } from "./model-status.js";
import {
  DEFAULT_SETTINGS,
  LOCAL_API_URL,
  MODE_OPTIONS,
  REMOTE_API_URL,
  activateDeployment,
  migrateSettings,
  normalizeMode,
  normalizePregenerateChapters,
  normalizeUrl,
} from "./settings.js";

const elements = {
  enabled: document.getElementById("enabled"),
  deploymentTabs: [...document.querySelectorAll(".deployment-tab")],
  mode: document.getElementById("mode"),
  comfyuiDirectOutput: document.getElementById("comfyuiDirectOutput"),
  comfyuiDirectOutputField: document.getElementById("comfyuiDirectOutputField"),
  pregenerateChapters: document.getElementById("pregenerateChapters"),
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
elements.comfyuiDirectOutput.addEventListener("change", markConnectionDirty);
elements.pregenerateChapters.addEventListener("input", markConnectionDirty);
elements.apiBaseUrl.addEventListener("input", markConnectionDirty);
elements.apiToken.addEventListener("input", markConnectionDirty);
elements.save.addEventListener("click", save);
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes.lastModelExecution) return;
  lastModelExecution = changes.lastModelExecution.newValue || null;
  renderModelTier(resolveSettings(storedSettings));
});

load();

// 方法说明：加载设置、执行记录并初始化弹窗界面。
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
  elements.comfyuiDirectOutput.checked = Boolean(storedSettings.comfyuiDirectOutput);
  elements.pregenerateChapters.value = String(storedSettings.pregenerateChapters);
  populateActiveForm();
  await checkService(resolveSettings(storedSettings));
}

// 方法说明：保存当前部署页签中的表单草稿。
function snapshotActiveForm() {
  drafts[activeDeployment] = {
    apiBaseUrl: normalizeUrl(elements.apiBaseUrl.value),
    apiToken: elements.apiToken.value.trim(),
    mode: normalizeMode(elements.mode.value),
  };
}

// 方法说明：用当前部署草稿填充弹窗表单。
function populateActiveForm() {
  const draft = drafts[activeDeployment];
  elements.mode.value = draft.mode;
  elements.apiBaseUrl.value = draft.apiBaseUrl;
  elements.apiToken.value = draft.apiToken;
  elements.comfyuiDirectOutput.checked = Boolean(storedSettings.comfyuiDirectOutput);
  elements.pregenerateChapters.value = String(storedSettings.pregenerateChapters);
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

// 方法说明：切换本地或远端部署配置。
async function switchDeployment(deployment) {
  if (!["remote", "local"].includes(deployment) || deployment === activeDeployment) {
    return;
  }
  snapshotActiveForm();
  activeDeployment = deployment;
  populateActiveForm();
  await checkService(resolveSettings(storedSettings));
}

// 方法说明：根据当前选择刷新档位和模型状态。
function renderSelection() {
  const settings = resolveSettings(storedSettings);
  renderComfyuiDirectOutputControl(settings.mode);
  const capabilities = cachedCapabilities(settings);
  applyModeAvailability(capabilities);
  renderModelTier(settings, capabilities);
}

// 方法说明：仅在 Qwen3-VL + FLUX.2 角色档显示原图直出开关。
function renderComfyuiDirectOutputControl(mode) {
  const visible = mode === "flux2_character";
  elements.comfyuiDirectOutputField.hidden = !visible;
  elements.comfyuiDirectOutput.disabled = !visible;
}

// 方法说明：渲染当前模型档位及最近执行详情。
function renderModelTier(settings, capabilities = cachedCapabilities(settings)) {
  const view = describeModelTier(settings, lastModelExecution, capabilities);
  elements.modelTier.textContent = view.title;
  elements.modelDetail.textContent = view.detail;
  elements.modelTier.dataset.state = view.state;
}

// 方法说明：按服务能力更新可选处理档位。
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

// 方法说明：将已修改的连接配置标记为待检查。
function markConnectionDirty() {
  snapshotActiveForm();
  renderSelection();
  setStatus("配置已修改", "checking");
}

// 方法说明：校验、保存设置并通知已打开的漫画页。
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
        pregenerateChapters: settings.pregenerateChapters,
        comfyuiDirectOutput: settings.comfyuiDirectOutput,
      },
    });
  } catch (error) {
    setStatus(error instanceof Error ? error.message : String(error), "failed");
  } finally {
    elements.save.disabled = false;
  }
}

// 方法说明：合并表单草稿并生成当前生效设置。
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
    comfyuiDirectOutput: Boolean(elements.comfyuiDirectOutput.checked),
    pregenerateChapters: normalizePregenerateChapters(
      elements.pregenerateChapters.value,
    ),
  };
  return activateDeployment(merged, activeDeployment);
}

// 方法说明：检查增强服务连接和档位可用性。
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

// 方法说明：读取当前服务对应的能力缓存。
function cachedCapabilities(settings) {
  return capabilityCache.get(capabilityKey(settings)) || null;
}

// 方法说明：生成服务能力缓存的稳定键。
function capabilityKey(settings) {
  return `${normalizeUrl(settings.apiBaseUrl)}\n${settings.apiToken || ""}`;
}

// 方法说明：判断服务是否支持指定处理档位。
function modeAvailable(capabilities, mode) {
  return (capabilities.processing_modes || []).includes(normalizeMode(mode));
}

// 方法说明：返回处理档位的界面名称。
function modeLabel(mode) {
  return activeModeOptions.find((option) => option.value === normalizeMode(mode))?.label || mode;
}

// 方法说明：更新弹窗中的连接状态。
function setStatus(message, state) {
  elements.status.textContent = message;
  elements.status.dataset.state = state;
}
