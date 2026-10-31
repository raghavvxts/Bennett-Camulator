import { Store, type Profile } from "./storage.ts";
import { hash } from "./security.ts";
import { ist, windowFor, timeMinutes } from "./time.ts";
import { UserError } from "./errors.ts";
export { UserError } from "./errors.ts";
export interface GateRecord {
  _id: string;
  isGatepass?: boolean;
  LvTy?: string;
  Stus?: string;
  status?: string;
  passTyCode?: string;
  passTyNme?: string;
  frdt?: string;
  todt?: string;
  rson?: string;
  start?: string;
  end?: string;
  Rson?: string;
  prntAp?: string;
  isCOut?: boolean;
  isCIn?: boolean;
}
interface PassType {
  passCd: string;
  iSDPss: boolean;
  isStuNVsb?: boolean;
  StFl: string;
  isManDoc?: boolean;
  enblTm?: boolean;
  frTm?: string;
  toTm?: string;
}
export interface PassResult {
  success: boolean;
  message: string;
  needs_next_day?: boolean;
  pass_id?: string;
  status?: string;
  departure?: string;
  return?: string;
  reused_gate_pass?: boolean;
  reused_leave?: boolean;
}
export function status(r: GateRecord): string {
  const raw = r.isGatepass ? r.status : r.Stus;
  return (
    (
      {
        SUB: "Submitted",
        APPRVD: "Approved",
        CMPT: "Completed",
        CNCL: "Cancelled",
        REJ: "Rejected",
        EXP: "Expired",
      } as Record<string, string>
    )[raw || ""] ||
    raw ||
    "Unknown"
  );
}
export function covers(r: GateRecord, day: string): boolean {
  return Boolean(
    r.frdt &&
    r.todt &&
    r.frdt.slice(0, 10) <= day &&
    day <= r.todt.slice(0, 10),
  );
}
export function blocking(
  records: GateRecord[],
  day: string,
  today: string,
): GateRecord[] {
  return records.filter(
    (r) =>
      r.isGatepass &&
      ((r.isCOut && !r.isCIn) ||
        (status(r) !== "Completed" && (covers(r, day) || covers(r, today)))),
  );
}
export async function boundedJson(
  response: Response | Request,
  maximum = 4 * 1024 * 1024,
): Promise<unknown> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Empty response");
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > maximum) {
      await reader.cancel();
      throw new Error("Response too large");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const c of chunks) {
    bytes.set(c, offset);
    offset += c.length;
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid portal response");
  return value as Record<string, unknown>;
}
export class Camu {
  static BASE = "https://student.bennetterp.camu.in";
  private cachedRecords: GateRecord[] | null = null;
  constructor(
    public profile: Profile,
    readonly network: typeof fetch = (...args) => fetch(...args),
  ) {}
  async post(path: string, payload: unknown, login = false): Promise<unknown> {
    const response = await this.network(
      Camu.BASE + (login ? "" : "/api/") + path,
      {
        method: "POST",
        redirect: "manual",
        signal: AbortSignal.timeout(10000),
        headers: {
          "Content-Type": "application/json;charset=UTF-8",
          Accept: "application/json",
          Origin: Camu.BASE,
          Referer: Camu.BASE + "/",
          Cookie: Object.entries(this.profile.cookies)
            .map(([k, v]) => `${k}=${v}`)
            .join("; "),
        },
        body: JSON.stringify(payload),
      },
    );
    for (const cookie of response.headers.getSetCookie()) {
      const first = cookie.split(";")[0],
        i = first.indexOf("=");
      if (i > 0) this.profile.cookies[first.slice(0, i)] = first.slice(i + 1);
    }
    if (response.status === 429)
      throw new UserError(
        "MyCamu rate limit reached. Wait a few minutes before trying again.",
      );
    if (!response.ok)
      throw new UserError(
        "MyCamu is unavailable. Try again later; check existing passes before retrying.",
      );
    const body = object(await boundedJson(response));
    const output = object(body.output);
    if (
      (output.errors &&
        (typeof output.errors !== "object" ||
          Object.keys(output.errors).length > 0)) ||
      output.data == null
    )
      throw new UserError(
        "MyCamu did not return valid data. Check the portal before retrying.",
      );
    return output.data;
  }
  async login(): Promise<void> {
    const data = object(
      await this.post(
        "/login/validate",
        { Email: this.profile.email, pwd: this.profile.password, dtype: "w" },
        true,
      ),
    );
    if (data.code)
      throw new UserError(
        data.code === "ACC_LOCKED"
          ? "MyCamu account temporarily locked."
          : data.code === "INVALID_CRED"
            ? "Invalid email or password."
            : "MyCamu login failed.",
      );
    const details = object(data.logindetails),
      students = details.Student;
    if (!Array.isArray(students) || !students.length)
      throw new UserError("No student linked to this parent account.");
    const student = object(students[0]);
    if (
      typeof student.StuID !== "string" ||
      typeof details.InId !== "string" ||
      typeof details._id !== "string" ||
      details.Type !== "parent"
    )
      throw new UserError("Please link a parent MyCamu account.");
    Object.assign(this.profile, {
      stu_id: student.StuID,
      in_id: details.InId,
      logged_in_id: details._id,
      account_type: details.Type,
      student_name: `${student.FNa || ""} ${student.LNa || ""}`.trim(),
    });
  }
  async connect(): Promise<void> {
    if (this.profile.stu_id && this.profile.logged_in_id) {
      try {
        this.cachedRecords = await this.records(true);
        return;
      } catch (e) {
        if (e instanceof UserError && e.message.includes("rate limit")) throw e;
      }
    }
    await this.login();
  }
  async records(fresh = false): Promise<GateRecord[]> {
    if (this.cachedRecords && !fresh) {
      const records = this.cachedRecords;
      this.cachedRecords = null;
      return records;
    }
    const data = await this.post("Leave/getAllleave/", {
      StuID: this.profile.stu_id,
      isFrmReact: true,
    });
    if (
      !Array.isArray(data) ||
      data.some((r) => !r || typeof r !== "object" || typeof r._id !== "string")
    )
      throw new UserError("Could not verify leave and gate-pass records.");
    return data as GateRecord[];
  }
  async active(): Promise<Record<string, unknown>> {
    const records = await this.records(),
      today = ist().slice(0, 10);
    const gates = blocking(records, today, today).sort(
      (a, b) =>
        Number(Boolean(b.isCOut && !b.isCIn)) -
        Number(Boolean(a.isCOut && !a.isCIn)),
    );
    const r = gates[0];
    return {
      success: true,
      has_active_pass: Boolean(r),
      is_outside_campus: Boolean(r?.isCOut && !r?.isCIn),
      pass: r || null,
      status: r ? status(r) : undefined,
      start: r?.frdt,
      end: r?.todt,
      reason: r?.rson,
      type: r?.passTyNme,
    };
  }
  async approve(record: GateRecord): Promise<PassResult> {
    if (!record._id) throw new UserError("Gate pass has no ID. Check MyCamu.");
    if (status(record) !== "Approved" && record.prntAp !== "APPRVD") {
      const result = object(
        await this.post("gatepasses/change-gatepasses-status", {
          id: record._id,
          roll: "parent",
          action: "Approve",
          PerId: this.profile.stu_id,
          loggedInId: this.profile.logged_in_id,
        }),
      );
      if (result.code !== "STATUS_UPDTD_SUCC")
        throw new UserError(
          "Gate pass submitted; approval not confirmed. Check MyCamu before retrying.",
        );
    }
    const data = object(
        await this.post("gatepasses/fetch-gatepasses-status", {
          id: record._id,
        }),
      ),
      gate = object(data.gatepass);
    if (gate._id !== record._id || typeof gate.status !== "string")
      throw new UserError("Could not verify gate pass approval. Check MyCamu.");
    const finalStatus = status({ ...gate, isGatepass: true } as GateRecord),
      approved = finalStatus === "Approved";
    return {
      success: approved,
      status: finalStatus,
      pass_id: record._id,
      message: approved
        ? "Day Out Pass approved."
        : "Gate pass exists; final approval is pending. Do not create another pass.",
      departure: String(gate.frdt || ""),
      return: String(gate.todt || ""),
    };
  }
  async createPass(
    store: Store,
    reason = "Going out",
    departure?: string,
  ): Promise<PassResult> {
    if (!reason.trim() || reason.length > 500)
      throw new UserError("Enter an outing reason of 1–500 characters.");
    let window = windowFor(departure);
    if ("needs_next_day" in window)
      return {
        success: false,
        needs_next_day: true,
        message: "Today's day-pass window has ended. Apply for tomorrow?",
      };
    const account = await hash(this.profile.email.trim().toLowerCase()),
      key = "pass:" + account,
      lease = await store.lease(key);
    if (!lease)
      throw new UserError(
        "A request for this MyCamu account is already running. Please wait.",
      );
    try {
      const records = await this.records();
      if (records.some((r) => r.isGatepass && r.isCOut && !r.isCIn))
        throw new UserError(
          "Your current gate pass is checked OUT. Wait until MyCamu marks it Completed.",
        );
      const config = object(
        await this.post("gatepasses/fetch-gatepasses-types", {}),
      );
      if (!Array.isArray(config.gTypes))
        throw new UserError("Could not fetch configured pass types.");
      const types = (config.gTypes as PassType[]).filter(
        (t) => t.iSDPss && !t.isStuNVsb && t.StFl === "A",
      );
      if (types.length !== 1 || !types[0].passCd)
        throw new UserError(
          "Could not identify the configured Day Out Pass type.",
        );
      const type = types[0];
      if (type.isManDoc)
        throw new UserError(
          "MyCamu now requires documents. Apply in the portal.",
        );
      const active = blocking(records, window.day, ist().slice(0, 10));
      if (active.length) {
        const existing = active[0];
        if (
          active.length !== 1 ||
          existing.passTyCode !== type.passCd ||
          !covers(existing, window.day) ||
          !["Submitted", "Approved"].includes(status(existing))
        )
          throw new UserError(
            "An existing gate pass is not Completed. No new pass created, regardless of whether you are inside or outside campus.",
          );
        await store.uncertain(account, true);
        const result = await this.approve(existing);
        if (result.success) await store.uncertain(account, false);
        return { ...result, reused_gate_pass: true };
      }
      if (await store.uncertain(account))
        throw new UserError(
          "A previous submission has an uncertain result. Check MyCamu; no new pass was submitted.",
        );
      // Recompute after network checks so immediate departure remains in the future.
      window = windowFor(departure);
      if ("needs_next_day" in window)
        return {
          success: false,
          needs_next_day: true,
          message: "Today's window ended while checking. Apply for tomorrow?",
        };
      const min = timeMinutes(type.frTm || ""),
        max = timeMinutes(type.toTm || "");
      if (
        type.enblTm &&
        (min === null ||
          max === null ||
          Number(window.departure.slice(11, 13)) * 60 +
            Number(window.departure.slice(14, 16)) <
            min ||
          max < 19 * 60)
      )
        throw new UserError(
          "Requested times are outside the current MyCamu pass window.",
        );
      const day = window.day,
        hasLeave = records.some(
          (r) =>
            !r.isGatepass &&
            r.LvTy === "Leave" &&
            !["Rejected", "Cancelled"].includes(status(r)) &&
            r.start &&
            r.end &&
            r.start.slice(0, 10) <= day &&
            day <= r.end.slice(0, 10),
        );
      const p = this.profile;
      const payload = {
        PerTy: "Student",
        PerId: p.stu_id,
        PerNm: p.student_name,
        InId: p.in_id,
        HstleReq: "true",
        GateOutPass: true,
        Type: "parent",
        stuId: p.stu_id,
        loggedInId: p.logged_in_id,
        passTy: type.passCd,
        rson: reason.trim(),
        atcmnt: [],
        periods: [],
        Attachments: [],
        timezoneOffSet: 330,
        frdt: window.departure,
        todt: window.return,
        ...(!hasLeave
          ? { LvTy: "Leave", Rson: reason.trim(), FrDt: day, ToDt: day }
          : {}),
      };
      await store.uncertain(account, true);
      const result = object(await this.post("Leave/crtLv", payload));
      const refusals: Record<string, string> = {
        LV_NEEDED: "MyCamu requires a leave for this date.",
        DUP_GATEPASS_EXISTS: "A gate pass already exists.",
        LEAVE_MISSING_ON_WORKING_DAY:
          "MyCamu requires a leave for this working day.",
        DAY_IS_HOLIDAY: "MyCamu refused leave on this holiday.",
        SUSPEND_STUDENT: "Student status prevents this request.",
        STUD_HLD_IN_CLR: "A clearance hold prevents this pass.",
        LEAVE_CANNOT_BE_APPLIED_ATTENDANCE_PRESENT:
          "Attendance is marked present; MyCamu refused leave.",
        WRONG_TIME_RANGE: "Requested times are outside the allowed range.",
        NOT_WITHIN_TIME: "Requested time does not match MyCamu periods.",
      };
      if (result.code && result.code !== "ONLY_GATEPASS_CRTD") {
        const message = refusals[String(result.code)];
        if (message) await store.uncertain(account, false);
        throw new UserError(
          (message || "MyCamu did not confirm creation.") +
            " Check MyCamu before retrying.",
        );
      }
      if (!result.code && !result._id)
        throw new UserError(
          "Creation was not confirmed. Check MyCamu before retrying.",
        );
      const old = new Set(records.map((r) => r._id));
      const matches = (await this.records(true)).filter(
        (r) =>
          r.isGatepass &&
          !old.has(r._id) &&
          r.passTyCode === type.passCd &&
          r.frdt?.slice(0, 16) === payload.frdt.slice(0, 16) &&
          r.todt?.slice(0, 16) === payload.todt.slice(0, 16) &&
          r.rson === reason.trim(),
      );
      if (matches.length !== 1)
        throw new UserError(
          "Submission sent, but the new gate pass could not be identified. Check MyCamu before retrying.",
        );
      const approved = await this.approve(matches[0]);
      if (approved.success) await store.uncertain(account, false);
      return { ...approved, reused_leave: hasLeave };
    } finally {
      await store.release(key, lease);
    }
  }
}
