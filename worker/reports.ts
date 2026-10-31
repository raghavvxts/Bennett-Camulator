import { Camu, object, UserError } from "./camu.ts";
import { ist, timeMinutes } from "./time.ts";
export function mealType(title: string): string | undefined {
  return ["breakfast", "lunch", "snacks", "dinner"].find((k) =>
    new RegExp(
      k === "snacks" ? "\\b(?:snacks?|high\\s*tea)\\b" : `\\b${k}\\b`,
      "i",
    ).test(title),
  );
}
export async function menu(
  client: Camu,
  selection?: string,
  now = Date.now(),
): Promise<string> {
  const local = ist(now),
    day = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"][
      new Date(local + "Z").getUTCDay()
    ];
  const data = object(
    await client.post("mess-management/get-student-menu-list", {
      stuId: client.profile.stu_id,
      InId: client.profile.in_id,
      day,
    }),
  );
  if (
    typeof data.curntDte !== "string" ||
    !/(Z|[+-]\d\d:\d\d)$/.test(data.curntDte) ||
    !Number.isFinite(Date.parse(data.curntDte)) ||
    ist(Date.parse(data.curntDte)).slice(0, 10) !== local.slice(0, 10)
  )
    throw new UserError(
      "MyCamu returned another date. Today's menu is unavailable.",
    );
  if (!Array.isArray(data.oMealList) || !data.oMealList.length)
    throw new UserError(
      "MyCamu has not published today's menu for your account.",
    );
  const meals = data.oMealList.map((m) => {
    const meal = object(m);
    if (typeof meal.msNme !== "string" || !meal.msNme.trim())
      throw new UserError(
        "Menu dishes are missing. Check Cafeteria in MyCamu.",
      );
    return {
      title: String(meal.mealTm || meal.msCde || "Meal"),
      dishes: meal.msNme,
      code: String(meal.msCde || ""),
    };
  });
  let selected = meals,
    note = "";
  if (selection && selection !== "all") {
    if (!["breakfast", "lunch", "snacks", "dinner"].includes(selection))
      throw new UserError("Unknown meal selection.");
    selected = meals.filter(
      (m) => mealType(m.title + " " + m.code) === selection,
    );
    if (!selected.length)
      throw new UserError(
        `MyCamu has not published today's ${selection} menu.`,
      );
  } else if (!selection) {
    const timed = meals.map((meal) => {
      const m =
        /(\d{1,2}:\d{2}\s*[AP]M)\s*[-–]\s*(\d{1,2}:\d{2}\s*[AP]M)/i.exec(
          meal.title,
        );
      return {
        meal,
        start: m ? timeMinutes(m[1]) : null,
        end: m ? timeMinutes(m[2]) : null,
      };
    });
    if (
      timed.every((t) => t.start !== null && t.end !== null && t.start <= t.end)
    ) {
      timed.sort((a, b) => a.start! - b.start!);
      const current =
        Number(local.slice(11, 13)) * 60 + Number(local.slice(14, 16));
      const active = timed.filter(
          (t) => t.start! <= current && current < t.end!,
        ),
        next = timed.find((t) => current < t.start!);
      selected = active.length
        ? active.map((t) => t.meal)
        : [next?.meal || timed.at(-1)!.meal];
      note = active.length
        ? ""
        : next
          ? "Next serving."
          : "Today's meal service has finished. Refresh after midnight IST for the new day's menu.";
    }
  }
  return `🍱 Mess Menu — ${local.slice(0, 10)}\n${data.facNme ? "Mess: " + String(data.facNme) + "\n" : ""}Fetched ${local.slice(11, 16)} IST\n${note ? note + "\n" : ""}\n${selected.map((m) => m.title + "\n" + m.dishes).join("\n\n")}\n\nSource: MyCamu Cafeteria (fetched now).`;
}
function number(value: unknown, max = Infinity, integer = false): number {
  if ((typeof value !== "number" && typeof value !== "string") || value === "")
    throw new UserError("Invalid attendance data.");
  const n = Number(value);
  if (
    !Number.isFinite(n) ||
    n < 0 ||
    n > max ||
    (integer && !Number.isInteger(n))
  )
    throw new UserError("Invalid attendance data.");
  return n;
}
export async function attendance(
  client: Camu,
): Promise<Record<string, unknown>> {
  const d = object(
    await client.post("Attendance/getDtaForStupage", {
      StuID: client.profile.stu_id,
    }),
  );
  const overall_pct = number(d.OvrAllPrcntg, 100),
    overall_present = number(d.OvrAllPCnt, Infinity, true),
    overall_total = number(d.OvrAllCnt, Infinity, true),
    month_pct = number(d.CurMnthPrcntg, 100);
  if (overall_present > overall_total || !Array.isArray(d.subjectList))
    throw new UserError("Invalid attendance data.");
  const subjects = d.subjectList.map((value) => {
    const s = object(value),
      p = number(s.prsentCnt, Infinity, true),
      tot = number(s.all, Infinity, true),
      pct = number(s.OvrAllPrcntg, 100),
      absent = number(s.absentCnt ?? 0, Infinity, true);
    if (p > tot) throw new UserError("Invalid attendance counts.");
    return {
      name: String(s.SubjNm || "Unknown"),
      code: String(s.SubjCd || ""),
      percentage: pct,
      present: p,
      total: tot,
      absent,
      advice:
        tot === 0
          ? "No classes recorded yet."
          : 4 * p >= 3 * tot
            ? `🟢 Can bunk ${Math.floor((4 * p - 3 * tot) / 3)} class(es).`
            : `🔴 Attend ${3 * tot - 4 * p} class(es) for 75%.`,
    };
  });
  return {
    success: true,
    overall_pct,
    overall_present,
    overall_total,
    month_pct,
    subjects,
  };
}
export function attendanceText(data: Record<string, unknown>): string {
  const subjects = data.subjects as {
    name: string;
    code: string;
    percentage: number;
    present: number;
    total: number;
    advice: string;
  }[];
  return (
    `📊 Live attendance\nOverall: ${data.overall_pct}% (${data.overall_present}/${data.overall_total})\nThis month: ${data.month_pct}%\n\n` +
    subjects
      .map(
        (s) =>
          `${s.name} ${s.code}\n${s.percentage}% (${s.present}/${s.total})\n${s.advice}`,
      )
      .join("\n\n")
  );
}
