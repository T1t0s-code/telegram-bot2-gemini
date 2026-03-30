import os
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
PRIMARY_ADMIN = 5024732090 # Your Verified ID

def db_query(query, params=(), commit=False, fetchall=False):
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute(query, params)
        if commit: con.commit()
        return cur.fetchall() if fetchall else cur.fetchone()
    finally: con.close()

# --- THE BROADCASTER ---
async def handle_everything(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # If it's a command, let the CommandHandlers handle it
    if update.message.text and update.message.text.startswith('/'): return
    
    # Only you can broadcast
    if update.effective_user.id != PRIMARY_ADMIN: return

    # Check for Photo
    if not update.message.photo: return

    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()

    # Save to DB
    res = db_query("SELECT value FROM meta WHERE key = 'current_post_id'")
    post_id = int(res[0]) + 1
    db_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),), commit=True)
    db_query("INSERT INTO posts (post_id, tip_text, photo_id) VALUES (?, ?, ?)", (post_id, caption, photo_id), commit=True)

    # Post to Channel
    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Unlock Selection", callback_data=f"GET_{post_id}")]])
        try:
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo_id,
                caption=f"**Post #{post_id}**\n**Status:** Active",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            await update.message.reply_text(f"✅ Game #{post_id} broadcasted.")
        except Exception as e:
            await update.message.reply_text(f"❌ Channel Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"RICTA TERMINAL\nAdmin: {update.effective_user.id == PRIMARY_ADMIN}")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PRIMARY_ADMIN: return
    uid = int(context.args[0])
    db_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,), commit=True)
    await update.message.reply_text(f"Authorized: {uid}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    u_id = query.from_user.id
    if not db_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u_id,)):
        await query.answer("Access Denied.", show_alert=True)
        return
    post_id = int(query.data.split("_")[1])
    post = db_query("SELECT tip_text, photo_id FROM posts WHERE post_id = ?", (post_id,))
    if post:
        await query.answer()
        await context.bot.send_photo(chat_id=u_id, photo=post[1], caption=f"**Data Sheet #{post_id}**\n\n**Selection:** {post[0]}\n\nSettlement: @R1cta", parse_mode="Markdown")

def main():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT)")
    con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_post_id', '0')")
    con.commit()
    con.close()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve))
    # THE FIX: Catch ALL messages that aren't commands
    app.add_handler(MessageHandler(filters.ALL, handle_everything))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
