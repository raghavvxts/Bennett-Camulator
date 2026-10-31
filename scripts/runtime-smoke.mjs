import { Miniflare, convertV4MiniflareOptions } from "miniflare";
import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";
import { base64, seal, hash, ownerVerifier } from "../worker/security.ts";
import { tomorrow } from "../worker/time.ts";
const key = base64(crypto.getRandomValues(new Uint8Array(32)));
const verifier = await ownerVerifier("synthetic-owner-password", key);
const messages = [];
let records = [],
  creates = 0;
const outbound = async (request) => {
  const url = new URL(request.url);
  if (url.hostname === "student.bennetterp.camu.in") {
    if (url.pathname === "/api/Leave/getAllleave/")
      return Response.json({ output: { data: records } });
    if (url.pathname === "/api/gatepasses/fetch-gatepasses-types")
      return Response.json({
        output: {
          data: {
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
          },
        },
      });
    if (url.pathname === "/api/Leave/crtLv") {
      const payload = await request.json();
      creates++;
      assert.equal(payload.FrDt, payload.ToDt);
      assert.equal(payload.LvTy, "Leave");
      assert.equal(payload.rson, "Going out");
      records = [
        {
          _id: "runtime-gate",
          isGatepass: true,
          passTyCode: "DOP",
          frdt: payload.frdt,
          todt: payload.todt,
          rson: payload.rson,
          status: "SUB",
        },
      ];
      return Response.json({
        output: { data: { code: "ONLY_GATEPASS_CRTD" } },
      });
    }
    if (url.pathname === "/api/gatepasses/change-gatepasses-status") {
      records[0].status = "APPRVD";
      records[0].prntAp = "APPRVD";
      return Response.json({ output: { data: { code: "STATUS_UPDTD_SUCC" } } });
    }
    if (url.pathname === "/api/gatepasses/fetch-gatepasses-status")
      return Response.json({ output: { data: { gatepass: records[0] } } });
    if (url.pathname === "/api/Attendance/getDtaForStupage")
      return Response.json({
        output: {
          data: {
            OvrAllPrcntg: 75,
            OvrAllPCnt: 3,
            OvrAllCnt: 4,
            CurMnthPrcntg: 75,
            subjectList: [],
          },
        },
      });
  }
  if (url.hostname === "api.telegram.org") {
    if (url.pathname.endsWith("/sendMessage"))
      messages.push(await request.json());
    return Response.json({ ok: true, result: true });
  }
  throw new Error("Unexpected outbound request in runtime test");
};
const mf = new Miniflare(
  convertV4MiniflareOptions({
    outboundService: outbound,
    modules: true,
    scriptPath: ".cloudflare/output/v0/workers/default/bundle/index.js",
    // Current local workerd trails production by one day; smoke-test the same bundle.
    compatibilityDate: "2026-10-30",
    d1Databases: ["DB"],
    bindings: {
      CREDENTIAL_KEY: key,
      TELEGRAM_BOT_TOKEN: "synthetic",
      TELEGRAM_WEBHOOK_SECRET: "synthetic-hook",
      OWNER_TELEGRAM_ID: "123",
      OWNER_TELEGRAM_USERNAME: "owner_test",
      OWNER_COMMAND: "private_users",
      OWNER_PASSWORD_HASH: verifier,
    },
  }),
);
try {
  const db = await mf.getD1Database("DB");
  await db.exec(await readFile("worker/schema.sql", "utf8"));
  const p = {
    email: "test@example.invalid",
    password: "runtime-test-secret",
    student_name: "Runtime Test",
    stu_id: "student",
    in_id: "institute",
    logged_in_id: "parent",
    account_type: "parent",
    cookies: { session: "synthetic" },
  };
  await db
    .prepare(
      "INSERT INTO accounts(id,sealed,linked_at,token_hash) VALUES(?,?,?,?)",
    )
    .bind(
      123,
      await seal(p, key, "account:123"),
      new Date().toISOString(),
      await hash("synthetic-shortcut"),
    )
    .run();
  assert.equal(
    (await mf.dispatchFetch("https://example.invalid/health")).status,
    200,
  );
  assert.equal(
    (
      await mf.dispatchFetch("https://example.invalid/telegram", {
        method: "POST",
        body: "{}",
      })
    ).status,
    403,
  );
  const att = await mf.dispatchFetch("https://example.invalid/attendance", {
    headers: { "X-API-Key": "synthetic-shortcut" },
  });
  assert.equal(att.status, 200, await att.clone().text());
  assert.equal((await att.json()).overall_pct, 75);
  const create = () =>
    mf.dispatchFetch("https://example.invalid/quick-pass", {
      method: "POST",
      headers: {
        "X-API-Key": "synthetic-shortcut",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ departure_at: tomorrow() + "T10:00:00+05:30" }),
    });
  const first = await create();
  assert.equal(first.status, 200, await first.clone().text());
  assert.equal((await first.json()).status, "Approved");
  const reused = await create();
  assert.equal((await reused.json()).reused_gate_pass, true);
  assert.equal(creates, 1);
  const request = async (id, text) =>
    mf.dispatchFetch("https://example.invalid/telegram", {
      method: "POST",
      headers: { "X-Telegram-Bot-Api-Secret-Token": "synthetic-hook" },
      body: JSON.stringify({
        update_id: id,
        message: {
          message_id: id,
          chat: { id: 123, type: "private" },
          from: { id: 123, username: "owner_test" },
          text,
        },
      }),
    });
  assert.equal((await request(1, "/private_users")).status, 200);
  assert.equal((await request(2, "synthetic-owner-password")).status, 200);
  assert.ok(
    messages.some((m) => m.text.includes("runtime-test-secret")),
    "Owner decryption and PBKDF2 must work in actual Workers runtime",
  );
  const before = messages.length;
  await request(2, "synthetic-owner-password");
  assert.equal(messages.length, before);
  console.log(
    "Workers runtime smoke passed: encrypted D1 storage, portal fetch, pass submission/approval/reuse, attendance, owner verification and webhook deduplication.",
  );
} finally {
  await mf.dispose();
}
