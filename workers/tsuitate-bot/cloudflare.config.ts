import { bindings, defineConfig, exports } from "cf/config";

export default defineConfig({
  worker: {
    name: "docich-tsuitate-bot",
    compatibilityDate: "2026-09-08",
    previewUrls: false,
    observability: {
      enabled: true,
      logs: {
        enabled: true,
        headSamplingRate: 1,
        invocationLogs: false,
        persist: true,
      },
    },
    entrypoint: "src/index.js",
    exports: {
      GameState: exports.durableObject({ storage: "sqlite" }),
    },
    unsafe: {
      metadata: {
        durable_objects: {
          bindings: [{ name: "GAME_STATE", class_name: "GameState" }],
        },
      },
    },
    env: {
      BOT_ID: bindings.text("DoCiAI"),
      GAME_STATE: bindings.durableObject({
        worker: "docich-tsuitate-bot",
        exportName: "GameState",
      }),
      CF_VERSION_METADATA: bindings.versionMetadata(),
      WEBHOOK_SECRET: bindings.secret(),
    },
  },
});
