import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
# This part is now extra-safe to catch spaces or weird formatting
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "").replace(" ", "")
ADMIN_IDS = {int(x) for x in ADMIN_IDS_RAW.split(",") if x.isdigit()}
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
EXPIRY_MINUTES = 120

# --- DB SETUP ---
def db_query(query, params=(), commit=False, fetchall=False):
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute(query, params)
        if commit: con.commit()
        return cur.fetchall() if fetchall else cur.fetchone()
    finally:
        con.close()

def db_init():
    db_query("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, post_id))", commit=True)
    db_query("INSERT OR IGNORE INTO meta(key, value) VALUES('current_post_id', '0')", commit=True)

# --- ADMIN CHECK ---
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username), commit=True)
    
    # DEBUG LINE: This tells you if the bot thinks you are an admin
    admin_status = "✅ ADMIN VERIFIED" if is_admin(u.id) else "❌ USER ACCESS ONLY"
    await update.message.reply_text(f"RICTA TERMINAL\n{admin_status}\n\nYour ID: `{u.id}`", parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return # Bot ignores photos from non-admins

    caption = (update.message.caption or "No Description").strip()
    photo_id = update.message.photo[-1].file_id

    res = db_query("SELECT value FROM meta WHERE key = 'current_post_id'")
    post_id = int(res[0]) + 1
    db_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),), commit=True)
    db_query("INSERT INTO posts (post_id, tip_text, photo_id) VALUES (?, ?, ?)", (post_id, caption, photo_id), commit=True)

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Unlock Selection", callback_data=f"GET_{post_id}")]])
    channel_text = f"**Post #{post_id}**\n**Status:** Active"

    if CHANNEL_ID:
        try:
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=channel_text, reply_markup=keyboard, parse_mode="Markdown")
            await update.message.reply_text(f"✅ Data #{post_id} broadcasted.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Usage: `/approve [ID]`")
        return
    uid = int(context.args[0])
    db_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,), commit=True)
    await update.message.reply_text(f"✅ ID {uid} Authorized.")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    since = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    rows = db_query("""
        SELECT c.post_id, u.full_name, c.claimed_at 
        FROM claims c JOIN users u ON c.user_id = u.user_id 
        WHERE c.claimed_at > ? ORDER BY c.claimed_at DESC""", (since,), fetchall=True)
    if not rows:
        await update.message.reply_text("No activity in 24h.")
        return
    res = "\n".join([f"#{p} | {n} | {t[11:16]}" for p, n, t in rows])
    await update.message.reply_text(f"**Activity Report (24h):**\n\n{res}", parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    if not query.data.startswith("GET_"): return
    
    post_id = int(query.data.split("_")[1])
    if not db_query("SELECT 1 FROM whitelist WHERE user_id = ?", (user.id,)):
        await query.answer("Access Denied.", show_alert=True)
        return

    post = db_query("SELECT tip_text, photo_id, created_at FROM posts WHERE post_id = ?", (post_id,))
    if not post: return

    tip_text, photo_id, created_at = post
    created_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
    if datetime.utcnow() > created_dt + timedelta(minutes=EXPIRY_MINUTES):
        await query.answer("Selection expired.", show_alert=True)
        return

    db_query("INSERT OR IGNORE INTO claims (user_id, post_id) VALUES (?, ?)", (user.id, post_id), commit=True)
    await query.answer()
    await context.bot.send_photo(
        chat_id=user.id, photo=photo_id,
        caption=f"**Data Sheet #{post_id}**\n\n**Selection:** {tip_text}\n\nSettlement via @R1cta.",
        parse_mode="Markdown"
    )

def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
