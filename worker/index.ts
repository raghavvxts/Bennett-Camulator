import { boundedJson, Camu, object, UserError } from "./camu.ts";
import { Store } from "./storage.ts";
import { equal } from "./security.ts";
import { Telegram, type Update } from "./telegram.ts";
import { attendance, menu } from "./reports.ts";
const json = (body: unknown, status = 200) =>
  Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
export async function webhook(request: Request, env: Env): Promise<Response> {
  const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
  if (
    !secret ||
    !env.TELEGRAM_WEBHOOK_SECRET ||
    !(await equal(secret, env.TELEGRAM_WEBHOOK_SECRET))
  )
    return json({ error: "Unauthorized" }, 403);
  let update: Update;
  try {
    const body = object(await boundedJson(request, 32768));
    if (!Number.isSafeInteger(body.update_id))
      return json({ error: "Invalid update" }, 400);
    update = body as unknown as Update;
  } catch {
    return json({ error: "Invalid update" }, 400);
  }
  const message = update.message || update.callback_query?.message;
  const sender = update.callback_query?.from || update.message?.from;
  if (
    !message ||
    !sender ||
    message.chat?.type !== "private" ||
    !Number.isSafeInteger(sender.id) ||
    message.chat.id !== sender.id
  )
    return json({ ok: true });
  const store = new Store(env),
    key = `chat:${sender.id}`,
    lease = await store.lease(key);
  if (!lease) return json({ error: "Chat busy; retry" }, 503);
  try {
    if (
      await env.DB.prepare("SELECT id FROM updates WHERE id=?")
        .bind(update.update_id)
        .first()
    )
      return json({ ok: true });
    const tg = new Telegram(env, store);
    try {
      await tg.handle(update);
    } catch (error) {
      if (!(error instanceof UserError))
        console.error(
          JSON.stringify({
            event: "telegram_request_failed",
            kind: error instanceof Error ? error.name : "Unknown",
            location:
              error instanceof Error
                ? error.stack
                    ?.split("\n")
                    .slice(1, 4)
                    .map((line) =>
                      line.match(/at ([\w.<>]+).*?:(\d+):(\d+)/)?.slice(1),
                    )
                : undefined,
          }),
        );
      await tg.send(
        sender.id,
        error instanceof UserError
          ? error.message
          : "The request could not be completed. If you submitted a pass, check MyCamu before retrying.",
      );
    }
    await env.DB.prepare(
      "INSERT OR IGNORE INTO updates(id,expires) VALUES(?,?)",
    )
      .bind(update.update_id, Date.now() + 7 * 86400000)
      .run();
    if (update.update_id % 100 === 0)
      await env.DB.batch([
        env.DB.prepare("DELETE FROM updates WHERE expires<?").bind(Date.now()),
        env.DB.prepare("DELETE FROM conversations WHERE expires<?").bind(
          Date.now(),
        ),
        env.DB.prepare("DELETE FROM leases WHERE expires<?").bind(Date.now()),
      ]);
    return json({ ok: true });
  } finally {
    await store.release(key, lease);
  }
}
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    try {
      if (
        (url.pathname === "/" || url.pathname === "/health") &&
        request.method === "GET"
      ) {
        await env.DB.prepare("SELECT 1 AS ready").first();
        const ready = Boolean(
          env.TELEGRAM_BOT_TOKEN &&
          env.TELEGRAM_WEBHOOK_SECRET &&
          env.CREDENTIAL_KEY,
        );
        return json(
          {
            status: ready ? "ok" : "not configured",
            app: "Camulator",
            telegram: ready ? "webhook configured" : "disabled",
          },
          ready ? 200 : 503,
        );
      }
      if (url.pathname === "/telegram" && request.method === "POST")
        return await webhook(request, env);
      if (
        ![
          "/quick-pass",
          "/active-pass",
          "/attendance",
          "/leaves",
          "/menu",
        ].includes(url.pathname)
      )
        return json({ error: "Not found" }, 404);
      const read = ["/active-pass", "/attendance", "/leaves", "/menu"].includes(
        url.pathname,
      );
      if (request.method !== "POST" && !(read && request.method === "GET"))
        return json({ error: "Method not allowed" }, 405);
      const token = request.headers.get("X-API-Key");
      if (!token || token.length > 256)
        return json({ error: "Unauthorized" }, 403);
      const store = new Store(env),
        id = await store.shortcutId(token);
      if (id === null) return json({ error: "Unauthorized" }, 403);
      const profile = await store.account(id);
      if (!profile) return json({ error: "Unauthorized" }, 403);
      const client = new Camu(profile);
      await client.connect();
      await store.saveAccount(id, client.profile);
      if (url.pathname === "/active-pass") return json(await client.active());
      if (url.pathname === "/attendance") return json(await attendance(client));
      if (url.pathname === "/menu")
        return json({ success: true, text: await menu(client, "all") });
      if (url.pathname === "/leaves")
        return json({ leaves: await client.records() });
      const body = object(await boundedJson(request, 4096));
      if (body.force)
        throw new UserError("Gate and overlap checks cannot be bypassed.");
      if (body.reason !== undefined && typeof body.reason !== "string")
        throw new UserError("Reason must be text.");
      if (
        body.departure_at !== undefined &&
        body.departure_at !== null &&
        typeof body.departure_at !== "string"
      )
        throw new UserError(
          "Departure must be an ISO timestamp with timezone.",
        );
      return json(
        await client.createPass(
          store,
          typeof body.reason === "string" ? body.reason : "Going out",
          typeof body.departure_at === "string" ? body.departure_at : undefined,
        ),
      );
    } catch (error) {
      if (error instanceof UserError)
        return json({ success: false, message: error.message }, 400);
      console.error(
        JSON.stringify({
          event: "request_failed",
          kind: error instanceof Error ? error.name : "Unknown",
          location:
            error instanceof Error
              ? error.stack
                  ?.split("\n")
                  .slice(1, 4)
                  .map((line) =>
                    line.match(/at ([\w.<>]+).*?:(\d+):(\d+)/)?.slice(1),
                  )
              : undefined,
        }),
      );
      return json(
        {
          success: false,
          message:
            "Service unavailable. If a pass was submitted, check MyCamu before retrying.",
        },
        503,
      );
    }
  },
} satisfies ExportedHandler<Env>;
