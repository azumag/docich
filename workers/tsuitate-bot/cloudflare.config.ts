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
    compatibilityFlags: ["nodejs_compat"],
    entrypoint: "src/worker.js",
    exports: {
      GameState: exports.durableObject({ storage: "sqlite" }),
      BetaArena: exports.durableObject({ storage: "sqlite" }),
    },
    env: {
      BOT_ID: bindings.text("DoCiAI"),
      GAME_STATE: bindings.durableObject({
        worker: "docich-tsuitate-bot",
        exportName: "GameState",
      }),
      CF_VERSION_METADATA: bindings.versionMetadata(),
      WEBHOOK_SECRET: bindings.secret(),
      BETA_CONTROL_SECRET: bindings.secret(),
      TSUITATE_BOT_TOKEN: bindings.secret(),
      BETA_ARENA_ENABLED: bindings.text("false"),
      BETA_ARENA: bindings.durableObject({ worker: "docich-tsuitate-bot", exportName: "BetaArena" }),
    },
  },
});
