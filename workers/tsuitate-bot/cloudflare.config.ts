import { bindings, defineConfig, exports } from "cf/config";

export default defineConfig({
  worker: {
    name: "docich-tsuitate-bot",
    compatibilityDate: "2026-09-08",
    previewUrls: false,
    entrypoint: "src/index.js",
    exports: {
      GameState: exports.durableObject({ storage: "sqlite" }),
    },
    env: {
      BOT_ID: bindings.text("replace-with-tsuitate-bot-id"),
      GAME_STATE: bindings.durableObject({
        worker: "docich-tsuitate-bot",
        exportName: "GameState",
      }),
      WEBHOOK_SECRET: bindings.secret(),
    },
  },
});
