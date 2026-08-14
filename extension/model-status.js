const PROFILE_LABELS = Object.freeze({
  "sd15-colorize": "SD1.5 Anime + Lineart",
  "manganinja-reference": "MangaNinja 角色参考",
  cobra: "Cobra 多参考上色",
  "flux2-klein-4b": "FLUX.2 Klein 4B",
  passthrough: "开发透传后端",
});

const MODE_TITLES = Object.freeze({
  fast: "快速档",
  quality: "质量档",
  manganinja: "MangaNinja 档",
  cobra: "Cobra 档",
  flux2: "FLUX.2 档",
});

export function buildModelExecution(result, settings, completedAt = Date.now()) {
  return {
    requestedMode: normalizeMode(settings.mode),
    configuredProfile: settings.profile || "custom",
    apiBaseUrl: normalizeUrl(settings.apiBaseUrl),
    modelProfile: String(result.model_profile || "unknown"),
    adapterSource: String(result.adapter_source || "none"),
    adapterId: result.adapter_id || null,
    adapterApplied: Boolean(result.adapter_applied),
    referenceApplied: Boolean(result.reference_applied),
    processedPanels: Number(result.processed_panels) || 0,
    cached: Boolean(result.cached),
    elapsedMs: Number(result.elapsed_ms) || 0,
    completedAt,
  };
}

export function describeModelTier(settings, execution, capabilities = null) {
  const mode = normalizeMode(settings.mode);
  const configuredTitle = MODE_TITLES[mode];
  if (!matchesCurrentSettings(settings, execution)) {
    const advertisedModes = capabilities?.processing_modes;
    const unavailable = Boolean(
      capabilities &&
        ((Array.isArray(advertisedModes) && !advertisedModes.includes(mode)) ||
          (mode === "manganinja" && capabilities.manganinja_available === false) ||
          (mode === "cobra" && capabilities.cobra_available === false)),
    );
    return {
      title: configuredTitle,
      detail: unavailable
        ? `服务未启用 ${configuredTitle}`
        : capabilities?.ready
          ? "服务已连接，等待当前档位首次推理"
          : "等待连接服务并完成首次推理",
      state: unavailable ? "unavailable" : capabilities?.ready ? "ready" : "pending",
    };
  }

  let actualTier = "基础模型";
  if (execution.referenceApplied) {
    actualTier = "角色参考";
  } else if (execution.adapterApplied && execution.adapterSource === "work") {
    actualTier = "作品 LoRA";
  } else if (execution.adapterApplied && execution.adapterSource === "generic") {
    actualTier = "通用 LoRA";
  }

  const modelLabel =
    PROFILE_LABELS[execution.modelProfile] || execution.modelProfile || "未知模型";
  const details = [modelLabel];
  if (execution.referenceApplied && execution.processedPanels > 0) {
    details.push(`${execution.processedPanels} 格参考上色`);
  }
  if (execution.cached) {
    details.push("缓存命中");
  } else if (execution.elapsedMs > 0) {
    details.push(`${formatElapsed(execution.elapsedMs)} 秒`);
  }
  return {
    title: `${configuredTitle} · ${actualTier}`,
    detail: `最近实际执行：${details.join(" · ")}`,
    state: "actual",
  };
}

export function matchesCurrentSettings(settings, execution) {
  if (!execution) return false;
  const mode = normalizeMode(settings.mode);
  return (
    execution.requestedMode === mode &&
    normalizeUrl(execution.apiBaseUrl) === normalizeUrl(settings.apiBaseUrl)
  );
}

function formatElapsed(elapsedMs) {
  return (elapsedMs / 1000).toFixed(elapsedMs >= 10000 ? 1 : 2);
}

function normalizeUrl(value) {
  return String(value || "").trim().replace(/\/$/, "");
}

function normalizeMode(value) {
  return ["fast", "quality", "manganinja", "cobra", "flux2"].includes(value)
    ? value
    : "fast";
}
