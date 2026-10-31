import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { Store, type Profile } from "./storage.ts";
import {
  base64,
  equal,
  hash,
  open,
  seal,
  ownerVerifier,
  verifyOwner,
} from "./security.ts";
import { Camu, blocking, type GateRecord } from "./camu.ts";
import { ist, tomorrow, windowFor } from "./time.ts";
import { menu, attendance } from "./reports.ts";
import worker from "./index.ts";
import { Telegram, type Update } from "./telegram.ts";

const fixed = Date.parse("2026-11-01T11:23:40Z"); // 16:53:40 IST
function fixture() {
  const sql = new DatabaseSync(":memory:");
  sql.exec(readFileSync("worker/schema.sql", "utf8"));
  const prepare = (query: string) => {
    let params: (string | number | null)[] = [];
    const statement = {
      bind(...args: (string | number | null)[]) {
        params = args;
        return statement;
      },
      async first() {
        return sql.prepare(query).get(...params) || null;
      },
      async all() {
        return { results: sql.prepare(query).all(...params), success: true };
      },
      async run() {
        const result = sql.prepare(query).run(...params);
        return { success: true, meta: { changes: Number(result.changes) } };
      },
    };
    return statement;
  };
  const db = {
    prepare,
    async batch(statements: { run: () => Promise<unknown> }[]) {
      const results = [];
      sql.exec("BEGIN");
      try {
        for (const s of statements) results.push(await s.run());
        sql.exec("COMMIT");
        return results;
      } catch (e) {
        sql.exec("ROLLBACK");
        throw e;
      }
    },
  };
  const env = {
    DB: db,
    CREDENTIAL_KEY: base64(crypto.getRandomValues(new Uint8Array(32))),
    TELEGRAM_BOT_TOKEN: "synthetic",
    TELEGRAM_WEBHOOK_SECRET: "webhook-secret",
    OWNER_TELEGRAM_ID: "123",
    OWNER_TELEGRAM_USERNAME: "owner_test",
    OWNER_COMMAND: "private_users",
    OWNER_PASSWORD_HASH: "",
  } as unknown as Env;
  return { env, store: new Store(env), sql };
}
function profile(email = "parent@example.invalid"): Profile {
  return {
    email,
    password: "synthetic-secret",
    student_name: "Test Student",
    stu_id: email,
    in_id: "institute",
    logged_in_id: "parent",
    account_type: "parent",
    cookies: { session: email },
  };
}
function portal(initial: GateRecord[] = [], fault?: string) {
  let records = structuredClone(initial),
    creates = 0,
    approvals = 0,
    lastPayload: Record<string, unknown> | undefined;
  const network = async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input),
      body = JSON.parse(String(init?.body || "{}"));
    let data: unknown;
    if (path.endsWith("getAllleave/")) data = records;
    else if (path.endsWith("fetch-gatepasses-types"))
      data = {
        gTypes: [
          {
            passCd: "DOP",
            iSDPss: true,
            StFl: "A",
            enblTm: true,
            frTm: "6:00 AM",
            toTm: "7:00 PM",
          },
        ],
      };
    else if (path.endsWith("crtLv")) {
      creates++;
      lastPayload = body;
      if (fault === "known") data = { code: "DAY_IS_HOLIDAY" };
      else if (fault === "unknown") data = { code: "NEW_REFUSAL" };
      else {
        records.unshift({
          _id: "new-" + creates,
          isGatepass: true,
          passTyCode: "DOP",
          frdt: body.frdt,
          todt: body.todt,
          rson: body.rson,
          status: "SUB",
        });
        if (fault === "timeout") throw new Error("network timeout");
        data = { code: "ONLY_GATEPASS_CRTD" };
      }
    } else if (path.endsWith("change-gatepasses-status")) {
      approvals++;
      const r = records.find((r) => r._id === body.id)!;
      r.status = "APPRVD";
      r.prntAp = "APPRVD";
      data = { code: "STATUS_UPDTD_SUCC" };
    } else if (path.endsWith("fetch-gatepasses-status"))
      data = { gatepass: records.find((r) => r._id === body.id) };
    else throw new Error("Unexpected test request " + path);
    return Response.json({ output: { data } });
  };
  return {
    network: network as typeof fetch,
    get creates() {
      return creates;
    },
    get approvals() {
      return approvals;
    },
    get payload() {
      return lastPayload;
    },
  };
}
const gate = (value: Partial<GateRecord> = {}): GateRecord => ({
  _id: "gate",
  isGatepass: true,
  passTyCode: "DOP",
  frdt: "2026-11-01T12:00:00Z",
  todt: "2026-11-01T19:00:00Z",
  rson: "Going out",
  status: "APPRVD",
  ...value,
});

test("IST next-minute departure, 6 AM minimum and wall-clock serialization", () => {
  const w = windowFor(undefined, fixed);
  assert.deepEqual(w, {
    departure: "2026-11-01T16:54:00+00:00",
    return: "2026-11-01T19:00:00+00:00",
    day: "2026-11-01",
  });
  assert.equal(
    (
      windowFor(undefined, Date.parse("2026-11-01T00:00:00Z")) as {
        departure: string;
      }
    ).departure,
    "2026-11-01T06:00:00+00:00",
  );
});
test("18:59 onward requires tomorrow, including 19:00", () => {
  for (const t of ["18:59:00", "19:00:00", "23:59:00"])
    assert.deepEqual(
      windowFor(undefined, Date.parse(`2026-11-01T${t}+05:30`)),
      { needs_next_day: true },
    );
});
test("explicit dates and times must be future, zoned, today/tomorrow and within window", () => {
  for (const value of [
    "2026-11-01T16:00:00+05:30",
    "2026-11-02T05:59:00+05:30",
    "2026-11-02T19:00:00+05:30",
    "2026-11-03T10:00:00+05:30",
    "2026-11-02T10:00:00",
  ])
    assert.throws(() => windowFor(value, fixed));
  assert.equal(
    (windowFor("2026-11-02T10:00:00+05:30", fixed) as { day: string }).day,
    "2026-11-02",
  );
});
test("IST midnight changes menu/pass date", () => {
  assert.equal(
    ist(Date.parse("2026-11-01T18:30:00Z")).slice(0, 10),
    "2026-11-02",
  );
  assert.equal(tomorrow(Date.parse("2026-11-01T18:29:59Z")), "2026-11-02");
});
test("AES-GCM binds ciphertext to account ID, resists tampering and uses random nonces", async () => {
  const key = fixture().env.CREDENTIAL_KEY,
    a = await seal({ password: "secret" }, key, "account:1"),
    b = await seal({ password: "secret" }, key, "account:1");
  assert.notEqual(a, b);
  assert.ok(!a.includes("secret"));
  assert.deepEqual(await open(a, key, "account:1"), { password: "secret" });
  await assert.rejects(open(a, key, "account:2"));
  await assert.rejects(open(a.slice(0, -5) + "AAAAA", key, "account:1"));
});
test("owner verifier requires password and hosting pepper", async () => {
  const verifier = await ownerVerifier("test-password-123", "pepper");
  assert.ok(await verifyOwner("test-password-123", verifier, "pepper"));
  assert.equal(
    await verifyOwner("wrong-password-123", verifier, "pepper"),
    false,
  );
  assert.equal(
    await verifyOwner("test-password-123", verifier, "wrong-pepper"),
    false,
  );
  assert.equal(await verifyOwner("short", verifier, "pepper"), false);
});
test("comparison handles differing lengths without ordinary string equality", async () => {
  assert.ok(await equal("abc", "abc"));
  assert.equal(await equal("abc", "abcd"), false);
});
test("encrypted account storage, shortcut rotation and logout", async () => {
  const { store, sql } = fixture();
  await store.saveAccount(1, profile());
  assert.ok(
    !String(sql.prepare("SELECT sealed FROM accounts").get()!.sealed).includes(
      "synthetic-secret",
    ),
  );
  assert.equal((await store.account(1))!.password, "synthetic-secret");
  const a = await store.shortcut(1),
    b = await store.shortcut(1);
  assert.equal(await store.shortcutId(a), null);
  assert.equal(await store.shortcutId(b), 1);
  await store.forget(1);
  assert.equal(await store.shortcutId(b), null);
  assert.equal(await store.account(1), null);
});
test("atomic account lease excludes competing holders; wrong holder cannot release it", async () => {
  const { store } = fixture();
  const [a, b] = await Promise.all([
    store.lease("account"),
    store.lease("account"),
  ]);
  assert.equal(Number(Boolean(a)) + Number(Boolean(b)), 1);
  await store.release("account", "wrong");
  assert.equal(await store.lease("account"), null);
  await store.release("account", a || b!);
  assert.ok(await store.lease("account"));
});
test("100 accounts maintain separate encrypted credentials and API keys", async () => {
  const { store } = fixture();
  const keys = [];
  for (let i = 1; i <= 100; i++) {
    await store.saveAccount(i, profile(`parent${i}@example.invalid`));
    keys.push(await store.shortcut(i));
  }
  assert.equal(new Set(keys).size, 100);
  for (let i = 1; i <= 100; i++) {
    assert.equal(await store.shortcutId(keys[i - 1]), i);
    assert.equal((await store.account(i))!.email, `parent${i}@example.invalid`);
  }
});
test("non-Completed gate statuses block, including rejected/cancelled/unknown; Completed permits next pass", () => {
  for (const s of ["SUB", "APPRVD", "CNCL", "REJ", "EXP", "NEW"])
    assert.equal(
      blocking([gate({ status: s })], "2026-11-01", "2026-11-01").length,
      1,
    );
  assert.equal(
    blocking([gate({ status: "CMPT" })], "2026-11-01", "2026-11-01").length,
    0,
  );
  assert.equal(
    blocking(
      [gate({ status: "CMPT", isCOut: true })],
      "2026-11-01",
      "2026-11-01",
    ).length,
    1,
  );
});
test("today unfinished blocks tomorrow too", () => {
  assert.equal(blocking([gate()], "2026-11-02", "2026-11-01").length, 1);
});
for (const reuse of [false, true])
  test(`create and verify pass, ${reuse ? "reuse existing leave" : "include missing leave"}`, async (t) => {
    t.mock.method(Date, "now", () => fixed);
    const { store } = fixture();
    const records: GateRecord[] = reuse
      ? [
          {
            _id: "leave",
            LvTy: "Leave",
            Stus: "Submitted",
            start: "2026-11-01T00:00:00Z",
            end: "2026-11-01T00:00:00Z",
          },
        ]
      : [];
    const p = portal(records),
      client = new Camu(profile(), p.network),
      result = await client.createPass(store);
    assert.ok(result.success);
    assert.equal(result.status, "Approved");
    assert.equal(p.creates, 1);
    assert.equal(p.approvals, 1);
    assert.equal(p.payload!.passTy, "DOP");
    assert.equal(p.payload!.frdt, "2026-11-01T16:54:00+00:00");
    assert.deepEqual(p.payload!.atcmnt, []);
    assert.equal(p.payload!.LvTy, reuse ? undefined : "Leave");
    if (!reuse) assert.equal(p.payload!.FrDt, p.payload!.ToDt);
  });
test("approved existing pass reused without new submission or repeated approval", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture(),
    p = portal([gate()]);
  const result = await new Camu(profile(), p.network).createPass(store);
  assert.ok(result.success);
  assert.ok(result.reused_gate_pass);
  assert.equal(p.creates, 0);
  assert.equal(p.approvals, 0);
});
test("submitted existing pass approved without duplicate", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture(),
    p = portal([gate({ status: "SUB" })]);
  assert.ok((await new Camu(profile(), p.network).createPass(store)).success);
  assert.equal(p.creates, 0);
  assert.equal(p.approvals, 1);
});
test("inside campus still blocks unfinished other pass type", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture(),
    p = portal([gate({ passTyCode: "LEAVE_OUT_PASS", isCOut: false })]);
  await assert.rejects(
    new Camu(profile(), p.network).createPass(store),
    /not Completed/,
  );
  assert.equal(p.creates, 0);
});
test("Completed historical pass allows a new pass", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture(),
    p = portal([gate({ status: "CMPT" })]);
  assert.ok((await new Camu(profile(), p.network).createPass(store)).success);
  assert.equal(p.creates, 1);
});
test("crash after submission reconciles existing record instead of replaying create", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture(),
    p = portal([], "timeout"),
    client = new Camu(profile(), p.network);
  await assert.rejects(client.createPass(store));
  assert.ok(await store.uncertain(await hash(profile().email)));
  assert.ok((await client.createPass(store)).success);
  assert.equal(p.creates, 1);
});
test("unknown refusal remains uncertain and blocks blind retry", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture(),
    p = portal([], "unknown"),
    client = new Camu(profile(), p.network);
  await assert.rejects(client.createPass(store));
  await assert.rejects(client.createPass(store), /uncertain/);
  assert.equal(p.creates, 1);
});
test("known refusal clears uncertainty without reporting success", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture(),
    p = portal([], "known");
  await assert.rejects(
    new Camu(profile(), p.network).createPass(store),
    /holiday/,
  );
  assert.equal(await store.uncertain(await hash(profile().email)), false);
});
test("declining/empty reason never submits", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture(),
    p = portal();
  await assert.rejects(new Camu(profile(), p.network).createPass(store, " "));
  assert.equal(p.creates, 0);
});
test("same MyCamu email in two Telegram accounts shares pass lock", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture(),
    p = portal();
  const token = await store.lease("pass:" + (await hash(profile().email)));
  await assert.rejects(
    new Camu(profile(), p.network).createPass(store),
    /already running/,
  );
  assert.equal(p.creates, 0);
  await store.release("pass:" + (await hash(profile().email)), token!);
});
test("menu uses live weekday, real serving windows and current date", async () => {
  let posted: Record<string, unknown> = {};
  const client = new Camu(profile(), async (_url, init) => {
    posted = JSON.parse(String(init?.body));
    return Response.json({
      output: {
        data: {
          curntDte: new Date(fixed).toISOString(),
          facNme: "Test Mess",
          oMealList: [
            {
              mealTm: "Breakfast 07:00 AM - 09:00 AM",
              msNme: "Live breakfast",
            },
            { mealTm: "Dinner 08:00 PM - 10:00 PM", msNme: "Live dinner" },
          ],
        },
      },
    });
  });
  const text = await menu(client, undefined, fixed);
  assert.equal(posted.day, "SUN");
  assert.match(text, /Live dinner/);
  assert.doesNotMatch(text, /Live breakfast/);
  assert.match(await menu(client, "all", fixed), /Live breakfast/);
  const late = Date.parse("2026-11-01T17:00:00Z");
  assert.match(await menu(client, undefined, late), /service has finished/);
});
test("stale menu is rejected; no invented fallback", async () => {
  const c = new Camu(profile(), async () =>
    Response.json({
      output: { data: { curntDte: "2026-10-30T12:00:00Z", oMealList: [] } },
    }),
  );
  await assert.rejects(menu(c, undefined, fixed), /another date/);
});
test("attendance validates counts and calculates exact 75% advice", async () => {
  const c = new Camu(profile(), async () =>
    Response.json({
      output: {
        data: {
          OvrAllPrcntg: 75,
          OvrAllPCnt: 3,
          OvrAllCnt: 4,
          CurMnthPrcntg: 75,
          subjectList: [
            { SubjNm: "A", OvrAllPrcntg: 50, prsentCnt: 2, all: 4 },
            { SubjNm: "B", OvrAllPrcntg: 100, prsentCnt: 4, all: 4 },
          ],
        },
      },
    }),
  );
  const data = await attendance(c),
    subjects = data.subjects as { advice: string }[];
  assert.match(subjects[0].advice, /Attend 4/);
  assert.match(subjects[1].advice, /bunk 1/);
});
test("webhook rejects spoofed secret without any bot messages", async () => {
  const { env } = fixture();
  const result = await worker.fetch(
    new Request("https://test/telegram", { method: "POST", body: "{}" }),
    env,
  );
  assert.equal(result.status, 403);
});
test("mutating GET and query API keys rejected", async () => {
  const { env } = fixture();
  assert.equal(
    (await worker.fetch(new Request("https://test/quick-pass"), env)).status,
    405,
  );
  assert.equal(
    (await worker.fetch(new Request("https://test/leaves?key=anything"), env))
      .status,
    403,
  );
});
function update(
  text: string,
  from: { id: number; username?: string } = { id: 123, username: "owner_test" },
): Update {
  return {
    update_id: 1,
    message: {
      message_id: 10,
      chat: { id: from.id, type: "private" },
      from,
      text,
    },
  };
}
function telegramMock(t: { mock: typeof mock }) {
  const calls: { method: string; data: Record<string, unknown> }[] = [];
  t.mock.method(
    globalThis,
    "fetch",
    async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        method: String(input).split("/").at(-1)!,
        data: JSON.parse(String(init?.body)),
      });
      return Response.json({ ok: true, result: true });
    },
  );
  return calls;
}
test("owner requires BOTH numeric ID and exact username; unauthorized command silently ignored", async (t) => {
  const { env } = fixture(),
    calls = telegramMock(t),
    tg = new Telegram(env);
  for (const from of [
    { id: 124, username: "owner_test" },
    { id: 123, username: "wrong" },
    { id: 123 },
  ])
    await tg.handle(update("/private_users", from));
  assert.equal(calls.length, 0);
});
test("owner directory deletes password first, reveals only after verification, requires password each time", async (t) => {
  const { env, store } = fixture();
  Object.assign(env, {
    OWNER_PASSWORD_HASH: await ownerVerifier(
      "test-password-123",
      env.CREDENTIAL_KEY,
    ),
  });
  await store.saveAccount(8, profile());
  const calls = telegramMock(t),
    tg = new Telegram(env, store);
  await tg.handle(update("/private_users"));
  await tg.handle(update("test-password-123"));
  const deletion = calls.findIndex((c) => c.method === "deleteMessage"),
    reveal = calls.findIndex((c) =>
      String(c.data.text).includes("synthetic-secret"),
    );
  assert.ok(deletion >= 0 && reveal > deletion);
  assert.equal(await store.state(123), null);
  calls.length = 0;
  await tg.handle(update("/private_users"));
  assert.ok(
    !calls.some((c) => String(c.data.text).includes("synthetic-secret")),
  );
});
test("five wrong owner passwords cause persistent lockout", async (t) => {
  const { env, store, sql } = fixture();
  Object.assign(env, {
    OWNER_PASSWORD_HASH: await ownerVerifier(
      "test-password-123",
      env.CREDENTIAL_KEY,
    ),
  });
  telegramMock(t);
  const tg = new Telegram(env, store);
  for (let i = 0; i < 5; i++) {
    await tg.handle(update("/private_users"));
    await tg.handle(update("wrong-password-123"));
  }
  assert.ok(
    Number(
      sql.prepare("SELECT locked_until FROM owner_auth").get()!.locked_until,
    ) > Date.now(),
  );
});
test("Telegram retries of the same update send only one response", async (t) => {
  const { env } = fixture(),
    calls = telegramMock(t);
  const request = () =>
    new Request("https://test/telegram", {
      method: "POST",
      headers: {
        "X-Telegram-Bot-Api-Secret-Token": env.TELEGRAM_WEBHOOK_SECRET,
      },
      body: JSON.stringify(update("/myid")),
    });
  assert.equal((await worker.fetch(request(), env)).status, 200);
  assert.equal((await worker.fetch(request(), env)).status, 200);
  assert.equal(calls.filter((c) => c.method === "sendMessage").length, 1);
});
test("100 concurrent independent accounts complete mocked submissions and approval", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { store } = fixture();
  const results = await Promise.all(
    Array.from({ length: 100 }, async (_, i) => {
      const p = portal(),
        client = new Camu(profile(`parallel${i}@example.invalid`), p.network);
      const result = await client.createPass(store);
      return { result, creates: p.creates };
    }),
  );
  assert.ok(results.every((r) => r.result.success && r.creates === 1));
});
test("owner deletion failure prevents credential disclosure", async (t) => {
  const { env, store } = fixture();
  Object.assign(env, {
    OWNER_PASSWORD_HASH: await ownerVerifier(
      "test-password-123",
      env.CREDENTIAL_KEY,
    ),
  });
  await store.saveAccount(8, profile());
  const messages: string[] = [];
  t.mock.method(
    globalThis,
    "fetch",
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = String(input).split("/").at(-1),
        data = JSON.parse(String(init?.body));
      if (method === "sendMessage") messages.push(data.text);
      return Response.json({ ok: method !== "deleteMessage", result: true });
    },
  );
  const tg = new Telegram(env, store);
  await tg.handle(update("/private_users"));
  await tg.handle(update("test-password-123"));
  assert.ok(!messages.some((text) => text.includes("synthetic-secret")));
  assert.equal(await store.state(123), null);
});
function callback(data: string): Update {
  return {
    update_id: 99,
    callback_query: {
      id: "picker",
      data,
      from: { id: 123, username: "owner_test" },
      message: { message_id: 10, chat: { id: 123, type: "private" } },
    },
  };
}
test("after 7 PM a next-day pass time is selected entirely with clock buttons", async (t) => {
  t.mock.method(Date, "now", () => Date.parse("2026-11-01T20:00:00+05:30"));
  const { env, store } = fixture(),
    calls = telegramMock(t),
    tg = new Telegram(env, store);
  await store.saveAccount(123, profile());
  await tg.handle(update("/daypass"));
  assert.equal((await store.state(123))!.step, "tomorrow");
  await tg.handle(callback("tomorrow"));
  const hourPrompt = calls.at(-1)!.data;
  assert.match(String(hourPrompt.text), /Choose the hour/);
  assert.match(
    JSON.stringify(hourPrompt.reply_markup),
    /clock:2026-11-02:hour:18/,
  );
  await tg.handle(callback("clock:2026-11-02:hour:18"));
  await tg.handle(callback("clock:2026-11-02:minute:55"));
  await tg.handle(callback("clock:2026-11-02:adjust:1"));
  await tg.handle(callback("clock:2026-11-02:use:0"));
  const state = await store.state(123);
  assert.equal(state!.step, "confirm");
  assert.equal(state!.departure, "2026-11-02T18:56:00+05:30");
  assert.match(String(calls.at(-1)!.data.text), /Reason: Going out/);
  assert.ok(
    !JSON.stringify(calls.at(-1)!.data.reply_markup).includes("Change reason"),
  );
  assert.equal(calls.filter((c) => c.method === "editMessageText").length, 3);
});
test("picker wraps minute controls, supports back, and rejects invalid or stale buttons", async (t) => {
  t.mock.method(Date, "now", () => fixed);
  const { env, store } = fixture();
  telegramMock(t);
  const tg = new Telegram(env, store);
  await store.saveAccount(123, profile());
  await store.setState(123, { step: "time", date: "2026-11-02" });
  await assert.rejects(
    tg.handle(callback("clock:2026-11-01:hour:10")),
    /expired/,
  );
  await assert.rejects(
    tg.handle(callback("clock:2026-11-02:hour:19")),
    /Choose an hour/,
  );
  await tg.handle(callback("clock:2026-11-02:hour:6"));
  await tg.handle(callback("clock:2026-11-02:adjust:-1"));
  assert.equal((await store.state(123))!.minute, 59);
  await tg.handle(callback("clock:2026-11-02:adjust:1"));
  assert.equal((await store.state(123))!.minute, 0);
  await assert.rejects(
    tg.handle(callback("clock:2026-11-02:minute:60")),
    /valid minutes/,
  );
  await tg.handle(callback("clock:2026-11-02:back:0"));
  assert.equal((await store.state(123))!.hour, undefined);
  await assert.rejects(
    tg.handle(callback("clock:2026-11-02:use:0")),
    /hour first/,
  );
});
test("before 6 AM uses the picker and refuses times that have already passed", async (t) => {
  let now = Date.parse("2026-11-01T05:59:00+05:30");
  t.mock.method(Date, "now", () => now);
  const { env, store } = fixture(),
    calls = telegramMock(t),
    tg = new Telegram(env, store);
  await store.saveAccount(123, profile());
  await tg.handle(update("/daypass"));
  assert.equal((await store.state(123))!.date, "2026-11-01");
  assert.match(String(calls.at(-1)!.data.text), /Choose the hour/);
  await tg.handle(callback("clock:2026-11-01:hour:6"));
  now = Date.parse("2026-11-01T06:01:00+05:30");
  await assert.rejects(
    tg.handle(callback("clock:2026-11-01:use:0")),
    /future departure/,
  );
});
test("confirmation discards legacy custom reason drafts and has no reason-edit action", async (t) => {
  const { env, store } = fixture(),
    calls = telegramMock(t),
    tg = new Telegram(env, store);
  await tg.confirm(123, {
    step: "confirm",
    date: "2026-11-02",
    ...{ reason: "Legacy custom text" },
  });
  const saved = await store.state(123);
  assert.ok(!("reason" in saved!));
  assert.match(String(calls.at(-1)!.data.text), /Reason: Going out/);
  assert.ok(!JSON.stringify(calls.at(-1)!.data).includes("Legacy custom text"));
  assert.ok(
    !JSON.stringify(calls.at(-1)!.data.reply_markup).includes("reason"),
  );
});
