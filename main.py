import os
import sqlite3
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
    db_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER)", commit=True)
    db_query("INSERT OR IGNORE INTO meta(key, value) VALUES('current_post_id', '0')", commit=True)

# --- PHOTO HANDLER ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u_id = update.effective_user.id
    if u_id not in ADMIN_IDS: return

    # Ensure we have a photo and a caption
    if not update.message.photo: return
    caption = (update.message.caption or "No Selection Data").strip()
    photo_id = update.message.photo[-1].file_id

    # Get New Post ID
    res = db_query("SELECT value FROM meta WHERE key = 'current_post_id'")
    post_id = int(res[0]) + 1
    db_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),), commit=True)
    db_query("INSERT INTO posts (post_id, tip_text, photo_id) VALUES (?, ?, ?)", (post_id, caption, photo_id), commit=True)

    # Prepare Channel Post
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Unlock Selection", callback_data=f"GET_{post_id}")]])
    
    if CHANNEL_ID:
        try:
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo_id,
                caption=f"**Post #{post_id}**\n**Status:** Active",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            # Count whitelist members for the success message
            partners = db_query("SELECT COUNT(*) FROM whitelist")
            partner_count = partners[0] if partners else 0
            await update.message.reply_text(f"✅ Game #{post_id} broadcasted to Terminal ({partner_count} partners).")
        except Exception as e:
            await update.message.reply_text(f"❌ Broadcast Failed: {e}")

# --- OTHER COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username), commit=True)
    await update.message.reply_text(f"RICTA TERMINAL\nID: `{u.id}`", parse_mode="Markdown")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS or not context.args: return
    uid = int(context.args[0])
    db_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,), commit=True)
    await update.message.reply_text(f"✅ Authorized: {uid}")

async def list_partners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    rows = db_query("SELECT w.user_id FROM whitelist w", fetchall=True)
    res = "\n".join([f"• `{r[0]}`" for r in rows]) if rows else "No partners."
    await update.message.reply_text(f"**Authorized Partners:**\n{res}", parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    u_id = query.from_user.id
    if not query.data.startswith("GET_"): return
    if not db_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u_id,)):
        await query.answer("Access Denied.", show_alert=True)
        return
    post_id = int(query.data.split("_")[1])
    post = db_query("SELECT tip_text, photo_id FROM posts WHERE post_id = ?", (post_id,))
    if post:
        await query.answer()
        await context.bot.send_photo(chat_id=u_id, photo=post[1], caption=f"**Data Sheet #{post_id}**\n\n**Selection:** {post[0]}\n\nSettlement: @R1cta", parse_mode="Markdown")

def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("list", list_partners))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo)) # Listen for photos
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
