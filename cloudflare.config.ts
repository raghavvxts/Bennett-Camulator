import { bindings, defineConfig } from "cf/config";

export default defineConfig({
  worker: {
    name: "camulator",
    entrypoint: "./worker/index.ts",
    compatibilityDate: "2026-10-31",
    placement: { mode: "smart" },
    env: {
      DB: bindings.d1({
        name: "camulator",
        id: "b4cfea9e-730d-49ea-a730-708afd8f2b4e",
      }),
      TELEGRAM_BOT_TOKEN: bindings.secret(),
      TELEGRAM_WEBHOOK_SECRET: bindings.secret(),
      CREDENTIAL_KEY: bindings.secret(),
      OWNER_TELEGRAM_ID: bindings.secret(),
      OWNER_TELEGRAM_USERNAME: bindings.secret(),
      OWNER_PASSWORD_HASH: bindings.secret(),
      OWNER_COMMAND: bindings.secret(),
    },
    observability: {
      enabled: true,
      redactQueryString: true,
      logs: { enabled: true, invocationLogs: false },
      // Telegram embeds the bot token in its URL; never persist subrequest URLs.
      traces: { enabled: true, headSamplingRate: 0 },
    },
  },
});
