import { readFile } from "node:fs/promises";
const [url, secretsPath] = process.argv.slice(2);
if (!url?.startsWith("https://") || !secretsPath)
  throw new Error(
    "Usage: node scripts/connect-telegram.mjs https://WORKER_URL /path/to/secrets.json",
  );
const secrets = JSON.parse(await readFile(secretsPath, "utf8"));
async function call(method, body = {}) {
  let result;
  try {
    const response = await fetch(
      `https://api.telegram.org/bot${secrets.TELEGRAM_BOT_TOKEN}/${method}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
      },
    );
    result = await response.json();
  } catch {
    throw new Error(`Telegram ${method} network failure`);
  }
  if (!result.ok) throw new Error(`Telegram ${method} refused request`);
  return result.result;
}
const bot = await call("getMe");
const health = await fetch(new URL("/health", url));
if (!health.ok)
  throw new Error("Worker health check failed; webhook unchanged");
await call("setMyCommands", {
  commands: [
    { command: "start", description: "Link account / open menu" },
    { command: "daypass", description: "Create or reuse a Day Out Pass" },
    { command: "menu", description: "Live mess menu" },
    { command: "attendance", description: "Live attendance" },
    { command: "status", description: "Current gate pass status" },
    { command: "shortcut", description: "Create a personal API key" },
    { command: "cancel", description: "Cancel the current input" },
    { command: "logout", description: "Remove linked account and credentials" },
  ],
});
await call("setWebhook", {
  url: new URL("/telegram", url).href,
  secret_token: secrets.TELEGRAM_WEBHOOK_SECRET,
  max_connections: 40,
  allowed_updates: ["message", "callback_query"],
  drop_pending_updates: false,
});
const info = await call("getWebhookInfo");
console.log(
  JSON.stringify({
    bot: "@" + bot.username,
    webhook: info.url,
    pending_updates: info.pending_update_count,
    configured: info.url === new URL("/telegram", url).href,
  }),
);
