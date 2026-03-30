import os
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
ADMIN_ID = 5024732090 

# --- DB HANDLER ---
def run_query(query, params=(), fetch=False):
    with sqlite3.connect(DB_PATH, timeout=15) as con:
        cur = con.cursor()
        cur.execute(query, params)
        if fetch:
            return cur.fetchone()
        con.commit()

def db_init():
    # 1. Create basic tables
    run_query("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)")
    run_query("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT)")
    run_query("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_post_id', '0')")
    
    # 2. THE REPAIR: Add photo_id column if it's missing
    try:
        run_query("ALTER TABLE posts ADD COLUMN photo_id TEXT")
        print("Database Repair: Added photo_id column.")
    except sqlite3.OperationalError:
        # Column already exists, ignore the error
        pass

# --- HANDLERS ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not update.message.photo: return

    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()

    try:
        # Update Post ID and Save
        res = run_query("SELECT value FROM meta WHERE key = 'current_post_id'", fetch=True)
        post_id = int(res[0]) + 1
        run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),))
        run_query("INSERT INTO posts (post_id, tip_text, photo_id) VALUES (?, ?, ?)", (post_id, caption, photo_id))
    except Exception as e:
        await update.message.reply_text(f"❌ DB Error: {e}")
        return

    # Broadcast to Channel
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
            await update.message.reply_text(f"✅ Game #{post_id} Live in Channel.")
        except Exception as e:
            await update.message.reply_text(f"❌ Channel Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("RICTA TERMINAL: Online.")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args: return
    uid = int(context.args[0])
    run_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,))
    await update.message.reply_text(f"Authorized ID: {uid}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    u_id = query.from_user.id
    
    # Whitelist Check
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u_id,), fetch=True):
        await query.answer("Access Denied.", show_alert=True)
        return
    
    post_id = int(query.data.split("_")[1])
    post = run_query("SELECT tip_text, photo_id FROM posts WHERE post_id = ?", (post_id,), fetch=True)
    
    if post:
        await query.answer()
        await context.bot.send_photo(
            chat_id=u_id, 
            photo=post[1], 
            caption=f"**Data Sheet #{post_id}**\n\n**Selection:** {post[0]}\n\nSettlement: @R1cta", 
            parse_mode="Markdown"
        )

def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
