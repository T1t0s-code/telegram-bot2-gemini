import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
ADMIN_ID = 5024732090 
EXPIRY_MINUTES = 120 

# --- DB ENGINE ---
def run_query(query, params=(), fetch_one=False, fetch_all=False):
    with sqlite3.connect(DB_PATH, timeout=20) as con:
        cur = con.cursor()
        cur.execute(query, params)
        if fetch_one: return cur.fetchone()
        if fetch_all: return cur.fetchall()
        con.commit()

def db_init():
    run_query("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)")
    run_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, channel_msg_id INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, is_expired INTEGER DEFAULT 0)")
    run_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, post_id))")
    run_query("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_post_id', '0')")
    try: run_query("ALTER TABLE posts ADD COLUMN channel_msg_id INTEGER")
    except: pass
    try: run_query("ALTER TABLE posts ADD COLUMN is_expired INTEGER DEFAULT 0")
    except: pass

# --- FIXED AUTO-EXPIRY MONITOR (No JobQueue Required) ---
async def start_expiry_monitor(app):
    """Background loop that checks for expired games every 60 seconds."""
    while True:
        try:
            # Find games older than 120 mins that aren't marked expired yet
            expiry_limit = (datetime.now() - timedelta(minutes=EXPIRY_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
            expired_posts = run_query(
                "SELECT post_id, channel_msg_id FROM posts WHERE is_expired = 0 AND created_at < ?", 
                (expiry_limit,), 
                fetch_all=True
            )
            
            for post_id, msg_id in expired_posts:
                run_query("UPDATE posts SET is_expired = 1 WHERE post_id = ?", (post_id,))
                if CHANNEL_ID and msg_id:
                    try:
                        new_text = (
                            f"🏆 **Game #{post_id}**\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"📍 Status: **EXPIRED**\n"
                            f"💎 For next access dm @R1cta"
                        )
                        await app.bot.edit_message_caption(
                            chat_id=CHANNEL_ID,
                            message_id=msg_id,
                            caption=new_text,
                            reply_markup=None,
                            parse_mode="Markdown"
                        )
                    except Exception: pass
        except Exception: pass
        await asyncio.sleep(60)

# --- BROADCASTER ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message.photo: return
    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()
    res = run_query("SELECT value FROM meta WHERE key = 'current_post_id'", fetch_one=True)
    post_id = int(res[0]) + 1
    run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),))
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Unlock Selection", callback_data=f"GET_{post_id}")]])
        channel_msg = (
            f"🏆 **Game #{post_id}**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📍 Status: **ACTIVE**\n"
            f"📍 Use button or `/send {post_id}` at @Ricta_Terminal_bot\n"
            f"💎 For access dm @R1cta"
        )
        try:
            sent = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=channel_msg, reply_markup=keyboard, parse_mode="Markdown")
            run_query("INSERT INTO posts (post_id, tip_text, photo_id, channel_msg_id, created_at) VALUES (?, ?, ?, ?, ?)", 
                      (post_id, caption, photo_id, sent.message_id, now_str))
            await update.message.reply_text(f"Game #{post_id} Live.")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

# --- LOGIC ---
async def deliver_game(user_id, game_id, context):
    post = run_query("SELECT tip_text, photo_id, created_at, is_expired FROM posts WHERE post_id = ?", (game_id,), fetch_one=True)
    if post:
        if post[3] == 1: return f"Game #{game_id} has expired."
        run_query("INSERT OR IGNORE INTO claims (user_id, post_id, claimed_at) VALUES (?, ?, ?)", 
                  (user_id, game_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        dm_caption = f"Game #{game_id}\n\nSelection: {post[0]}\n\ndm @R1cta"
        await context.bot.send_photo(chat_id=user_id, photo=post[1], caption=dm_caption)
        return None
    return f"Game #{game_id} not found."

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    await update.message.reply_text("RICTA TERMINAL\nAccess restricted to approved partners.\n\nTo request access, use /addme.")

async def add_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your ID: `{update.effective_user.id}`\n\nSend this to @R1cta.", parse_mode="Markdown")

async def send_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.id,), fetch_one=True):
        await update.message.reply_text("Access restricted. Use /addme.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /send [number]")
        return
    error = await deliver_game(u.id, context.args[0], context)
    if error: await update.message.reply_text(error)

async def audit_partners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = run_query("""
        SELECT u.full_name, u.user_id, COUNT(DISTINCT c.post_id), GROUP_CONCAT(DISTINCT c.post_id)
        FROM whitelist w
        LEFT JOIN users u ON w.user_id = u.user_id
        LEFT JOIN claims c ON w.user_id = c.user_id
        GROUP BY w.user_id
        ORDER BY COUNT(DISTINCT c.post_id) DESC
    """, fetch_all=True)
    report_lines = ["📋 **PARTNER AUDIT**\n━━━━━━━━━━━━━━━"]
    for name, uid, count, games in data:
        name_str = name if name else "Unknown"
        game_list = games if games else "None"
        report_lines.append(f"👤 {name_str} (`{uid}`)\n└ {count} games: {game_list}\n")
    await update.message.reply_text("\n".join(report_lines), parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (query.from_user.id,), fetch_one=True):
        await query.answer("Access Denied.", show_alert=True)
        return
    game_id = query.data.split("_")[1]
    error = await deliver_game(query.from_user.id, game_id, context)
    if error: await query.answer(error, show_alert=True)
    else: await query.answer()

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addme", add_me))
    app.add_handler(CommandHandler("send", send_game))
    app.add_handler(CommandHandler("admin", lambda u, c: u.message.reply_text("⚙️ **ADMIN**\n/approve /remove /list /audit /clearstats")))
    app.add_handler(CommandHandler("audit", audit_partners))
    app.add_handler(CommandHandler("approve", lambda u, c: run_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (int(c.args[0]),)) if c.args else None))
    app.add_handler(CommandHandler("remove", lambda u, c: run_query("DELETE FROM whitelist WHERE user_id = ?", (int(c.args[0]),)) if c.args else None))
    app.add_handler(CommandHandler("list", lambda u, c: u.message.reply_text("\n".join([f"• {r[0]} | {r[1]}" for r in run_query("SELECT w.user_id, u.full_name FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)]) or "Empty")))
    app.add_handler(CommandHandler("clearstats", lambda u, c: run_query("DELETE FROM claims")))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Start background task manually
    loop = asyncio.get_event_loop()
    loop.create_task(start_expiry_monitor(app))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
