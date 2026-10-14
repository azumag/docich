import { readFileSync } from "node:fs";
import { bindings, defineConfig, exports, triggers } from "cf/config";

const personaUrl = new URL("../../src/docich/comment/prompts/comment_persona_main.md", import.meta.url);
const persona = readFileSync(personaUrl, "utf8").trim();
if (!persona || Buffer.byteLength(persona, "utf8") > 32768) {
  throw new Error("canonical Discord persona is missing or invalid");
}

export default defineConfig({
  worker: {
    name: "docich-discord-chat",
    compatibilityDate: "2026-10-03",
    previewUrls: false,
    observability: {
      logs: {
        enabled: true,
        headSamplingRate: 1,
        invocationLogs: false,
        persist: true,
      },
      issues: {
        enabled: false,
      },
    },
    entrypoint: "src/index.js",
    exports: {
      DiscordBot: exports.durableObject({ storage: "sqlite" }),
    },
    env: {
      DISCORD_BOT: bindings.durableObject({
        worker: "docich-discord-chat",
        exportName: "DiscordBot",
      }),
      AI: bindings.ai(),
      WORKERS_AI_MODEL: bindings.text("@cf/deepseek-ai/deepseek-v4-flash-0731"),
      DOCICH_PERSONA: bindings.text(persona),
      CF_VERSION_METADATA: bindings.versionMetadata(),
    },
    triggers: [triggers.scheduled({ schedule: "* * * * *" })],
  },
});
