import test from "node:test";
import assert from "node:assert/strict";

import {
  activateDeployment,
  migrateSettings,
  normalizeMode,
  prefetchPagesForMode,
} from "./settings.js";

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

test("keeps separate local and remote service configurations", () => {
  const stored = migrateSettings({
    deployment: "remote",
    remoteApiBaseUrl: "http://remote.example:8765",
    remoteApiToken: "remote-token",
    remoteMode: "flux2",
    localApiBaseUrl: "http://127.0.0.1:9876",
    localApiToken: "local-token",
    localMode: "cobra",
  });

  const local = activateDeployment(stored, "local");
  const remote = activateDeployment(local, "remote");
  assert.deepEqual(
    [local.apiBaseUrl, local.apiToken, local.mode, local.profile],
    ["http://127.0.0.1:9876", "local-token", "cobra", "local-cobra"],
  );
  assert.deepEqual(
    [remote.apiBaseUrl, remote.apiToken, remote.mode, remote.profile],
    ["http://remote.example:8765", "remote-token", "flux2", "remote-flux2"],
  );
});

test("accepts experimental modes with conservative prefetch", () => {
  assert.equal(normalizeMode("cobra"), "cobra");
  assert.equal(normalizeMode("flux2"), "flux2");
  assert.equal(normalizeMode("flux2_quant"), "flux2_quant");
  assert.equal(prefetchPagesForMode("cobra"), 1);
  assert.equal(prefetchPagesForMode("flux2"), 1);
});
