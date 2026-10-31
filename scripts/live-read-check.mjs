// Read-only integration check. Credentials arrive over stdin; no pass mutations.
import { Camu } from "../worker/camu.ts";
import { seal, hash, randomToken } from "../worker/security.ts";
import fs from "node:fs";
import { execFileSync } from "node:child_process";
const config = JSON.parse(fs.readFileSync(0, "utf8"));
const secrets = JSON.parse(fs.readFileSync(config.secretsPath, "utf8"));
const p = {
  email: config.email,
  password: config.password,
  student_name: "",
  stu_id: "",
  in_id: "",
  logged_in_id: "",
  account_type: "",
  cookies: {},
};
await new Camu(p).login();
const id = -Math.floor(Date.now() / 1000),
  key = randomToken();
function query(sql, params = []) {
  return execFileSync(
    "cf",
    [
      "d1",
      "query",
      config.databaseId,
      "--sql",
      sql,
      ...(params.length ? ["--params", ...params.map(String)] : []),
    ],
    { encoding: "utf8" },
  );
}
let successful = true;
try {
  query(
    "INSERT INTO accounts(id,sealed,linked_at,token_hash) VALUES(?,?,?,?)",
    [
      id,
      await seal(p, secrets.CREDENTIAL_KEY, `account:${id}`),
      new Date().toISOString(),
      await hash(key),
    ],
  );
  for (const endpoint of config.endpoints || [
    "attendance",
    "leaves",
    "active-pass",
    "menu",
  ]) {
    const response = await fetch(new URL("/" + endpoint, config.url), {
      headers: { "X-API-Key": key },
      signal: AbortSignal.timeout(30000),
    });
    const body = await response.json(),
      ok = response.ok && (body.success === true || Array.isArray(body.leaves));
    successful &&= ok;
    console.log(
      JSON.stringify({
        endpoint,
        http: response.status,
        success: ok,
        records: Array.isArray(body.leaves) ? body.leaves.length : undefined,
        subjects: Array.isArray(body.subjects)
          ? body.subjects.length
          : undefined,
        message: body.message,
      }),
    );
  }
} finally {
  query("DELETE FROM accounts WHERE id=?", [id]);
  console.log("Temporary integration account removed. No pass mutations.");
}
if (!successful) process.exitCode = 1;
