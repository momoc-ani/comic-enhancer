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

// 方法说明：验证基础模型及实际耗时能够正确显示。
test("shows the actual base model and elapsed time", () => {
  const execution = buildModelExecution(
    {
      model_profile: "sd15-colorize",
      elapsed_ms: 5270,
    },
    settings,
  );

  assert.equal(describeModelTier(settings, execution).title, "质量档 · 基础模型");
  assert.match(describeModelTier(settings, execution).detail, /5\.27 秒/);
});

// 方法说明：验证基础模型的缓存命中状态能够正确显示。
test("shows cache hits for the actual base model", () => {
  const execution = buildModelExecution(
    {
      model_profile: "sd15-colorize",
      cached: true,
    },
    settings,
  );

  const view = describeModelTier(settings, execution);
  assert.equal(view.title, "质量档 · 基础模型");
  assert.match(view.detail, /缓存命中/);
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

// 方法说明：验证角色线稿保真档按独立能力和真实模型展示。
test("shows character lineart mode availability and execution", () => {
  const lineartSettings = {
    ...settings,
    profile: "remote-flux2_character_lineart",
    mode: "flux2_character_lineart",
  };
  const unavailable = describeModelTier(lineartSettings, null, {
    ready: true,
    flux2_character_lineart_available: false,
  });
  assert.equal(unavailable.title, "角色线稿保真档");
  assert.match(unavailable.detail, /未启用 角色线稿保真档/);

  const execution = buildModelExecution(
    {
      model_profile:
        "flux2-klein-4b-qwen3-vl-character-lineart+realcugan-se-2x",
      reference_applied: true,
      elapsed_ms: 6000,
    },
    lineartSettings,
  );
  const actual = describeModelTier(lineartSettings, execution);
  assert.equal(actual.title, "角色线稿保真档 · 角色参考");
  assert.match(actual.detail, /FLUX\.2 线稿保真 · Real-CUGAN 2x/);
});


// 方法说明：验证角色参考不可用时显示真实的无参考模型档位。
test("shows character no-reference fallback profile", () => {
  const characterSettings = {
    ...settings,
    profile: "remote-flux2_character",
    mode: "flux2_character",
  };
  const execution = buildModelExecution(
    {
      model_profile: "flux2-klein-4b-character-no-reference",
      reference_applied: false,
      elapsed_ms: 1200,
    },
    characterSettings,
  );
  const actual = describeModelTier(characterSettings, execution);

  assert.equal(actual.title, "角色稳定档 · 无参考");
  assert.match(actual.detail, /FLUX\.2 Klein 4B · 无参考降级/);
});

// 方法说明：验证新增验收档按独立能力字段和组合模型标识展示。
test("shows new FLUX.2 acceptance modes independently", () => {
  const qualitySettings = {
    ...settings,
    profile: "remote-flux2_9b_lora",
    mode: "flux2_9b_lora",
  };
  const unavailable = describeModelTier(qualitySettings, null, {
    ready: true,
    flux2_9b_lora_available: false,
  });
  assert.equal(unavailable.title, "9B LoRA 画质档");
  assert.match(unavailable.detail, /未启用 9B LoRA 画质档/);

  const execution = buildModelExecution(
    {
      model_profile: "flux2-klein-9b-lora+realcugan-se-2x",
      reference_applied: true,
      elapsed_ms: 9000,
    },
    qualitySettings,
  );
  const actual = describeModelTier(qualitySettings, execution);
  assert.equal(actual.title, "9B LoRA 画质档 · 角色参考");
  assert.match(actual.detail, /FLUX\.2 Klein 9B LoRA · Real-CUGAN 2x/);
});


// 方法说明：验证 9B FP8 快速档按独立能力字段和真实模型展示。
test("shows 9B FP8 fast mode independently", () => {
  const fastSettings = {
    ...settings,
    profile: "remote-flux2_9b_fast",
    mode: "flux2_9b_fast",
  };
  const unavailable = describeModelTier(fastSettings, null, {
    ready: true,
    flux2_9b_fast_available: false,
  });
  assert.equal(unavailable.title, "9B FP8 快速档");
  assert.match(unavailable.detail, /未启用 9B FP8 快速档/);

  const execution = buildModelExecution(
    {
      model_profile: "flux2-klein-9b-fast+realcugan-se-2x",
      reference_applied: true,
      elapsed_ms: 7000,
    },
    fastSettings,
  );
  const actual = describeModelTier(fastSettings, execution);
  assert.equal(actual.title, "9B FP8 快速档 · 角色参考");
  assert.match(actual.detail, /FLUX\.2 Klein 9B FP8 快速计算 · Real-CUGAN 2x/);
});


// 方法说明：验证 4B 色彩增强档按独立能力字段和真实模型展示。
test("shows 4B color mode independently", () => {
  const colorSettings = {
    ...settings,
    profile: "remote-flux2_4b_color",
    mode: "flux2_4b_color",
  };
  const unavailable = describeModelTier(colorSettings, null, {
    ready: true,
    flux2_4b_color_available: false,
  });
  assert.equal(unavailable.title, "4B 色彩增强档");
  assert.match(unavailable.detail, /未启用 4B 色彩增强档/);

  const execution = buildModelExecution(
    {
      model_profile: "flux2-klein-4b-color+realcugan-se-2x",
      reference_applied: true,
      elapsed_ms: 6500,
    },
    colorSettings,
  );
  const actual = describeModelTier(colorSettings, execution);
  assert.equal(actual.title, "4B 色彩增强档 · 角色参考");
  assert.match(actual.detail, /FLUX\.2 Klein 4B 色彩增强 · Real-CUGAN 2x/);
});
