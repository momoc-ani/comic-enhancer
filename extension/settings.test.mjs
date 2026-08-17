import test from "node:test";
import assert from "node:assert/strict";

import {
  activateDeployment,
  migrateSettings,
  normalizeMode,
  prefetchPagesForMode,
} from "./settings.js";

// 方法说明：验证旧版远端配置迁移时不会丢失 Token。
test("migrates a legacy remote profile without losing its token", () => {
  const settings = migrateSettings({
    profile: "remote-quality",
    apiBaseUrl: "http://enhancer.example/",
    apiToken: "remote-token",
    mode: "quality",
  });

  assert.equal(settings.deployment, "remote");
  assert.equal(settings.remoteApiBaseUrl, "http://enhancer.example");
  assert.equal(settings.remoteApiToken, "remote-token");
  assert.equal(settings.remoteMode, "quality");
  assert.equal(settings.apiToken, "remote-token");
});

// 方法说明：验证本地和远端服务配置会分别保存。
test("keeps separate local and remote service configurations", () => {
  const stored = migrateSettings({
    deployment: "remote",
    remoteApiBaseUrl: "http://remote.example:8765",
    remoteApiToken: "remote-token",
    remoteMode: "flux2",
    localApiBaseUrl: "http://127.0.0.1:9876",
    localApiToken: "local-token",
    localMode: "quality",
  });

  const local = activateDeployment(stored, "local");
  const remote = activateDeployment(local, "remote");
  assert.deepEqual(
    [local.apiBaseUrl, local.apiToken, local.mode, local.profile],
    ["http://127.0.0.1:9876", "local-token", "quality", "local-quality"],
  );
  assert.deepEqual(
    [remote.apiBaseUrl, remote.apiToken, remote.mode, remote.profile],
    ["http://remote.example:8765", "remote-token", "flux2", "remote-flux2"],
  );
});

// 方法说明：验证实验档位使用保守的页面预取数量。
test("accepts experimental modes with conservative prefetch", () => {
  assert.equal(normalizeMode("cobra"), "quality");
  assert.equal(normalizeMode("upscale"), "upscale");
  assert.equal(normalizeMode("flux2"), "flux2");
  assert.equal(normalizeMode("flux2_quant"), "flux2_quant");
  assert.equal(normalizeMode("flux2_character"), "flux2_character");
  assert.equal(
    normalizeMode("flux2_character_lineart"),
    "flux2_character_lineart",
  );
  assert.equal(prefetchPagesForMode("cobra"), 2);
  assert.equal(prefetchPagesForMode("upscale"), 1);
  assert.equal(prefetchPagesForMode("flux2"), 1);
  assert.equal(prefetchPagesForMode("flux2_character_lineart"), 1);
  assert.equal(normalizeMode("anima_base"), "anima_base");
  assert.equal(normalizeMode("anima_2_9b"), "anima_2_9b");
  assert.equal(prefetchPagesForMode("anima_base"), 1);
  assert.equal(prefetchPagesForMode("anima_2_9b"), 1);
});

// 方法说明：验证 ComfyUI 直出开关默认关闭且能从存储中迁移。
test("migrates the ComfyUI direct output switch", () => {
  assert.equal(migrateSettings({}).comfyuiDirectOutput, false);
  assert.equal(migrateSettings({ comfyuiDirectOutput: true }).comfyuiDirectOutput, true);
  assert.equal(
    migrateSettings({ comfyuiDirectOutput: "false" }).comfyuiDirectOutput,
    false,
  );
});
