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

test("shows experimental mode availability from backend capabilities", () => {
  const cobraSettings = {
    ...settings,
    profile: "remote-cobra",
    mode: "cobra",
  };
  const unavailable = describeModelTier(cobraSettings, null, {
    ready: true,
    processing_modes: ["fast", "quality"],
  });
  assert.equal(unavailable.title, "Cobra 档");
  assert.match(unavailable.detail, /未启用 Cobra/);

  const execution = buildModelExecution(
    { model_profile: "cobra", elapsed_ms: 3200 },
    cobraSettings,
  );
  const actual = describeModelTier(cobraSettings, execution, {
    ready: true,
    processing_modes: ["fast", "quality", "cobra"],
    cobra_available: true,
  });
  assert.equal(actual.title, "Cobra 档 · 基础模型");
  assert.match(actual.detail, /Cobra 多参考上色/);

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
