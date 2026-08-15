const PROFILE_LABELS = Object.freeze({
  "sd15-colorize": "SD1.5 Anime + Lineart",
  "realcugan-se-2x": "Real-CUGAN SE 2x",
  cobra: "Cobra 多参考上色",
  "flux2-klein-4b": "FLUX.2 Klein 4B",
  "flux2-klein-4b-qwen3-fp8": "FLUX.2 Klein 4B · Qwen3 FP8 mixed",
  passthrough: "开发透传后端",
});

const MODE_TITLES = Object.freeze({
  fast: "快速档",
  quality: "质量档",
  upscale: "放大档",
  cobra: "Cobra 档",
  flux2: "最高质量档",
  flux2_quant: "质量档（FLUX.2 量化实验）",
});

// 方法说明：记录最近一次真实模型执行信息。
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

// 方法说明：生成当前档位和实际执行模型的展示信息。
export function describeModelTier(settings, execution, capabilities = null) {
  const mode = normalizeMode(settings.mode);
  const configuredTitle = MODE_TITLES[mode];
  if (!matchesCurrentSettings(settings, execution)) {
    const advertisedModes = capabilities?.processing_modes;
    const unavailable = Boolean(
      capabilities &&
        ((Array.isArray(advertisedModes) && !advertisedModes.includes(mode)) ||
          (mode === "upscale" && capabilities.upscale_available === false) ||
          (mode === "cobra" && capabilities.cobra_available === false) ||
          (mode === "flux2" && capabilities.flux2_available === false) ||
          (mode === "flux2_quant" && capabilities.flux2_quant_available === false)),
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

  let actualTier = mode === "upscale" ? "原生超分" : "基础模型";
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

// 方法说明：判断执行记录是否属于当前服务和档位。
export function matchesCurrentSettings(settings, execution) {
  if (!execution) return false;
  const mode = normalizeMode(settings.mode);
  return (
    execution.requestedMode === mode &&
    normalizeUrl(execution.apiBaseUrl) === normalizeUrl(settings.apiBaseUrl)
  );
}

// 方法说明：将毫秒耗时格式化为秒数。
function formatElapsed(elapsedMs) {
  return (elapsedMs / 1000).toFixed(elapsedMs >= 10000 ? 1 : 2);
}

// 方法说明：规范化服务地址并移除末尾斜杠。
function normalizeUrl(value) {
  return String(value || "").trim().replace(/\/$/, "");
}

// 方法说明：规范化处理档位并回退到安全默认值。
function normalizeMode(value) {
  return ["fast", "quality", "upscale", "cobra", "flux2", "flux2_quant"].includes(value)
    ? value
    : "fast";
}
