export const REMOTE_API_URL = "http://192.168.38.226:8765";
export const LOCAL_API_URL = "http://127.0.0.1:8765";

export const MODE_OPTIONS = Object.freeze([
  { value: "fast", label: "快速模式", prefetchPages: 3 },
  { value: "quality", label: "质量模式", prefetchPages: 2 },
  { value: "cobra", label: "Cobra 实验档", prefetchPages: 1 },
  { value: "flux2", label: "最高质量模式（FLUX.2）", prefetchPages: 1 },
  { value: "flux2_quant", label: "质量模式（FLUX.2 量化实验）", prefetchPages: 1 },
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
  preferWorkAdapter: true,
  allowGenericAdapter: true,
  remoteApiBaseUrl: REMOTE_API_URL,
  remoteApiToken: "",
  remoteMode: "fast",
  localApiBaseUrl: LOCAL_API_URL,
  localApiToken: "",
  localMode: "fast",
});

export function normalizeUrl(value) {
  return String(value || "").trim().replace(/\/$/, "");
}

export function normalizeMode(value) {
  const normalized = String(value || "").trim();
  if (MODE_VALUES.has(normalized)) return normalized;
  return /^[a-z0-9_:-]+$/.test(normalized) ? normalized : "fast";
}

export function prefetchPagesForMode(mode) {
  return MODE_OPTIONS.find((option) => option.value === normalizeMode(mode))
    ?.prefetchPages || 1;
}

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
    preferWorkAdapter: true,
    allowGenericAdapter: true,
  };
}

export function migrateSettings(raw = {}) {
  const deployment = inferDeployment(raw);
  const legacyMode = normalizeMode(raw.mode);
  const legacyUrl = normalizeUrl(raw.apiBaseUrl);
  const legacyToken = String(raw.apiToken || "").trim();
  const merged = {
    ...DEFAULT_SETTINGS,
    ...raw,
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
