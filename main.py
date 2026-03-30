import os
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
ADMIN_ID = 5024732090

# --- DB ---
def db_init():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT)")
    con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_post_id', '0')")
    con.commit()
    con.close()

# --- THE HANDLER ---
async def catch_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Log to Railway console so we can see if it hears ANYTHING
    print(f"Message received from {update.effective_user.id}")
    
    if update.effective_user.id != ADMIN_ID: return
    if not update.message or not update.message.photo: return

    # If we are here, it's a photo from you
    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT value FROM meta WHERE key = 'current_post_id'")
    post_id = int(cur.fetchone()[0]) + 1
    cur.execute("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),))
    cur.execute("INSERT INTO posts (post_id, tip_text, photo_id) VALUES (?, ?, ?)", (post_id, caption, photo_id))
    con.commit()
    con.close()

    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Unlock Selection", callback_data=f"GET_{post_id}")]])
        await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=f"**Post #{post_id}**", reply_markup=keyboard, parse_mode="Markdown")
        await update.message.reply_text(f"✅ Game #{post_id} Live.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Terminal Online.")

def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # WE PUT THE PHOTO CATCHER FIRST
    app.add_handler(MessageHandler(filters.PHOTO, catch_all))
    app.add_handler(CommandHandler("start", start))
    app.run_polling()

if __name__ == "__main__":
    main()
