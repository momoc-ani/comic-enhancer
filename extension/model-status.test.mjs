import test from "node:test";
import assert from "node:assert/strict";

import {
  buildModelExecution,
  describeModelTier,
  matchesCurrentSettings,
} from "./model-status.js";

const settings = {
  profile: "remote-quality",
  mode: "quality",
  apiBaseUrl: "http://192.168.38.226:8765/",
};

// 方法说明：验证作品 LoRA 仅在实际应用后才会显示。
test("shows work LoRA only when it was actually applied", () => {
  const execution = buildModelExecution(
    {
      model_profile: "sd15-colorize",
      adapter_source: "work",
      adapter_id: "work-v1",
      adapter_applied: true,
      elapsed_ms: 5270,
    },
    settings,
  );

  assert.equal(describeModelTier(settings, execution).title, "质量档 · 作品 LoRA");
  assert.match(describeModelTier(settings, execution).detail, /5\.27 秒/);
});

// 方法说明：验证通用 LoRA 和缓存命中状态能够正确显示。
test("shows generic LoRA and cache hits", () => {
  const execution = buildModelExecution(
    {
      model_profile: "sd15-colorize",
      adapter_source: "generic",
      adapter_applied: true,
      cached: true,
    },
    settings,
  );

  const view = describeModelTier(settings, execution);
  assert.equal(view.title, "质量档 · 通用 LoRA");
  assert.match(view.detail, /缓存命中/);
});

// 方法说明：验证未实际应用的适配器不会被错误展示。
test("does not claim an adapter when the selected adapter was not applied", () => {
  const execution = buildModelExecution(
    {
      model_profile: "sd15-colorize",
      adapter_source: "generic",
      adapter_applied: false,
    },
    settings,
  );

  assert.equal(describeModelTier(settings, execution).title, "质量档 · 基础模型");
});

// 方法说明：验证其他服务地址或档位的执行记录会被忽略。
test("ignores execution state from another endpoint or mode", () => {
  const execution = buildModelExecution(
    { model_profile: "sd15-colorize" },
    { ...settings, mode: "fast" },
  );

  assert.equal(matchesCurrentSettings(settings, execution), false);
  assert.equal(
    describeModelTier(settings, execution, { ready: true }).detail,
    "服务已连接，等待当前档位首次推理",
  );
});

// 方法说明：验证实验档位可用性会按后端能力展示。
test("shows experimental mode availability from backend capabilities", () => {
  const flux2Settings = {
    ...settings,
    profile: "remote-flux2",
    mode: "flux2",
  };
  const flux2Unavailable = describeModelTier(flux2Settings, null, {
    ready: true,
    flux2_available: false,
  });
  assert.equal(flux2Unavailable.title, "最高质量档");
  assert.match(flux2Unavailable.detail, /未启用 最高质量档/);
});

// 方法说明：验证放大档展示 Real-CUGAN 实际模型和独立超分类型。
test("shows Real-CUGAN execution for upscale mode", () => {
  const upscaleSettings = {
    ...settings,
    profile: "local-upscale",
    mode: "upscale",
  };
  const unavailable = describeModelTier(upscaleSettings, null, {
    ready: true,
    processing_modes: ["fast", "quality"],
    upscale_available: false,
  });
  assert.equal(unavailable.title, "放大档");
  assert.match(unavailable.detail, /未启用 放大档/);

  const execution = buildModelExecution(
    { model_profile: "realcugan-se-2x", elapsed_ms: 1500 },
    upscaleSettings,
  );
  const actual = describeModelTier(upscaleSettings, execution, {
    ready: true,
    processing_modes: ["fast", "quality", "upscale"],
    upscale_available: true,
  });
  assert.equal(actual.title, "放大档 · 原生超分");
  assert.match(actual.detail, /Real-CUGAN SE 2x/);
});
