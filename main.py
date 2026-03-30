import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Optional

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
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db") # Points to Railway Volume
EXPIRY_MINUTES = 120

# --- DB HELPERS ---
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
    db_query("""CREATE TABLE IF NOT EXISTS posts (
                post_id INTEGER PRIMARY KEY, 
                tip_text TEXT, 
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""", commit=True)
    db_query("""CREATE TABLE IF NOT EXISTS claims (
                user_id INTEGER, post_id INTEGER, 
                claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
                PRIMARY KEY (user_id, post_id))""", commit=True)
    db_query("INSERT OR IGNORE INTO meta(key, value) VALUES('current_post_id', '0')", commit=True)

# --- SECURITY ---
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def alert_admins(context: ContextTypes.DEFAULT_TYPE, text: str):
    for admin_id in ADMIN_IDS:
        try: await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
        except: pass

# --- ADMIN COMMANDS ---
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    help_text = (
        "🛠 **Admin Cheat Sheet**\n\n"
        "📸 **New Game:** Send a Photo with the tip as the **Caption**.\n\n"
        "📝 **Management**\n"
        "• `/edit [ID] [New Text]` - Fix a typo in a tip.\n"
        "• `/delete [ID]` - Completely remove a game and its tip.\n"
        "• `/setpostid [Num]` - Change the sequence number (e.g., jump to 100).\n\n"
        "📊 **Billing & Users**\n"
        "• `/report` - Who opened tips in the last 24h.\n"
        "• `/list` - Show all approved partners.\n"
        "• `/approve [ID]` - Add someone manually.\n"
        "• `/remove [ID]` - Kick someone out."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def delete_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args:
        await update.message.reply_text("Usage: `/delete [Post ID]`")
        return
    pid = context.args[0]
    db_query("DELETE FROM posts WHERE post_id = ?", (pid,), commit=True)
    await update.message.reply_text(f"🗑 Game #{pid} and its tip have been deleted.")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    rows = db_query("""
        SELECT w.user_id, u.full_name, u.username 
        FROM whitelist w 
        LEFT JOIN users u ON w.user_id = u.user_id
    """, fetchall=True)
    if not rows:
        await update.message.reply_text("Whitelist is empty.")
        return
    text = "📋 **Active Partners:**\n"
    for uid, name, user in rows:
        text += f"• `{uid}` - {name or 'No Name'} (@{user or 'N/A'})\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# --- BROADCAST LOGIC ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    caption = (update.message.caption or "").strip()
    if not caption:
        await update.message.reply_text("❌ Error: You must write the tip in the photo caption!")
        return

    # Increment Post ID
    res = db_query("SELECT value FROM meta WHERE key = 'current_post_id'")
    post_id = int(res[0]) + 1
    db_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),), commit=True)
    db_query("INSERT INTO posts (post_id, tip_text) VALUES (?, ?)", (post_id, caption), commit=True)

    photo_id = update.message.photo[-1].file_id
    users = db_query("SELECT user_id FROM whitelist", fetchall=True)
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Send Tip", callback_data=f"GET_{post_id}")]])
    broadcast_msg = f"📸 **Game #{post_id}**\nTap below to unlock the tip."

    count = 0
    for (uid,) in users:
        try:
            await context.bot.send_photo(chat_id=uid, photo=photo_id, caption=broadcast_msg, reply_markup=keyboard, parse_mode="Markdown")
            count += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await context.bot.send_photo(chat_id=uid, photo=photo_id, caption=broadcast_msg, reply_markup=keyboard, parse_mode="Markdown")
        except: pass
    await update.message.reply_text(f"✅ Game #{post_id} sent to {count} users.")

# --- BUTTON HANDLING ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    u = query.from_user
    if not query.data.startswith("GET_"): return
    
    post_id = int(query.data.split("_")[1])

    if not db_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.id,)):
        await query.answer("❌ Access Denied. You are not whitelisted.", show_alert=True)
        return

    post = db_query("SELECT tip_text, created_at FROM posts WHERE post_id = ?", (post_id,))
    if not post:
        await query.answer("❌ This game was deleted by the admin.", show_alert=True)
        return

    tip_text, created_at = post
    created_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
    if datetime.utcnow() > created_dt + timedelta(minutes=EXPIRY_MINUTES):
        await query.answer("⏰ This tip has expired (2hr limit).", show_alert=True)
        return

    db_query("INSERT OR IGNORE INTO claims (user_id, post_id) VALUES (?, ?)", (u.id, post_id), commit=True)
    await query.answer()
    await query.message.reply_text(f"🎯 **Tip for Game #{post_id}:**\n\n{tip_text}", parse_mode="Markdown")
    
    user_label = f"{u.full_name} (@{u.username or 'N/A'})"
    await alert_admins(context, f"💰 **Tip Unlocked!**\nUser: {user_label}\nGame: #{post_id}")

# --- OTHER COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username), commit=True)
    await update.message.reply_text("Welcome! Use /addme to request access to betting tips.")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    since = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    rows = db_query("""
        SELECT c.post_id, u.full_name, u.username, c.claimed_at 
        FROM claims c JOIN users u ON c.user_id = u.user_id 
        WHERE c.claimed_at > ? ORDER BY c.claimed_at DESC""", (since,), fetchall=True)
    if not rows:
        await update.message.reply_text("No tips opened in the last 24h.")
        return
    text = "📊 **Last 24h Claims:**\n"
    for pid, name, user, time in rows:
        text += f"• Game #{pid}: {name} (@{user or 'N/A'}) at {time}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def set_post_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    val = context.args[0]
    if val.isdigit():
        db_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (val,), commit=True)
        await update.message.reply_text(f"✅ Next Game will be #{int(val)+1}")

async def edit_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or len(context.args) < 2: return
    pid, text = context.args[0], " ".join(context.args[1:])
    db_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (text, pid), commit=True)
    await update.message.reply_text(f"✅ Tip #{pid} updated.")

async def addme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text("Approval request sent to admin.")
    await alert_admins(context, f"📥 **Access Request**\nName: {u.full_name}\nUser: @{u.username}\nID: `{u.id}`\nApprove: `/approve {u.id}`")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    uid = int(context.args[0])
    db_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,), commit=True)
    await update.message.reply_text(f"✅ User `{uid}` approved.")
    try: await context.bot.send_message(chat_id=uid, text="✅ You have been approved for tips!")
    except: pass

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("addme", addme))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("list", list_users))
    app.add_handler(CommandHandler("delete", delete_post))
    app.add_handler(CommandHandler("setpostid", set_post_id))
    app.add_handler(CommandHandler("edit", edit_tip))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("Bot is live...")
    app.run_polling()

if __name__ == "__main__":
    main()
