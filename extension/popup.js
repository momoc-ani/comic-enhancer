const REMOTE_API_URL = "http://192.168.38.226:8765";
const LOCAL_API_URL = "http://127.0.0.1:8765";

const PROFILES = Object.freeze({
  "remote-fast": {
    title: "远端 RTX 4090 · 快速",
    detail: "10 步快速上色，预处理后续 3 页",
    apiBaseUrl: REMOTE_API_URL,
    mode: "fast",
    prefetchPages: 3,
  },
  "remote-quality": {
    title: "远端 RTX 4090 · 质量",
    detail: "16 步质量上色，优先作品 LoRA 并预处理后续 2 页",
    apiBaseUrl: REMOTE_API_URL,
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
  customApiBaseUrl: "",
  customMode: "fast",
});

const elements = {
  enabled: document.getElementById("enabled"),
  profile: document.getElementById("profile"),
  apiToken: document.getElementById("apiToken"),
  customFields: document.getElementById("customFields"),
  customApiBaseUrl: document.getElementById("customApiBaseUrl"),
  customMode: document.getElementById("customMode"),
  profileSummary: document.getElementById("profileSummary"),
  save: document.getElementById("save"),
  status: document.getElementById("status"),
};

elements.profile.addEventListener("change", renderProfile);
elements.customMode.addEventListener("change", renderProfile);
elements.save.addEventListener("click", save);
load();

async function load() {
  const raw = await chrome.storage.local.get(null);
  const stored = { ...DEFAULTS, ...raw };
  const profile = inferProfile(raw);
  elements.enabled.checked = Boolean(stored.enabled);
  elements.profile.value = profile;
  elements.apiToken.value = stored.apiToken || "";
  elements.customApiBaseUrl.value =
    stored.customApiBaseUrl || (profile === "custom" ? stored.apiBaseUrl : "");
  elements.customMode.value = stored.customMode || stored.mode || "fast";
  renderProfile();
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
  elements.customFields.hidden = !custom;
  const profile = custom
    ? {
        title: `自定义服务 · ${elements.customMode.value === "quality" ? "质量" : "快速"}`,
        detail: "使用指定服务地址，自动启用作品 LoRA 回退和页面预处理",
      }
    : PROFILES[profileId];
  elements.profileSummary.innerHTML = `<strong>${profile.title}</strong>${profile.detail}`;
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
    preset?.apiBaseUrl || elements.customApiBaseUrl.value,
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
    customApiBaseUrl: normalizeUrl(elements.customApiBaseUrl.value),
    customMode,
  };
}

async function checkService(settings) {
  if (!settings.apiToken) {
    setStatus("等待填写 API Token", "failed");
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
    const elapsed = Math.round(performance.now() - started);
    setStatus(`已连接 · ${capabilities.backend} · ${elapsed}ms`, "connected");
  } catch (error) {
    setStatus(`连接失败 · ${error instanceof Error ? error.message : error}`, "failed");
  }
}

function setStatus(message, state) {
  elements.status.textContent = message;
  elements.status.dataset.state = state;
}

function normalizeUrl(value) {
  return String(value || "").trim().replace(/\/$/, "");
}
