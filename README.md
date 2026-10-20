# Bennett Camulator 🚀

An automated system and multi-user Telegram bot for Bennett University students to instantly generate Day Out Passes, track live Mess Menus, and view real-time Attendance with a smart bunk calculator.

---

## ✨ Features

- ⚡ **1-Tap Instant Day Outpass:** Bypasses manual MyCamu forms and applies with Parent Portal authorization.
- 🚫 **Campus Gate & Overlap Protection:** Checks if you currently have an active leave or are outside campus before submitting.
- 🍱 **Time-Aware Mess Menu:** Automatically serves Breakfast, Lunch, High Tea, or Dinner based on current Indian Standard Time (IST).
- 📊 **Live Attendance & Bunk Calculator:** Shows overall attendance, monthly score, and tells you how many classes you can bunk or need to attend to stay safe at 75%.
- 🤖 **Multi-User Telegram Bot:** Students can link their own parent accounts using a private 2-step flow (email first, password second with automatic message deletion).
- 📱 **iOS & Android Shortcuts:** 1-Tap trigger via Apple Action Button, Siri, or Android HTTP Shortcuts widget.

---

## 🛠️ Tech Stack

- **FastAPI** — Lightweight high-performance asynchronous API
- **python-telegram-bot** — Interactive multi-user Telegram client
- **SQLite** — Lightweight local user database
- **Docker** — Ready for 1-click cloud deployment (Koyeb, Render, Railway)

---

## 🚀 Deployment (Render)

1. Deploy this repository to **Render**.
2. Set Environment Variables:
   - `TELEGRAM_BOT_TOKEN`: Your token from `@BotFather`
   - `API_SECRET_KEY`: `bennett-camu-key` (or your custom secret)
3. Set up a free 5-minute keep-alive ping on **UptimeRobot** pointing to:
   `https://<your-app-domain>/health`
