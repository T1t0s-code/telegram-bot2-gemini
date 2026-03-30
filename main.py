import os
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "").replace(" ", "")
ADMIN_IDS = {int(x) for x in ADMIN_IDS_RAW.split(",") if x.isdigit()}
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")

# --- DATABASE ---
def db_query(query, params=(), commit=False, fetchall=False):
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute(query, params)
        if commit: con.commit()
        return cur.fetchall() if fetchall else cur.fetchone()
    finally: con.close()

def db_init():
    db_query("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, post_id))", commit=True)
    db_query("INSERT OR IGNORE INTO meta(key, value) VALUES('current_post_id', '0')", commit=True)

# --- ADMIN COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username), commit=True)
    status = "✅ ADMIN" if u.id in ADMIN_IDS else "❌ USER"
    await update.message.reply_text(f"RICTA TERMINAL\nStatus: {status}\nID: `{u.id}`", parse_mode="Markdown")

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    msg = (
        "**Terminal Admin Commands:**\n"
        "• Send Photo + Caption: Broadcast Tip\n"
        "• `/approve ID`: Add partner\n"
        "• `/list`: Show all authorized partners\n"
        "• `/report`: Recent activity (24h)\n"
        "• `/delete ID`: Remove a specific post"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args: return
    uid = int(context.args[0])
    db_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,), commit=True)
    await update.message.reply_text(f"✅ Authorized: {uid}")

async def list_partners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    rows = db_query("SELECT w.user_id, u.full_name, u.username FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetchall=True)
    if not rows:
        await update.message.reply_text("No partners authorized.")
        return
    res = "\n".join([f"• `{r[0]}` | {r[1]} (@{r[2]})" for r in rows])
    await update.message.reply_text(f"**Authorized Partners:**\n{res}", parse_mode="Markdown")

async def delete_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args: return
    pid = int(context.args[0])
    db_query("DELETE FROM posts WHERE post_id = ?", (pid,), commit=True)
    await update.message.reply_text(f"🗑 Post #{pid} removed from database.")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    rows = db_query("SELECT c.post_id, u.full_name, c.claimed_at FROM claims c JOIN users u ON c.user_id = u.user_id ORDER BY c.claimed_at DESC LIMIT 15", fetchall=True)
    if not rows:
        await update.message.reply_text("No activity.")
        return
    res = "\n".join([f"#{r[0]} | {r[1]} | {r[2][11:16]}" for r in rows])
    await update.message.reply_text(f"**Activity (Recent):**\n{res}", parse_mode="Markdown")

# --- PHOTO BROADCAST ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    
    caption = (update.message.caption or "No Data").strip()
    photo_id = update.message.photo[-1].file_id

    # Post ID Logic
    res = db_query("SELECT value FROM meta WHERE key = 'current_post_id'")
    post_id = int(res[0]) + 1
    db_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),), commit=True)
    db_query("INSERT INTO posts (post_id, tip_text, photo_id) VALUES (?, ?, ?)", (post_id, caption, photo_id), commit=True)

    # Channel Post
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Unlock Selection", callback_data=f"GET_{post_id}")]])
    if CHANNEL_ID:
        try:
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=f"**Post #{post_id}**\n**Status:** Active", reply_markup=keyboard, parse_mode="Markdown")
            await update.message.reply_text(f"🚀 Data #{post_id} Live.")
        except Exception as e:
            await update.message.reply_text(f"❌ Channel Error: {e}")

# --- CALLBACK ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    u_id = query.from_user.id
    if not query.data.startswith("GET_"): return
    
    if not db_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u_id,)):
        await query.answer("Access Denied. Contact @R1cta", show_alert=True)
        return

    post_id = int(query.data.split("_")[1])
    post = db_query("SELECT tip_text, photo_id FROM posts WHERE post_id = ?", (post_id,))
    
    if post:
        db_query("INSERT OR IGNORE INTO claims (user_id, post_id) VALUES (?, ?)", (u_id, post_id), commit=True)
        await query.answer()
        await context.bot.send_photo(chat_id=u_id, photo=post[1], caption=f"**Data Sheet #{post_id}**\n\n**Selection:** {post[0]}\n\nSettlement: @R1cta", parse_mode="Markdown")

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("list", list_partners))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("delete", delete_post))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
