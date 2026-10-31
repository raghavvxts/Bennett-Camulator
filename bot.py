import os
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from weakref import WeakValueDictionary
from datetime import datetime, time, timedelta
from day_pass import IST, day_pass_window
from telegram.error import TelegramError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
    TypeHandler,
    ApplicationHandlerStop,
    BaseUpdateProcessor
)
from dotenv import load_dotenv
import database
from mycamu_api import MyCamuClient
import mess_menu

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORTAL_WORKERS = 16
portal_executor = ThreadPoolExecutor(max_workers=PORTAL_WORKERS, thread_name_prefix="mycamu")


async def portal_call(function, *args, **kwargs):
    return await asyncio.get_running_loop().run_in_executor(portal_executor, partial(function, *args, **kwargs))

class ChatUpdateProcessor(BaseUpdateProcessor):
    """Keep each chat's conversation ordered while other users can proceed."""
    def __init__(self):
        super().__init__(PORTAL_WORKERS)
        self.locks = WeakValueDictionary()

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_process_update(self, update, coroutine):
        chat_id = update.effective_chat.id if update.effective_chat else None
        lock = self.locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            await coroutine

# States for login conversation
AWAITING_EMAIL, AWAITING_PASSWORD, PASS_TOMORROW, PASS_TIME, PASS_REASON = range(5)

def get_main_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🚀 Instant Day Out Pass", callback_data="btn_create_pass")],
        [
            InlineKeyboardButton("🍱 Mess Menu (Live)", callback_data="btn_mess_menu"),
            InlineKeyboardButton("📊 Attendance", callback_data="btn_attendance")
        ],
        [
            InlineKeyboardButton("🔍 Gate Status", callback_data="btn_check_status"),
            InlineKeyboardButton("📋 Recent Passes", callback_data="btn_recent_passes")
        ],
        [InlineKeyboardButton("🚪 Logout / Switch Account", callback_data="btn_logout")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_meal_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🍳 Breakfast", callback_data="meal_breakfast"),
            InlineKeyboardButton("🍱 Lunch", callback_data="meal_lunch")
        ],
        [
            InlineKeyboardButton("☕ High Tea", callback_data="meal_snacks"),
            InlineKeyboardButton("🍲 Dinner", callback_data="meal_dinner")
        ],
        [InlineKeyboardButton("📋 Today’s full menu", callback_data="meal_all")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="btn_back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = database.get_user(user_id)

    if not user_data:
        welcome_text = (
            "👋 *Welcome to Bennett Camulator Bot!*\n\n"
            "Request *Day Out Passes*, check today’s *Mess Menu* from MyCamu (IST), and track your *Attendance*.\n\n"
            "🔒 *First-Time Setup:*\n"
            "Tap below to link your Bennett Parent Account."
        )
        login_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 Link Parent Account", callback_data="btn_start_login")]
        ])
        await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=login_btn)
        return

    student_name = user_data.get("student_name", "Student")
    msg = (
        f"👋 *Welcome back, {student_name}!* 🎓\n\n"
        "Choose an option below or tap the `/` button:"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restarts conversation state and returns to main menu."""
    context.user_data.clear()
    await start_command(update, context)

async def features_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    features_text = (
        "✨ *Bennett Camulator Features*\n\n"
        "🚀 *Day Out Pass*: Creates a gate pass and verifies parent approval.\n"
        "🔍 *Gate & Overlap Status*: Real-time security gate verification.\n"
        "🍱 *Live Mess Menu*: Today’s dishes and serving times from your MyCamu Cafeteria.\n"
        "📊 *Live Attendance & Bunk Calculator*: Enrolled courses, bunks remaining, and targets.\n"
        "📋 *Recent Passes*: View past leave history and approval status.\n"
        "🚪 *Multi-User Support*: Switch or log into any student account safely.\n\n"
        "Type `/start` or `/restart` anytime!"
    )
    await update.message.reply_text(features_text, parse_mode="Markdown")

def linked_client(user):
    return MyCamuClient(email=user["email"], password=user["password"], cookies=user["cookies"],
                        stu_id=user["stu_id"], in_id=user["in_id"], student_name=user["student_name"])


def gate_time_label(value):
    # Gate timestamps contain IST wall time despite their literal Z suffix.
    return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d %b %Y, %I:%M %p IST")


def save_pass_draft(update, context, state):
    data = {key: value for key, value in context.user_data.items() if key.startswith("pass_")}
    data.update(state=state, created_day=datetime.now(IST).date().isoformat())
    database.save_draft(update.effective_user.id, data)


async def reason_prompt(update, context):
    reason = context.user_data.get("pass_reason", "Going out")
    selected = context.user_data.get("pass_departure")
    when = datetime.fromisoformat(selected).strftime("%d %b, %I:%M %p IST") if selected else "Today, next available minute"
    await update.effective_message.reply_text(
        f"Departure: {when}\nReason: {reason}\nReturn: 7 PM IST on the selected day. No documents needed.\n"
        "Departure will be the next minute after portal checks unless you selected a time.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Create pass", callback_data="pass_submit"),
             InlineKeyboardButton("Change reason", callback_data="pass_change_reason")],
            [InlineKeyboardButton("Cancel", callback_data="pass_cancel")]]))
    save_pass_draft(update, context, PASS_REASON)
    return PASS_REASON


async def tomorrow_prompt(update, context):
    await update.effective_message.reply_text("Today's day-pass window has ended. Apply for tomorrow?", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes, tomorrow", callback_data="pass_tomorrow_yes"),
         InlineKeyboardButton("Cancel", callback_data="pass_tomorrow_no")]]))
    save_pass_draft(update, context, PASS_TOMORROW)
    return PASS_TOMORROW


async def pass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    if not database.get_user(update.effective_user.id):
        await update.effective_message.reply_text("Link your parent account first using /start.")
        return ConversationHandler.END
    context.user_data.pop("pass_departure", None)
    context.user_data.pop("pass_date", None)
    context.user_data["pass_reason"] = "Going out"
    now = datetime.now(IST)
    draft = database.get_draft(update.effective_user.id)
    if draft and draft.get("created_day") == now.date().isoformat():
        context.user_data.update({k: v for k, v in draft.items() if k.startswith("pass_")})
        if draft["state"] == PASS_TOMORROW:
            return await tomorrow_prompt(update, context)
        if draft["state"] == PASS_TIME:
            await update.effective_message.reply_text(f"Resuming your request for {draft.get('pass_date')}. Enter a future time from 06:00 to before 19:00 IST (HH:MM).")
            return PASS_TIME
        return await reason_prompt(update, context)
    database.delete_draft(update.effective_user.id)
    if day_pass_window(now=now).get("needs_next_day"):
        return await tomorrow_prompt(update, context)
    if now.time() < time(6):
        context.user_data["pass_date"] = now.date().isoformat()
        await update.effective_message.reply_text("What time today? Enter HH:MM in 24-hour IST, from 06:00 to before 19:00. /cancel to stop.")
        save_pass_draft(update, context, PASS_TIME)
        return PASS_TIME
    return await reason_prompt(update, context)


async def pass_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pass_tomorrow_no":
        database.delete_draft(update.effective_user.id)
        await query.edit_message_text("Day-pass request cancelled.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    context.user_data["pass_reason"] = "Going out"
    context.user_data["pass_date"] = (datetime.now(IST).date() + timedelta(days=1)).isoformat()
    context.user_data.pop("pass_departure", None)
    await query.edit_message_text("What time tomorrow? Enter HH:MM in 24-hour IST, from 06:00 to before 19:00. /cancel to stop.")
    save_pass_draft(update, context, PASS_TIME)
    return PASS_TIME


async def pass_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = datetime.strptime(update.message.text.strip(), "%H:%M").time()
        departure = datetime.combine(datetime.fromisoformat(context.user_data["pass_date"]).date(), value, IST)
        window = day_pass_window(departure)
        if not window["success"]:
            raise ValueError(window["message"])
    except (ValueError, KeyError):
        await update.message.reply_text("Enter a future time between 06:00 and before 19:00, using HH:MM (IST). /cancel to stop.")
        return PASS_TIME
    context.user_data["pass_departure"] = departure.isoformat()
    return await reason_prompt(update, context)


async def pass_action(update, context):
    query = update.callback_query
    await query.answer()
    draft = database.get_draft(update.effective_user.id)
    if not draft:
        await query.message.reply_text("This request has ended. Start again with /pass.")
        return ConversationHandler.END
    context.user_data.update({k: v for k, v in draft.items() if k.startswith("pass_")})
    if query.data == "pass_cancel":
        return await cancel_pass(update, context)
    if query.data == "pass_change_reason":
        await query.message.reply_text("Send the outing reason, or /cancel to stop.")
        return PASS_REASON
    return await submit_pass(update, context)


async def pass_reason(update, context):
    reason = update.message.text.strip()
    if not reason:
        await update.message.reply_text("An outing reason is required.")
        return PASS_REASON
    context.user_data["pass_reason"] = reason
    return await reason_prompt(update, context)


async def submit_pass(update, context):
    user = database.get_user(update.effective_user.id)
    if not user:
        await update.effective_message.reply_text("Link your account again with /start.")
        return ConversationHandler.END
    departure = context.user_data.get("pass_departure")
    departure = datetime.fromisoformat(departure) if departure else None
    window = day_pass_window(departure)
    if window.get("needs_next_day"):
        return await tomorrow_prompt(update, context)
    if not window["success"]:
        await update.effective_message.reply_text(window["message"] + " Start again with /pass.")
        database.delete_draft(update.effective_user.id)
        return ConversationHandler.END
    status = await update.effective_message.reply_text("1/3 Checking your leave and gate status…")
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    async def show_progress():
        while True:
            text = await queue.get()
            if text is None:
                return
            try:
                await status.edit_text(text)
            except TelegramError:
                logger.warning("Could not update Telegram progress message")
    display = asyncio.create_task(show_progress())
    try:
        result = await portal_call(linked_client(user).create_day_pass,
            reason=context.user_data.get("pass_reason", "Going out"), departure_at=departure,
            progress=lambda text: loop.call_soon_threadsafe(queue.put_nowait, text))
    finally:
        await queue.put(None)
        await display
    if result.get("needs_next_day"):
        return await tomorrow_prompt(update, context)
    if result.get("success"):
        text = (f"✅ Day Out Pass approved for {user['student_name']}.\n"
                f"From: {gate_time_label(result['departure'])}\nTo: {gate_time_label(result['return'])}\n"
                f"Status: {result['status']}\n"
                + ("Existing gate pass reused." if result.get("reused_gate_pass") else
                   "Existing leave reused." if result.get("reused_leave") else "Leave and gate pass created."))
    else:
        text = result.get("message", "Could not verify approval. Check MyCamu before retrying.")
    await status.edit_text(text, reply_markup=get_main_keyboard())
    database.delete_draft(update.effective_user.id)
    for key in list(context.user_data):
        if key.startswith("pass_"):
            context.user_data.pop(key)
    return ConversationHandler.END


async def cancel_pass(update, context):
    database.delete_draft(update.effective_user.id)
    for key in list(context.user_data):
        if key.startswith("pass_"):
            context.user_data.pop(key)
    await update.effective_message.reply_text("Day-pass request cancelled.", reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def reset_pass(update, context):
    context.user_data.clear()
    database.delete_draft(update.effective_user.id)
    await start_command(update, context)
    return ConversationHandler.END


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = database.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Link your parent account first using /start to fetch your mess menu.")
        return
    status = await update.message.reply_text("Fetching today's menu from MyCamu Cafeteria…")
    meal = await portal_call(mess_menu.get_meal_info, linked_client(user))
    await show_result(status, update.message, mess_menu.menu_text(meal), get_meal_keyboard())


async def shortcut_command(update, context):
    try:
        token = database.issue_shortcut_token(update.effective_user.id)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        "Your personal shortcut key (only for your linked account):\n\n"
        f"X-API-Key: {token}\n\n"
        "Use this header with POST /quick-pass on the bot’s API server. "
        'Send {} to use “Going out”, or {"reason":"your reason"}. '
        "Running /shortcut again replaces this key. Logout or switching accounts revokes it.")

def attendance_text(att, name):
    overall = float(att["overall_pct"])
    blocks = max(0, min(10, int(overall // 10)))
    text = (f"📊 Attendance Report — {name}\n\n"
            f"Overall: {overall:g}% {'🟩' * blocks}{'⬜' * (10-blocks)}\n"
            f"Classes attended: {att['overall_present']} / {att['overall_total']}\n"
            f"This month: {att['month_pct']}%\n\nSubject breakdown:\n")
    for subject in att.get("subjects", []):
        icon = "🟢" if subject["percentage"] >= 75 else "🔴"
        text += (f"\n{icon} {subject['code']} — {subject['name']}\n"
                 f"{subject['percentage']}% ({subject['present']}/{subject['total']} classes)\n{subject['advice']}\n")
    return text


async def show_result(status, message, text, keyboard):
    chunks = [text[i:i+3800] for i in range(0, len(text), 3800)] or ["No data returned."]
    await status.edit_text(chunks[0], reply_markup=keyboard if len(chunks)==1 else None)
    for index, chunk in enumerate(chunks[1:], 1):
        await message.reply_text(chunk, reply_markup=keyboard if index==len(chunks)-1 else None)


async def attendance_command(update, context):
    user = database.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Link your parent account first using /start.")
        return
    status = await update.message.reply_text("Fetching live attendance…")
    att = await portal_call(linked_client(user).get_attendance)
    text = attendance_text(att, user["student_name"]) if att.get("success") else f"Could not fetch attendance: {att.get('message')}"
    await show_result(status, update.message, text, get_main_keyboard())

# --- Step-by-Step Login Handlers ---
async def start_login_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Open a private chat with this bot to link your account.")
        return ConversationHandler.END
    context.user_data.clear()
    database.delete_draft(update.effective_user.id)
    query = update.callback_query
    if query:
        await query.answer()

    msg = (
        "📧 *Step 1 of 2: Parent Email*\n\n"
        "Please type and send your Bennett Parent Portal email address:\n"
        "_(e.g. `parent@gmail.com`)_"
    )
    if query:
        await query.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")

    return AWAITING_EMAIL

async def receive_email_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return ConversationHandler.END
    email = update.message.text.strip()
    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ Please enter a valid email address:")
        return AWAITING_EMAIL

    context.user_data["login_email"] = email
    msg = (
        f"✅ Email saved: `{email}`\n\n"
        "🔑 *Step 2 of 2: Password*\n\n"
        "Now send your Password.\n\n"
        "Your password and portal session are stored encrypted on this server for future requests. "
        "Use Logout / Switch Account to remove the saved account.\n\n"
        "🔒 _(Your message will be deleted immediately from this chat for security)_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    return AWAITING_PASSWORD

async def receive_password_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return ConversationHandler.END
    password = update.message.text
    chat_id = update.effective_chat.id
    message_id = update.message.message_id

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Could not delete password message: {e}")

    email = context.user_data.get("login_email")
    if not email:
        await update.message.reply_text("❌ Session expired. Please send `/login` again.")
        return ConversationHandler.END

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ *Authenticating with Bennett Parent Portal...*",
        parse_mode="Markdown"
    )

    success, err_msg, details = await portal_call(MyCamuClient.test_and_fetch_profile, email, password)

    if not success:
        await status_msg.edit_text(
            f"❌ *Authentication Failed:*\n{err_msg}\n\nType `/login` to try again.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    if details.get("account_type") != "parent":
        await status_msg.edit_text("Link a parent account to request and approve passes.")
        return ConversationHandler.END
    user_id = update.effective_user.id
    database.save_user(
        telegram_id=user_id,
        email=email,
        password=password,
        student_name=details["student_name"],
        stu_id=details["stu_id"],
        in_id=details["in_id"],
        cookies=details["cookies"]
    )

    success_msg = (
        f"🎉 *Account Linked Successfully!*\n\n"
        f"👤 *Student:* {details['student_name']}\n"
        f"🆔 *Student ID:* `{details['stu_id']}`\n\n"
        "Tap a button below to get started:"
    )
    await status_msg.edit_text(success_msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
    return ConversationHandler.END

async def cancel_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Login cancelled. Type `/start` anytime.", parse_mode="Markdown")
    return ConversationHandler.END

# --- Button Handlers ---
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    user_data = database.get_user(user_id)

    if not user_data:
        await query.message.reply_text("⚠️ Please link your account first.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 Link Parent Account", callback_data="btn_start_login")]
        ]))
        return

    client = MyCamuClient(
        email=user_data["email"],
        password=user_data["password"],
        cookies=user_data["cookies"],
        stu_id=user_data["stu_id"],
        in_id=user_data["in_id"],
        student_name=user_data["student_name"]
    )

    action = query.data

    if action == "btn_back_main":
        await query.edit_message_text(
            f"👋 *Bennett Camulator - {user_data['student_name']}*\n\nSelect an option below:",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )

    elif action == "btn_mess_menu" or action.startswith("meal_"):
        meal_key = action.removeprefix("meal_") if action.startswith("meal_") else None
        await query.edit_message_text("Fetching today's menu from MyCamu Cafeteria…")
        meal = await portal_call(mess_menu.get_meal_info, client, meal_key)
        await show_result(query.message, query.message, mess_menu.menu_text(meal), get_meal_keyboard())

    elif action == "btn_attendance":
        await query.edit_message_text("Fetching live attendance…")
        att = await portal_call(client.get_attendance)
        text = attendance_text(att, user_data["student_name"]) if att.get("success") else f"Could not fetch attendance: {att.get('message')}"
        await show_result(query.message, query.message, text, get_main_keyboard())

    elif action == "btn_check_status":
        await query.edit_message_text("🔍 *Checking your campus gate and pass status...*", parse_mode="Markdown")
        active = await portal_call(client.get_active_pass)

        if not active.get("success"):
            await query.message.reply_text(active["message"], reply_markup=get_main_keyboard())
            return

        if active.get("has_active_pass"):
            text = (
                f"⚠️ *Active Pass In Progress*\n\n"
                f"📋 *Type:* {active.get('type')}\n"
                f"🎯 *Reason:* {active.get('reason')}\n"
                f"📊 *Status:* `{active.get('status')}`\n"
                f"⏳ *Period:* {active.get('start', '')[:10]} to {active.get('end', '')[:10]}\n\n"
            )
            text += "A new gate pass can be created only after this one is marked Completed, whether you are inside or outside campus."
        else:
            text = "✅ *All Clear!*\n\nYou are in campus with no active passes blocking you. You can generate an instant Day Out Pass whenever you are ready."

        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif action == "btn_recent_passes":
        await query.edit_message_text("📋 *Fetching your pass history...*", parse_mode="Markdown")
        try:
            leaves = await portal_call(client.get_leaves)
        except Exception:
            await query.message.reply_text("Could not fetch leave and gate-pass history. Try again later.", reply_markup=get_main_keyboard())
            return

        if not leaves:
            text = "ℹ️ No recent leaves or passes found."
        else:
            text = f"📋 *Recent Leaves & Outpasses ({min(5, len(leaves))} most recent):*\n\n"
            for idx, l in enumerate(leaves[:5], 1):
                start_dt = (l.get("frdt", "") if l.get("isGatepass") else l.get("start", ""))[:10]
                record_status = client.record_status(l)
                status_icon = "🟢" if record_status == "Approved" else ("🟡" if record_status == "Submitted" else "⚪")
                text += f"{idx}. {status_icon} *{client.record_type(l)}* ({l.get('rson') if l.get('isGatepass') else l.get('Rson', 'N/A')})\n   📅 Date: `{start_dt}` | Status: `{record_status}`\n\n"

        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif action == "btn_logout":
        database.delete_user(user_id)
        await query.edit_message_text(
            "🚪 *Logged out successfully!*\nYour credentials have been removed. Type `/start` to link again anytime.",
            parse_mode="Markdown"
        )

async def bot_heartbeat():
    while True:
        await asyncio.to_thread(database.heartbeat, "telegram")
        await asyncio.sleep(15)


async def post_shutdown(application):
    task = application.bot_data.get("heartbeat_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def post_init(application):
    """Configures the Telegram 3-slash (/) menu button with quick commands."""
    commands = [
        BotCommand("start", "Start the bot and open main menu"),
        BotCommand("restart", "Restart the bot session and reset state"),
        BotCommand("pass", "Request an instant Day Out Pass"),
        BotCommand("menu", "Fetch today’s MyCamu mess menu"),
        BotCommand("attendance", "View Attendance & bunk advice"),
        BotCommand("features", "View full list of bot features"),
        BotCommand("shortcut", "Generate your personal API shortcut key")
    ]
    await application.bot.set_my_commands(commands)
    application.bot_data["heartbeat_task"] = asyncio.create_task(bot_heartbeat())
    logger.info("Bot commands menu registered successfully.")

async def cancel_workflow(update, context):
    context.user_data.clear()
    database.delete_draft(update.effective_user.id)
    await update.effective_message.reply_text("Request cancelled. Use /start to return to the menu.")
    return ConversationHandler.END


async def logout_workflow(update, context):
    await handle_button(update, context)
    context.user_data.clear()
    return ConversationHandler.END


async def private_chat_only(update, context):
    if update.effective_chat and update.effective_chat.type != "private":
        if update.callback_query:
            await update.callback_query.answer("Use this bot in a private chat.", show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text("Use this bot in a private chat to access your account.")
        raise ApplicationHandlerStop


async def handle_error(update, context):
    # Updates may contain passwords; never log the update or raw exception text.
    logger.error("Bot request failed (%s)", type(context.error).__name__)
    if update and update.effective_chat and update.effective_chat.type == "private" and update.effective_message:
        try:
            await update.effective_message.reply_text("Could not finish this request. Check MyCamu before retrying a pass request.")
        except TelegramError:
            logger.warning("Could not send the error notification")


def main():
    if not BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not set in .env")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(ChatUpdateProcessor()).post_init(post_init).post_shutdown(post_shutdown).build()

    app.add_handler(TypeHandler(Update, private_chat_only), group=-1)
    app.add_error_handler(handle_error)

    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("login", start_login_step),
            CallbackQueryHandler(start_login_step, pattern="^btn_start_login$"),
            CommandHandler("pass", pass_command),
            CallbackQueryHandler(pass_command, pattern="^btn_create_pass$"),
            CallbackQueryHandler(pass_action, pattern="^pass_(submit|change_reason|cancel)$"),
            CallbackQueryHandler(pass_tomorrow, pattern="^pass_tomorrow_(yes|no)$"),
        ],
        states={
            AWAITING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email_step)],
            AWAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password_step)],
            PASS_TOMORROW: [CallbackQueryHandler(pass_tomorrow, pattern="^pass_tomorrow_(yes|no)$")],
            PASS_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, pass_time)],
            PASS_REASON: [CallbackQueryHandler(pass_action, pattern="^pass_(submit|change_reason|cancel)$"),
                          MessageHandler(filters.TEXT & ~filters.COMMAND, pass_reason)],
        },
        fallbacks=[CommandHandler("cancel", cancel_workflow), CommandHandler("start", reset_pass),
                   CommandHandler("restart", reset_pass), CallbackQueryHandler(logout_workflow, pattern="^btn_logout$")],
        allow_reentry=True,
    ))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("features", features_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("attendance", attendance_command))
    app.add_handler(CommandHandler("shortcut", shortcut_command))
    app.add_handler(CallbackQueryHandler(handle_button))

    print("🤖 Multi-User Telegram Bot is running!")
    app.run_polling()

if __name__ == "__main__":
    main()
