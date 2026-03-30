import os
import sqlite3
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
# Ensure Channel ID starts with -100
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
ADMIN_ID = 5024732090 

# --- DB INIT ---
def db_init():
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT)")
        con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_post_id', '0')")
        con.commit()
        con.close()
    except Exception as e:
        print(f"DB INIT ERROR: {e}")

# --- THE HANDLER ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not update.message.photo: return

    # 1. Get Photo Data
    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()

    # 2. Database Update (Wrapped in Try)
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT value FROM meta WHERE key = 'current_post_id'")
        post_id = int(cur.fetchone()[0]) + 1
        cur.execute("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),))
        cur.execute("INSERT INTO posts (post_id, tip_text, photo_id) VALUES (?, ?, ?)", (post_id, caption, photo_id))
        con.commit()
        con.close()
    except Exception as e:
        await update.message.reply_text(f"❌ Database Error: {e}")
        return

    # 3. Channel Broadcast (Wrapped in Try)
    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Unlock Selection", callback_data=f"GET_{post_id}")]])
        try:
            # We send a NEW photo to the channel, not a forward
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo_id,
                caption=f"**Post #{post_id}**\n**Status:** Active",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            await update.message.reply_text(f"✅ Game #{post_id} Live in Channel.")
        except Exception as e:
            # THIS WILL TELL US THE PROBLEM
            await update.message.reply_text(f"❌ Channel Error: {e}\nCheck if Bot is Admin and Channel ID is correct.")
    else:
        await update.message.reply_text("❌ Error: No CHANNEL_ID found in Railway Variables.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("RICTA TERMINAL: Online.")

def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    # Listen for photos specifically
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
