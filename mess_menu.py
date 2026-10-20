from datetime import datetime, time, timezone, timedelta

# Bennett University Hostel Mess Menu (7-Day Rotation)
BENNETT_MESS_MENU = {
    "Monday": {
        "breakfast": "Aloo Pyaz Paratha, Curd, Pickle, Boiled Eggs / Omelette, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Rajma Masala, Aloo Jeera, Steamed Rice, Roti, Boondi Raita, Fresh Green Salad, Gulab Jamun",
        "snacks": "Veg Cutlet / Samosa, Mint & Tamarind Chutney, Hot Tea & Coffee",
        "dinner": "Paneer Butter Masala, Dal Makhani, Jeera Rice, Tawa Butter Roti, Kheer, Salad"
    },
    "Tuesday": {
        "breakfast": "Idli, Medu Vada, Sambar, Coconut Chutney, Boiled Eggs, Toast, Butter, Jam, Tea & Coffee",
        "lunch": "Punjabi Kadhi Pakora, Aloo Methi, Steamed Basmati Rice, Phulka, Mix Veg Raita, Salad, Papad",
        "snacks": "Indori Poha with Sev & Fried Peanuts, Lemon, Hot Tea & Coffee",
        "dinner": "Mix Veg Curry, Dal Tadka, Peas Pulao, Butter Roti, Ice Cream / Sweet, Salad"
    },
    "Wednesday": {
        "breakfast": "Puri Bhaji, Suji Halwa, Scrambled Eggs, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Amritsari Chole Bhature / Jeera Rice, Aloo Gobhi Dry, Boondi Raita, Onion Pickle Salad",
        "snacks": "Red Sauce / White Sauce Pasta, Chilli Flakes & Oregano, Hot Tea & Coffee",
        "dinner": "Egg Curry / Shahi Paneer, Dal Fry, Jeera Rice, Tandoori Roti, Fruit Custard, Salad"
    },
    "Thursday": {
        "breakfast": "Uttapam, Sambar, Tomato & Coconut Chutney, Boiled Eggs, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Dal Makhani, Bhindi Masala, Steamed Rice, Phulka, Cucumber Mint Raita, Salad",
        "snacks": "Bread Pakora with Green Chutney & Sauce, Hot Tea & Coffee",
        "dinner": "Kadai Paneer, Yellow Dal Tadka, Veg Dum Pulao, Butter Naan / Tandoori Roti, Sewaiyan, Salad"
    },
    "Friday": {
        "breakfast": "Pav Bhaji (Amul Butter), Boiled Eggs, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Rajma Chawal, Aloo Shimla Mirch, Fresh Phulka, Plain Curd, Green Salad, Roasted Papad",
        "snacks": "Bhel Puri / Maggi Noodles, Tomato Ketchup, Hot Tea & Coffee",
        "dinner": "Chicken Curry / Paneer Lababdar, Dal Tadka, Steamed Rice, Tandoori Roti, Gajar Halwa, Salad"
    },
    "Saturday": {
        "breakfast": "Methi Thepla / Gobhi Paratha, Curd, Omelette, Bread, Butter, Jam, Tea & Coffee",
        "lunch": "Pindi Chana Masala, Aloo Baingan Bharta, Steamed Rice, Phulka, Pineapple Raita, Salad",
        "snacks": "Grilled Veg Sandwich, Tomato Ketchup, Hot Tea & Coffee",
        "dinner": "Matar Paneer, Dal Fry, Jeera Rice, Butter Naan, Chocolate Brownie / Sweet, Salad"
    },
    "Sunday": {
        "breakfast": "Mysore Masala Dosa, Sambar, Coconut & Tomato Chutney, Boiled Eggs, Bread, Butter, Tea",
        "lunch": "Special Hyderabadi Dum Biryani (Chicken & Veg), Mirchi Ka Salan, Onion Raita, Gulab Jamun",
        "snacks": "Crispy French Fries / Spring Rolls, Cookies, Hot Tea & Coffee",
        "dinner": "Sunday Special Feast: Shahi Paneer, Dal Makhani, Peas Pulao, Butter Roti, Ice Cream"
    }
}

IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    """Returns current datetime in Indian Standard Time (IST)."""
    return datetime.now(IST)

def get_current_meal_type():
    """
    Determines meal type according to the 12-hour time of day:
    - 12:00 AM - 11:00 AM: Breakfast
    - 11:00 AM - 03:30 PM: Lunch
    - 03:30 PM - 07:00 PM: High Tea / Snacks
    - 07:00 PM - 11:59 PM: Dinner
    """
    now = get_ist_now().time()
    if now < time(11, 0):
        return "breakfast", "🍳 Breakfast (07:30 AM - 10:30 AM)"
    elif now < time(15, 30):
        return "lunch", "🍱 Lunch (12:00 PM - 02:30 PM)"
    elif now < time(19, 0):
        return "snacks", "☕ High Tea / Snacks (05:00 PM - 06:30 PM)"
    else:
        return "dinner", "🍲 Dinner (07:30 PM - 10:30 PM)"

def get_meal_info(meal_key: str = None, day: str = None) -> dict:
    """Returns the menu for given meal or current meal according to 12-hour IST time."""
    now_ist = get_ist_now()
    current_day = day or now_ist.strftime("%A")

    labels_12h = {
        "breakfast": "🍳 Breakfast (07:30 AM - 10:30 AM)",
        "lunch": "🍱 Lunch (12:00 PM - 02:30 PM)",
        "snacks": "☕ High Tea / Snacks (05:00 PM - 06:30 PM)",
        "dinner": "🍲 Dinner (07:30 PM - 10:30 PM)"
    }

    if not meal_key:
        meal_key, meal_label = get_current_meal_type()
    else:
        meal_label = labels_12h.get(meal_key, meal_key.capitalize())

    day_menu = BENNETT_MESS_MENU.get(current_day, BENNETT_MESS_MENU["Monday"])
    menu_items = day_menu.get(meal_key, "Menu not available.")

    return {
        "day": current_day,
        "time": now_ist.strftime("%I:%M %p"),
        "meal_key": meal_key,
        "meal_label": meal_label,
        "menu": menu_items
    }
