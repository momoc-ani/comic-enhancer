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

test("shows MangaNinja reference panels", () => {
  const execution = buildModelExecution(
    {
      model_profile: "manganinja-reference",
      reference_applied: true,
      processed_panels: 2,
      elapsed_ms: 8120,
    },
    settings,
  );

  const view = describeModelTier(settings, execution);
  assert.equal(view.title, "质量档 · 角色参考");
  assert.match(view.detail, /2 格参考上色/);
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
