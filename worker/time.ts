import { UserError } from "./errors.ts";
export const OFFSET = 330 * 60000;
export function ist(now = Date.now()): string {
  return new Date(now + OFFSET).toISOString().slice(0, 19);
}
export function tomorrow(now = Date.now()): string {
  return ist(now + 86400000).slice(0, 10);
}
export function parseDeparture(value: string): number {
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$/.test(value)
  )
    throw new UserError("Departure must include a timezone.");
  const n = Date.parse(value);
  if (!Number.isFinite(n)) throw new UserError("Invalid departure.");
  return n;
}
export function windowFor(
  departure?: string,
  now = Date.now(),
):
  | { departure: string; return: string; day: string }
  | { needs_next_day: true } {
  const next = Math.floor(now / 60000) * 60000 + 60000;
  let local = departure ? ist(parseDeparture(departure)) : ist(next);
  if (!departure && local.slice(11, 16) < "06:00")
    local = `${local.slice(0, 10)}T06:00:00`;
  if (
    !departure &&
    (ist(now).slice(11, 16) >= "19:00" || local.slice(11, 16) >= "19:00")
  )
    return { needs_next_day: true };
  const day = local.slice(0, 10),
    today = ist(now).slice(0, 10),
    time = local.slice(11, 19);
  if (day !== today && day !== tomorrow(now))
    throw new UserError("Choose today or tomorrow.");
  if (
    time < "06:00:00" ||
    time >= "19:00:00" ||
    Date.parse(local + "+05:30") <= now
  )
    throw new UserError(
      "Choose a future departure from 6 AM to before 7 PM IST.",
    );
  return { departure: local + "+00:00", return: day + "T19:00:00+00:00", day };
}
export function timeMinutes(text: string): number | null {
  const match = /^(\d{1,2}):(\d{2})\s*([AP]M)$/i.exec(text.trim());
  if (!match) return null;
  const h = Number(match[1]),
    m = Number(match[2]);
  return h < 1 || h > 12 || m > 59
    ? null
    : ((h % 12) + (match[3].toUpperCase() === "PM" ? 12 : 0)) * 60 + m;
}
