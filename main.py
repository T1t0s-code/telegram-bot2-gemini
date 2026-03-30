import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Set

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.error import RetryAfter

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "").strip()
ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()}
DB_PATH = os.environ.get("DB_PATH", "bot.db")
EXPIRY_MINUTES = 120

# --- DB LOGIC ---
def db_query(query, params=(), commit=False, fetchall=False):
    """Helper to handle DB connections safely"""
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute(query, params)
        if commit:
            con.commit()
        if fetchall:
            return cur.fetchall()
        return cur.fetchone()
    finally:
        con.close()

def db_init():
    db_query("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)", commit=True)
    # New tables for Multi-Game and Ledger
    db_query("""
        CREATE TABLE IF NOT EXISTS posts (
            post_id INTEGER PRIMARY KEY,
            tip_text TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""", commit=True)
    db_query("""
        CREATE TABLE IF NOT EXISTS claims (
            user_id INTEGER,
            post_id INTEGER,
            claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, post_id)
        )""", commit=True)
    db_query("INSERT OR IGNORE INTO meta(key, value) VALUES('current_post_id', '0')", commit=True)

# --- ADMIN HELPERS ---
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def alert_admins(context: ContextTypes.DEFAULT_TYPE, text: str):
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception: pass

# --- CORE LOGIC ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return

    caption = (update.message.caption or "").strip()
    if not caption:
        await update.message.reply_text("❌ Error: You must include the tip as a caption with the photo.")
        return

    # Get and increment sequence
    res = db_query("SELECT value FROM meta WHERE key = 'current_post_id'")
    post_id = int(res[0]) + 1
    db_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),), commit=True)
    
    # Save post to DB
    db_query("INSERT INTO posts (post_id, tip_text) VALUES (?, ?)", (post_id, caption), commit=True)

    photo_id = update.message.photo[-1].file_id
    users = db_query("SELECT user_id FROM whitelist", fetchall=True)
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Send", callback_data=f"GET_{post_id}")]])
    broadcast_text = f"📸 Game #{post_id}\nTap 'Send' below to unlock the tip."

    sent_count = 0
    for (uid,) in users:
        try:
            await context.bot.send_photo(chat_id=uid, photo=photo_id, caption=broadcast_text, reply_markup=keyboard)
            sent_count += 1
            await asyncio.sleep(0.05) # Tiny sleep to avoid flood
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await context.bot.send_photo(chat_id=uid, photo=photo_id, caption=broadcast_text, reply_markup=keyboard)
        except Exception: pass

    await update.message.reply_text(f"✅ Broadcast Post #{post_id} sent to {sent_count} users.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    u = query.from_user
    data = query.data # "GET_101"

    if not data.startswith("GET_"): return
    post_id = int(data.split("_")[1])

    # 1. Check Whitelist
    if not db_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.id,)):
        await query.answer("❌ Not approved.", show_alert=True)
        return

    # 2. Check Post & Expiry
    post = db_query("SELECT tip_text, created_at FROM posts WHERE post_id = ?", (post_id,))
    if not post:
        await query.answer("Post not found.", show_alert=True)
        return

    tip_text, created_at = post
    created_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
    if datetime.utcnow() > created_dt + timedelta(minutes=EXPIRY_MINUTES):
        await query.answer("⚠️ This tip has expired (2hr limit).", show_alert=True)
        return

    # 3. Log Claim
    db_query("INSERT OR IGNORE INTO claims (user_id, post_id) VALUES (?, ?)", (u.id, post_id), commit=True)
    
    # 4. Deliver Tip
    await query.answer()
    await query.message.reply_text(f"🎯 Tip for Game #{post_id}:\n\n{tip_text}")
    
    # 5. Alert Admin (Option A)
    username = f"@{u.username}" if u.username else u.full_name
    await alert_admins(context, f"💰 Tip unlocked!\nUser: {username} ({u.id})\nGame: #{post_id}")

# --- NEW COMMANDS (Option B & Sequence) ---
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    
    # Get claims from last 24 hours
    since = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    rows = db_query("""
        SELECT c.post_id, u.full_name, u.username, c.claimed_at 
        FROM claims c 
        JOIN users u ON c.user_id = u.user_id 
        WHERE c.claimed_at > ? 
        ORDER BY c.post_id DESC""", (since,), fetchall=True)

    if not rows:
        await update.message.reply_text("No claims in the last 24 hours.")
        return

    text = "📊 Recent Claims (Last 24h):\n"
    for pid, name, user, t in rows:
        text += f"- Game #{pid}: {name} (@{user or 'N/A'}) at {t}\n"
    await update.message.reply_text(text)

async def set_post_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    new_id = context.args[0]
    if new_id.isdigit():
        db_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (new_id,), commit=True)
        await update.message.reply_text(f"✅ Sequence ID set to {new_id}")

async def edit_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or len(context.args) < 2:
        await update.message.reply_text("Usage: /edit [ID] [New Text]")
        return
    pid, new_text = context.args[0], " ".join(context.args[1:])
    db_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (new_text, pid), commit=True)
    await update.message.reply_text(f"✅ Tip #{pid} updated.")

# --- BOILERPLATE COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username), commit=True)
    await update.message.reply_text("Welcome. Use /addme to request access.")

async def addme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text("Request sent.")
    await alert_admins(context, f"📥 New Access Request:\n{u.full_name} (@{u.username})\nID: {u.id}\nApprove: `/approve {u.id}`")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    uid = int(context.args[0])
    db_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,), commit=True)
    await update.message.reply_text(f"✅ User {uid} approved.")
    try: await context.bot.send_message(chat_id=uid, text="✅ You are approved! You will receive future tips.")
    except: pass
        
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    help_text = (
        "🛠 **Admin Control Panel**\n\n"
        "📢 **Broadcasting**\n"
        "• Send a **Photo + Caption** to start a new Game.\n"
        "• `/broadcast [text]` - Send a text-only alert to everyone.\n\n"
        "📝 **Management**\n"
        "• `/edit [ID] [New Text]` - Change the tip for a specific Game.\n"
        "• `/setpostid [Number]` - Change the next Game's number.\n"
        "• `/cleartext` - Emergency wipe of the active tip memory.\n\n"
        "👥 **Users & Billing**\n"
        "• `/report` - See who unlocked tips in the last 24h.\n"
        "• `/approve [ID]` - Whitelist a user manually.\n"
        "• `/remove [ID]` - Kick a user from the whitelist.\n"
        "• `/list` - See all whitelisted users.\n\n"
        "🆔 Your Admin ID: `{}`"
    ).format(update.effective_user.id)

    await update.message.reply_text(help_text, parse_mode="Markdown")
    
# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addme", addme))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("setpostid", set_post_id))
    app.add_handler(CommandHandler("edit", edit_tip))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CommandHandler("admin", admin_help))
    
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
