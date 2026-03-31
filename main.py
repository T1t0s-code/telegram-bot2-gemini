import os
import sqlite3
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
    run_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
    run_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, post_id))")
    run_query("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_post_id', '0')")
    try: run_query("ALTER TABLE posts ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
    except: pass

# --- BROADCASTER ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message.photo: return

    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()

    res = run_query("SELECT value FROM meta WHERE key = 'current_post_id'", fetch_one=True)
    post_id = int(res[0]) + 1
    run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),))
    
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run_query("INSERT INTO posts (post_id, tip_text, photo_id, created_at) VALUES (?, ?, ?, ?)", 
              (post_id, caption, photo_id, now_str))

    if CHANNEL_ID:
        # High-end Channel Format as discussed
        channel_msg = (
            f"🏆 **Game #{post_id}**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📍 Get the game at @Ricta_Terminal_bot\n"
            f"💎 For access dm @R1cta"
        )
        try:
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=channel_msg, parse_mode="Markdown")
            await update.message.reply_text(f"Game #{post_id} Live.")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

# --- PARTNER DATA RETRIEVAL (/send 10) ---
async def send_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    
    # Check Whitelist
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.id,), fetch_one=True):
        await update.message.reply_text("RICTA TERMINAL\nAccess restricted to approved partners.\n\nTo request access, use /addme.")
        return

    # Check for ID
    if not context.args:
        await update.message.reply_text("Usage: /send [game number] (Example: /send 10)")
        return

    game_id = context.args[0]
    post = run_query("SELECT tip_text, photo_id, created_at FROM posts WHERE post_id = ?", (game_id,), fetch_one=True)
    
    if post:
        # Expiry Check
        created_dt = datetime.strptime(post[2], '%Y-%m-%d %H:%M:%S')
        if datetime.now() > created_dt + timedelta(minutes=EXPIRY_MINUTES):
            await update.message.reply_text(f"Game #{game_id} has expired.")
            return

        # Log Activity
        run_query("INSERT OR IGNORE INTO claims (user_id, post_id, claimed_at) VALUES (?, ?, ?)", 
                  (u.id, game_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        # Clean Professional DM Format
        dm_caption = (
            f"Game #{game_id}\n\n"
            f"Selection: {post[0]}\n\n"
            f"dm @R1cta"
        )
        await context.bot.send_photo(chat_id=u.id, photo=post[1], caption=dm_caption)
    else:
        await update.message.reply_text(f"Game #{game_id} not found.")

# --- ADMIN & USER COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    if u.id == ADMIN_ID:
        await update.message.reply_text("Terminal Online.\nUse /admin for controls.")
    else:
        await update.message.reply_text("RICTA TERMINAL\nAccess restricted to approved partners.\n\nTo request access, use /addme.")

async def add_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text(f"Your ID: `{u.id}`\n\nSend this ID to @R1cta to request authorization.", parse_mode="Markdown")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = (
        "⚙️ **TERMINAL CONTROL**\n"
        "━━━━━━━━━━━━━━━\n"
        "👤 **USER MANAGEMENT**\n"
        "• `/approve ID` - Authorize partner\n"
        "• `/remove ID`  - Revoke access\n"
        "• `/list`       - View all partners\n\n"
        "🎮 **GAME MANAGEMENT**\n"
        "• `/edit ID Text` - Change selection\n"
        "• `/delete ID`    - Wipe game data\n"
        "• `/setid ID`     - Reset counter\n\n"
        "📊 **DATA**\n"
        "• `/report`      - Recent claims\n"
        "• `/clearstats`  - Wipe activity logs"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args: return
    run_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (int(context.args[0]),))
    await update.message.reply_text(f"Partner {context.args[0]} Added.")

async def remove_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args: return
    run_query("DELETE FROM whitelist WHERE user_id = ?", (int(context.args[0]),))
    await update.message.reply_text(f"Partner {context.args[0]} Removed.")

async def list_partners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    rows = run_query("SELECT w.user_id, u.full_name, u.username FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)
    res = "\n".join([f"• {r[0]} | {r[1]} (@{r[2] if r[2] else 'None'})" for r in rows]) if rows else "Empty."
    await update.message.reply_text(f"Partners:\n{res}")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    rows = run_query("SELECT c.post_id, u.full_name, u.username, c.claimed_at FROM claims c JOIN users u ON c.user_id = u.user_id ORDER BY c.claimed_at DESC LIMIT 25", fetch_all=True)
    res = "\n".join([f"#{r[0]} | {r[1]} | {r[3][11:16]}" for r in rows]) if rows else "No activity."
    await update.message.reply_text(f"Report:\n{res}")

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # User Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addme", add_me))
    app.add_handler(CommandHandler("send", send_game)) # THE /send 10 FEATURE
    
    # Admin Commands
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("remove", remove_partner))
    app.add_handler(CommandHandler("list", list_partners))
    app.add_handler(CommandHandler("edit", lambda u, c: run_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (" ".join(c.args[1:]), c.args[0])) if len(c.args) >= 2 else None))
    app.add_handler(CommandHandler("delete", lambda u, c: run_query("DELETE FROM posts WHERE post_id = ?", (c.args[0],)) if c.args else None))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("clearstats", lambda u, c: run_query("DELETE FROM claims")))
    app.add_handler(CommandHandler("setid", lambda u, c: run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (c.args[0],)) if c.args else None))
    
    # Broadcast Handler
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
