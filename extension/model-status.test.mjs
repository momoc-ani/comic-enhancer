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

// 方法说明：验证 Anima 实验档位按独立能力和真实模型展示。
test("shows Anima experimental mode availability and execution", () => {
  const animaSettings = {
    ...settings,
    profile: "remote-anima_base",
    mode: "anima_base",
  };
  const unavailable = describeModelTier(animaSettings, null, {
    ready: true,
    anima_base_available: false,
  });
  assert.equal(unavailable.title, "Anima Base 线稿上色实验档");
  assert.match(unavailable.detail, /未启用 Anima Base 线稿上色实验档/);

  const execution = buildModelExecution(
    { model_profile: "anima-base-v1.0-lllite-lineart", elapsed_ms: 5000 },
    animaSettings,
  );
  const actual = describeModelTier(animaSettings, execution);
  assert.equal(actual.title, "Anima Base 线稿上色实验档 · 基础模型");
  assert.match(actual.detail, /Anima Base v1\.0/);
});
