from datetime import datetime, time, timezone, timedelta

# Bennett University Standard Hostel Mess Menu (7-Day Rotation)
BENNETT_MESS_MENU = {
    "Monday": {
        "breakfast": "Aloo Paratha, Curd, Pickle, Boiled Eggs / Omelette, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Rajma Masala, Aloo Jeera, Steamed Rice, Roti, Boondi Raita, Salad, Gulab Jamun",
        "snacks": "Veg Cutlet / Samosa, Mint Chutney, Tea & Coffee",
        "dinner": "Paneer Butter Masala, Dal Makhani, Jeera Rice, Chapati, Kheer, Green Salad"
    },
    "Tuesday": {
        "breakfast": "Idli, Vada, Sambar, Coconut Chutney, Boiled Eggs, Toast, Butter, Jam, Tea & Coffee",
        "lunch": "Kadhi Pakora, Aloo Methi, Steamed Rice, Phulka, Mix Veg Raita, Salad, Papad",
        "snacks": "Poha with Sev, Lemon, Tea & Coffee",
        "dinner": "Mix Veg Curry, Dal Tadka, Peas Pulao, Butter Roti, Ice Cream, Salad"
    },
    "Wednesday": {
        "breakfast": "Puri Bhaji, Halwa, Scrambled Eggs, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Chole Bhature / Rice, Aloo Gobhi Dry, Boondi Raita, Onion Salad, Pickle",
        "snacks": "Pasta / White Sauce Macaroni, Sauce, Tea & Coffee",
        "dinner": "Egg Curry / Shahi Paneer, Dal Fry, Jeera Rice, Tandoori Roti, Custard, Salad"
    },
    "Thursday": {
        "breakfast": "Uttapam, Sambar, Tomato Chutney, Boiled Eggs, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Dal Makhani, Bhindi Masala, Steamed Rice, Phulka, Cucumber Raita, Salad",
        "snacks": "Bread Pakora, Green Chutney, Tea & Coffee",
        "dinner": "Kadai Paneer, Yellow Dal, Veg Biryani / Pulao, Naan/Roti, Sewaiyan, Salad"
    },
    "Friday": {
        "breakfast": "Pav Bhaji, Boiled Eggs, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Rajma Chawal, Aloo Shimla Mirch, Roti, Curd, Green Salad, Papad",
        "snacks": "Bhel Puri / Maggi, Tea & Coffee",
        "dinner": "Chicken Curry / Paneer Lababdar, Dal Tadka, Steamed Rice, Tandoori Roti, Halwa, Salad"
    },
    "Saturday": {
        "breakfast": "Methi Thepla / Paratha, Curd, Omelette, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Chana Masala, Aloo Baingan, Steamed Rice, Phulka, Pineapple Raita, Salad",
        "snacks": "Veg Sandwich, Tomato Ketchup, Tea & Coffee",
        "dinner": "Matar Paneer, Dal Fry, Jeera Rice, Butter Naan, Sweet / Brownie, Salad"
    },
    "Sunday": {
        "breakfast": "Masala Dosa, Sambar, Coconut & Tomato Chutney, Boiled Eggs, Bread, Butter, Tea",
        "lunch": "Special Veg Dum Biryani / Chicken Biryani, Mirchi Ka Salan, Boondi Raita, Gulab Jamun",
        "snacks": "Cookies / Biscuits, French Fries, Tea & Coffee",
        "dinner": "Special Thali: Shahi Paneer, Dal Makhani, Pulao, Missi Roti, Ice Cream"
    }
}

IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    """Returns current datetime in Indian Standard Time (IST)."""
    return datetime.now(IST)

def get_current_meal_type():
    """
    Determines meal type according to the time of day:
    - 00:00 - 11:00: Breakfast
    - 11:00 - 15:30: Lunch
    - 15:30 - 19:00: High Tea / Snacks
    - 19:00 - 23:59: Dinner
    """
    now = get_ist_now().time()
    if now < time(11, 0):
        return "breakfast", "🍳 Breakfast (07:30 - 10:30)"
    elif now < time(15, 30):
        return "lunch", "🍛 Lunch (12:00 - 14:30)"
    elif now < time(19, 0):
        return "snacks", "☕ High Tea / Snacks (17:00 - 18:30)"
    else:
        return "dinner", "🍲 Dinner (19:30 - 22:30)"

def get_meal_info(meal_key: str = None, day: str = None) -> dict:
    """Returns the menu for given meal or current meal according to time."""
    now_ist = get_ist_now()
    current_day = day or now_ist.strftime("%A")

    if not meal_key:
        meal_key, meal_label = get_current_meal_type()
    else:
        labels = {
            "breakfast": "🍳 Breakfast (07:30 - 10:30)",
            "lunch": "🍛 Lunch (12:00 - 14:30)",
            "snacks": "☕ High Tea / Snacks (17:00 - 18:30)",
            "dinner": "🍲 Dinner (19:30 - 22:30)"
        }
        meal_label = labels.get(meal_key, meal_key.capitalize())

    day_menu = BENNETT_MESS_MENU.get(current_day, BENNETT_MESS_MENU["Monday"])
    menu_items = day_menu.get(meal_key, "Menu not available.")

    return {
        "day": current_day,
        "time": now_ist.strftime("%I:%M %p"),
        "meal_key": meal_key,
        "meal_label": meal_label,
        "menu": menu_items
    }
