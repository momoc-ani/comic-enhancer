export const REMOTE_API_URL = "http://192.168.38.226:8765";
export const LOCAL_API_URL = "http://127.0.0.1:8765";

export const MODE_OPTIONS = Object.freeze([
  { value: "fast", label: "快速模式", prefetchPages: 3 },
  { value: "quality", label: "质量模式", prefetchPages: 2 },
  { value: "upscale", label: "放大模式（Real-CUGAN 2x）", prefetchPages: 1 },
  { value: "flux2", label: "最高质量模式（FLUX.2）", prefetchPages: 1 },
  { value: "flux2_quant", label: "质量模式（FLUX.2 量化实验）", prefetchPages: 1 },
  {
    value: "flux2_character",
    label: "角色稳定模式（Qwen3-VL + FLUX.2）",
    prefetchPages: 1,
  },
  {
    value: "flux2_character_lineart",
    label: "角色线稿保真模式（Qwen3-VL + FLUX.2）",
    prefetchPages: 1,
  },
]);

const MODE_VALUES = new Set(MODE_OPTIONS.map((option) => option.value));

export const DEFAULT_SETTINGS = Object.freeze({
  enabled: true,
  deployment: "remote",
  profile: "remote-fast",
  apiBaseUrl: REMOTE_API_URL,
  apiToken: "",
  mode: "fast",
  prefetchPages: 3,
  remoteApiBaseUrl: REMOTE_API_URL,
  remoteApiToken: "",
  remoteMode: "fast",
  localApiBaseUrl: LOCAL_API_URL,
  localApiToken: "",
  localMode: "fast",
  comfyuiDirectOutput: false,
});

const SETTING_KEYS = new Set(Object.keys(DEFAULT_SETTINGS));

// 方法说明：规范化服务地址并移除末尾斜杠。
export function normalizeUrl(value) {
  return String(value || "").trim().replace(/\/$/, "");
}

// 方法说明：规范化处理档位并回退到安全默认值。
export function normalizeMode(value) {
  const normalized = String(value || "").trim();
  if (normalized === "cobra") return "quality";
  if (MODE_VALUES.has(normalized)) return normalized;
  return /^[a-z0-9_:-]+$/.test(normalized) ? normalized : "fast";
}

// 方法说明：返回指定档位建议的预取页数。
export function prefetchPagesForMode(mode) {
  return MODE_OPTIONS.find((option) => option.value === normalizeMode(mode))
    ?.prefetchPages || 1;
}

// 方法说明：从旧设置推断本地或远端部署。
export function inferDeployment(settings) {
  if (["local", "remote"].includes(settings.deployment)) {
    return settings.deployment;
  }
  if (String(settings.profile || "").startsWith("local-")) return "local";
  if (String(settings.profile || "").startsWith("remote-")) return "remote";
  const url = normalizeUrl(settings.apiBaseUrl);
  return url === LOCAL_API_URL || url === "http://localhost:8765"
    ? "local"
    : "remote";
}

// 方法说明：激活指定部署并同步其服务配置。
export function activateDeployment(settings, deployment) {
  const selected = deployment === "local" ? "local" : "remote";
  const prefix = selected === "local" ? "local" : "remote";
  const apiBaseUrl = normalizeUrl(settings[`${prefix}ApiBaseUrl`]);
  const apiToken = String(settings[`${prefix}ApiToken`] || "").trim();
  const mode = normalizeMode(settings[`${prefix}Mode`]);
  return {
    ...settings,
    deployment: selected,
    profile: `${selected}-${mode}`,
    apiBaseUrl,
    apiToken,
    mode,
    prefetchPages: prefetchPagesForMode(mode),
  };
}

// 方法说明：迁移并补全扩展设置。
export function migrateSettings(raw = {}) {
  const retainedRaw = Object.fromEntries(
    Object.entries(raw).filter(([key]) => SETTING_KEYS.has(key)),
  );
  const deployment = inferDeployment(raw);
  const legacyMode = normalizeMode(raw.mode);
  const legacyUrl = normalizeUrl(raw.apiBaseUrl);
  const legacyToken = String(raw.apiToken || "").trim();
  const merged = {
    ...DEFAULT_SETTINGS,
    ...retainedRaw,
    comfyuiDirectOutput: normalizeBoolean(raw.comfyuiDirectOutput),
    remoteApiBaseUrl: normalizeUrl(
      raw.remoteApiBaseUrl ||
        (deployment === "remote" ? legacyUrl : "") ||
        REMOTE_API_URL,
    ),
    remoteApiToken: String(
      raw.remoteApiToken || (deployment === "remote" ? legacyToken : ""),
    ).trim(),
    remoteMode: normalizeMode(
      raw.remoteMode || (deployment === "remote" ? legacyMode : "fast"),
    ),
    localApiBaseUrl: normalizeUrl(
      raw.localApiBaseUrl ||
        (deployment === "local" ? legacyUrl : "") ||
        LOCAL_API_URL,
    ),
    localApiToken: String(
      raw.localApiToken || (deployment === "local" ? legacyToken : ""),
    ).trim(),
    localMode: normalizeMode(
      raw.localMode || (deployment === "local" ? legacyMode : "fast"),
    ),
  };
  return activateDeployment(merged, deployment);
}

// 方法说明：将旧版或存储中的开关值规范化为布尔值。
function normalizeBoolean(value) {
  return value === true || value === 1 || value === "1" || value === "true";
}
