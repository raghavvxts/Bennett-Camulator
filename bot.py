import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler
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

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# States for login conversation
AWAITING_EMAIL, AWAITING_PASSWORD = range(2)

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
            InlineKeyboardButton("🍛 Lunch", callback_data="meal_lunch")
        ],
        [
            InlineKeyboardButton("☕ High Tea", callback_data="meal_snacks"),
            InlineKeyboardButton("🍲 Dinner", callback_data="meal_dinner")
        ],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="btn_back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = database.get_user(user_id)

    if not user_data:
        welcome_text = (
            "👋 *Welcome to Bennett Camulator Bot!*\n\n"
            "Generate *instant Day Out Passes*, check live *Mess Menu*, and track your *Attendance* with ease.\n\n"
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
        f"Parent: `{user_data.get('email')}`\n\n"
        "Choose an option below:"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

# --- Step-by-Step Login Handlers ---
async def start_login_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        chat_id = query.message.chat_id
    else:
        chat_id = update.effective_chat.id

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
    email = update.message.text.strip()
    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ Please enter a valid email address:")
        return AWAITING_EMAIL

    context.user_data["login_email"] = email
    msg = (
        f"✅ Email saved: `{email}`\n\n"
        "🔑 *Step 2 of 2: Password*\n\n"
        "Now send your Password.\n\n"
        "🔒 *(Your message will be deleted immediately from this chat for security)*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    return AWAITING_PASSWORD

async def receive_password_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    chat_id = update.effective_chat.id
    msg_id = update.message.message_id

    # Immediately delete user message containing the password!
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

    email = context.user_data.get("login_email")
    status_msg = await update.message.reply_text("🔄 *Authenticating with Bennett ERP (student.bennetterp.camu.in)...*", parse_mode="Markdown")

    ok, err, details = MyCamuClient.test_and_fetch_profile(email, password)
    if not ok:
        await status_msg.edit_text(
            f"❌ *Authentication Failed:*\n{err}\n\nType `/login` to try again.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # Save to SQLite database
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
        f"🆔 *Student ID:* `{details['stu_id']}`\n"
        f"📧 *Parent:* `{email}`\n\n"
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

    elif action == "btn_create_pass":
        await query.edit_message_text("⏳ *Requesting Day Out Pass from Bennett MyCamu...*", parse_mode="Markdown")
        res = client.create_day_pass(reason="Day Out")

        if res.get("success"):
            text = (
                f"✅ *Day Out Pass Created!*\n\n"
                f"👤 *Student:* {user_data['student_name']}\n"
                f"📅 *Date:* {res.get('date')}\n"
                f"⏰ *Generated:* {res.get('created_time')}\n"
                f"⚡ *Approval:* Auto-Approved (Parent Session)\n\n"
                f"Show your MyCamu QR / ID card at the security gate to exit."
            )
        else:
            text = f"❌ *Pass Request Blocked:*\n\n{res.get('message')}"

        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif action == "btn_mess_menu" or action.startswith("meal_"):
        meal_key = None
        if action.startswith("meal_"):
            meal_key = action.replace("meal_", "")

        meal = mess_menu.get_meal_info(meal_key)
        text = (
            f"🍱 *Bennett Mess Menu — {meal['day']}*\n"
            f"🕒 Current Time: `{meal['time']}`\n\n"
            f"🍴 *{meal['meal_label']}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{meal['menu']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            "_Tap below to see other meals for today:_"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_meal_keyboard())

    elif action == "btn_attendance":
        await query.edit_message_text("📊 *Fetching live attendance data from Bennett ERP...*", parse_mode="Markdown")
        att = client.get_attendance()

        if not att.get("success"):
            await query.message.reply_text(f"❌ Failed to fetch attendance: {att.get('message')}", reply_markup=get_main_keyboard())
            return

        overall = att["overall_pct"]
        bar = "🟩" * (overall // 10) + "⬜" * (10 - (overall // 10))

        text = (
            f"📊 *Attendance Report — {user_data['student_name']}*\n\n"
            f"📈 *Overall:* {overall}% {bar}\n"
            f"🎯 *Classes Attended:* {att['overall_present']} / {att['overall_total']}\n"
            f"🗓️ *This Month:* {att['month_pct']}%\n\n"
            f"📚 *Subject Breakdown:*\n"
        )

        for s in att.get("subjects", []):
            pct = s["percentage"]
            icon = "🟢" if pct >= 75 else "🔴"
            text += (
                f"\n{icon} *{s['code']}* - {s['name']}\n"
                f"   • Score: *{pct}%* ({s['present']}/{s['total']} classes)\n"
                f"   • {s['advice']}\n"
            )

        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif action == "btn_check_status":
        await query.edit_message_text("🔍 *Checking your campus gate and pass status...*", parse_mode="Markdown")
        active = client.get_active_pass()

        if active.get("has_active_pass"):
            text = (
                f"⚠️ *Active Pass In Progress*\n\n"
                f"📋 *Type:* {active.get('type')}\n"
                f"🎯 *Reason:* {active.get('reason')}\n"
                f"📊 *Status:* `{active.get('status')}`\n"
                f"⏳ *Period:* {active.get('start', '')[:10]} to {active.get('end', '')[:10]}\n\n"
                f"*(You must check back in at the campus gate before applying for a new pass)*"
            )
        else:
            text = "✅ *All Clear!*\n\nYou have no active leaves blocking you. You can generate an instant Day Out Pass whenever you are ready."

        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif action == "btn_recent_passes":
        await query.edit_message_text("📋 *Fetching your pass history...*", parse_mode="Markdown")
        leaves = client.get_leaves()

        if not leaves:
            text = "ℹ️ No recent leaves or passes found."
        else:
            text = f"📋 *Recent Leaves & Outpasses ({min(5, len(leaves))} most recent):*\n\n"
            for idx, l in enumerate(leaves[:5], 1):
                start_dt = l.get("start", "")[:10]
                status_icon = "🟢" if l.get("Stus") == "Approved" else ("🟡" if l.get("Stus") == "Submitted" else "⚪")
                text += f"{idx}. {status_icon} *{l.get('LvTy')}* ({l.get('Rson', 'N/A')})\n   📅 Date: `{start_dt}` | Status: `{l.get('Stus')}`\n\n"

        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif action == "btn_logout":
        database.delete_user(user_id)
        await query.edit_message_text(
            "🚪 *Logged out successfully!*\nYour credentials have been removed. Type `/start` to link again anytime.",
            parse_mode="Markdown"
        )

def main():
    if not BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not set in .env")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Conversation handler for step-by-step login (Email first, then Password)
    login_conv = ConversationHandler(
        entry_points=[
            CommandHandler("login", start_login_step),
            CallbackQueryHandler(start_login_step, pattern="^btn_start_login$")
        ],
        states={
            AWAITING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email_step)],
            AWAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password_step)]
        },
        fallbacks=[CommandHandler("cancel", cancel_login)]
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(login_conv)
    app.add_handler(CallbackQueryHandler(handle_button))

    print("🤖 Multi-User Telegram Bot is running!")
    app.run_polling()

if __name__ == "__main__":
    main()
