const DEFAULTS = {
  enabled: true,
  apiBaseUrl: "http://192.168.38.226:8765",
  apiToken: "",
  mode: "fast",
  prefetchPages: 3,
  preferWorkAdapter: true,
  allowGenericAdapter: true,
};

const ids = Object.keys(DEFAULTS);
const elements = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));
const status = document.getElementById("status");

document.getElementById("save").addEventListener("click", save);
load();

async function load() {
  const settings = await chrome.storage.local.get(DEFAULTS);
  for (const [key, value] of Object.entries(settings)) {
    const element = elements[key];
    if (!element) continue;
    if (element.type === "checkbox") element.checked = Boolean(value);
    else element.value = value;
  }
  await checkService(settings);
}

async function save() {
  const settings = {};
  for (const [key, element] of Object.entries(elements)) {
    settings[key] = element.type === "checkbox" ? element.checked : element.value;
  }
  settings.prefetchPages = Math.max(0, Math.min(8, Number(settings.prefetchPages) || 0));
  settings.apiBaseUrl = settings.apiBaseUrl.replace(/\/$/, "");
  const granted = await chrome.permissions.request({
    origins: ["https://*/*", "http://*/*"],
  });
  if (!granted) {
    status.textContent = "需要漫画图片读取权限";
    return;
  }
  await chrome.storage.local.set(settings);
  await checkService(settings);
}

async function checkService(settings) {
  status.textContent = "检查推理服务...";
  try {
    const response = await fetch(`${settings.apiBaseUrl}/v1/capabilities`, {
      headers: { Authorization: `Bearer ${settings.apiToken}` },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const capabilities = await response.json();
    status.textContent = `已连接 · ${capabilities.backend}`;
  } catch (error) {
    status.textContent = `未连接 · ${error instanceof Error ? error.message : error}`;
  }
}
