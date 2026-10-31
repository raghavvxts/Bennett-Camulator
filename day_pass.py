from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def day_pass_window(departure_at=None, now=None):
    now = (now or datetime.now(IST)).astimezone(IST)
    if departure_at is None:
        if now.time() >= time(19):
            return {"success": False, "needs_next_day": True,
                    "message": "Today's day-pass window has ended. Apply for tomorrow?"}
        departure_at = max(
            now.replace(second=0, microsecond=0) + timedelta(minutes=1),
            datetime.combine(now.date(), time(6), IST),
        )
        if departure_at.time() >= time(19):
            return {"success": False, "needs_next_day": True,
                    "message": "Today's day-pass window has ended. Apply for tomorrow?"}
    elif departure_at.tzinfo is None:
        return {"success": False, "message": "Departure must include a timezone."}
    departure_at = departure_at.astimezone(IST)
    if departure_at.date() not in (now.date(), now.date() + timedelta(days=1)):
        return {"success": False, "message": "Choose today or tomorrow."}
    if departure_at <= now or not time(6) <= departure_at.time() < time(19):
        return {"success": False, "message": "Choose a future departure between 6:00 AM and 7:00 PM IST (before 7 PM)."}
    return {"success": True, "departure": departure_at,
            "return": datetime.combine(departure_at.date(), time(19), IST)}
