import re
from datetime import datetime
from day_pass import IST

MEAL_NAMES = {"breakfast": "Breakfast", "lunch": "Lunch", "snacks": "Snacks / High Tea", "dinner": "Dinner"}


def meal_type(meal):
    title = f"{meal.get('mealTm', '')} {meal.get('msCde', '')}".lower()
    for key, pattern in [("breakfast", r"\bbreakfast\b"), ("lunch", r"\blunch\b"),
                         ("snacks", r"\b(?:snacks?|high\s*tea)\b"), ("dinner", r"\bdinner\b")]:
        if re.search(pattern, title):
            return key
    return None


def serving_window(meal):
    match = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)\s*[-–]\s*(\d{1,2}:\d{2}\s*[AP]M)",
                      meal.get("mealTm", ""), re.IGNORECASE)
    if not match:
        return None
    try:
        return tuple(datetime.strptime(re.sub(r"\s+", "", value).upper(), "%I:%M%p").time()
                     for value in match.groups())
    except ValueError:
        return None


def get_meal_info(client, meal_key=None, now=None):
    now = (now or datetime.now(IST)).astimezone(IST)
    try:
        data = client.get_mess_menu()
        published_date = datetime.fromisoformat(data["curntDte"].replace("Z", "+00:00"))
        if published_date.tzinfo is None or published_date.astimezone(IST).date() != now.date():
            raise ValueError("MyCamu returned a menu for another date. Today's menu is unavailable.")
        meals = data.get("oMealList")
        if not isinstance(meals, list) or not meals:
            raise ValueError("MyCamu has not published a mess menu for your account today.")
        service_note = ""
        if meal_key == "all":
            selected = meals
        elif meal_key:
            if meal_key not in MEAL_NAMES:
                raise ValueError("Unknown meal selection.")
            selected = [meal for meal in meals if meal_type(meal) == meal_key]
            if not selected:
                raise ValueError(f"MyCamu has not published today's {MEAL_NAMES[meal_key].lower()} menu for your account.")
        else:
            timed = [(serving_window(meal), meal) for meal in meals]
            if all(window and window[0] <= window[1] for window, _ in timed):
                timed.sort(key=lambda item: item[0][0])
                active = [meal for (start, end), meal in timed if start <= now.time() < end]
                upcoming = [meal for (start, end), meal in timed if now.time() < start]
                selected = active or upcoming[:1] or [timed[-1][1]]
                if not active and not upcoming:
                    service_note = "Today’s meal service has finished. Showing the final meal; refresh after midnight IST for the new day’s menu."
                elif not active:
                    service_note = "Next serving shown below."
            else:
                selected = meals
        sections = []
        for meal in selected:
            dishes = meal.get("msNme", "").strip()
            if not dishes:
                raise ValueError("MyCamu has not published the dishes for this meal. Check the Cafeteria page.")
            title = meal.get("mealTm") or meal.get("msCde") or "Meal"
            sections.append(f"{title}\n{dishes}")
        return {"success": True, "day": now.strftime("%A, %d %b %Y"), "time": now.strftime("%I:%M %p IST"),
                "service_note": service_note, "facility": data.get("facNme", ""), "menu": "\n\n".join(sections), "source": "MyCamu Cafeteria"}
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    except Exception:
        return {"success": False, "message": "Could not fetch today's menu from MyCamu. Try again later or check Cafeteria in the portal."}


def menu_text(meal):
    if not meal.get("success"):
        return meal["message"]
    facility = f"\nMess: {meal['facility']}" if meal.get("facility") else ""
    note = f"\n{meal['service_note']}\n" if meal.get("service_note") else ""
    return (f"🍱 Bennett Mess Menu — {meal['day']}{facility}\n"
            f"🕒 {meal['time']}\n{note}\n{meal['menu']}\n\n"
            "Source: MyCamu Cafeteria (fetched now). Tap below for another meal today.")
