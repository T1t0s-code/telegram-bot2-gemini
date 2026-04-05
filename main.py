import os
import sqlite3
import asyncio
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
ADMIN_ID = 5024732090 
EXPIRY_SECONDS = 120 * 60  # 120 minutes in seconds

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
    # Using INTEGER for created_at to store Unix Timestamps
    run_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, channel_msg_id INTEGER, created_at INTEGER, is_expired INTEGER DEFAULT 0)")
    run_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, claimed_at INTEGER, PRIMARY KEY (user_id, post_id))")
    run_query("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_post_id', '0')")

# --- AUTO-EXPIRY (THE MATH FIX) ---
async def start_expiry_monitor(app):
    while True:
        try:
            now = int(time.time())
            # Find games where (Current Time - Created Time) > 7200 seconds
            expired = run_query(
                "SELECT post_id, channel_msg_id FROM posts WHERE is_expired = 0 AND (? - created_at) > ?", 
                (now, EXPIRY_SECONDS), 
                fetch_all=True
            )
            
            for p_id, m_id in expired:
                run_query("UPDATE posts SET is_expired = 1 WHERE post_id = ?", (p_id,))
                if CHANNEL_ID and m_id:
                    try:
                        text = f"🏆 **Game #{p_id}**\n━━━━━━━━━━━━━━━\nStatus: **EXPIRED**\nFor access dm @R1cta"
                        await app.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=m_id, caption=text, reply_markup=None, parse_mode="Markdown")
                    except: pass
        except: pass
        await asyncio.sleep(60)

# --- BROADCASTER ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message.photo: return
    
    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()
    
    res = run_query("SELECT value FROM meta WHERE key = 'current_post_id'", fetch_one=True)
    post_id = int(res[0]) + 1
    
    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Unlock Selection", callback_data=f"GET_{post_id}")]])
        msg = f"🏆 **Game #{post_id}**\n━━━━━━━━━━━━━━━\nStatus: **ACTIVE**\nGet the game at @Ricta_Terminal_bot\nFor access dm @R1cta"
        
        try:
            sent = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=msg, reply_markup=keyboard, parse_mode="Markdown")
            
            # Save the exact current second (Unix Timestamp)
            now_ts = int(time.time())
            run_query("INSERT INTO posts (post_id, tip_text, photo_id, channel_msg_id, created_at) VALUES (?, ?, ?, ?, ?)", 
                      (post_id, caption, photo_id, sent.message_id, now_ts))
            run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),))
            
            await update.message.reply_text(f"✅ **Game #{post_id} Live.**")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

# --- DELIVERY ---
async def deliver(user_id, game_id, context):
    post = run_query("SELECT tip_text, photo_id, created_at, is_expired FROM posts WHERE post_id = ?", (game_id,), fetch_one=True)
    if not post: return "Game not found."
    
    # Double Check Expiry via Math
    now = int(time.time())
    if post[3] == 1 or (now - post[2]) > EXPIRY_SECONDS:
        if post[3] == 0: run_query("UPDATE posts SET is_expired = 1 WHERE post_id = ?", (game_id,))
        return f"Game #{game_id} has expired."
    
    run_query("INSERT OR IGNORE INTO claims (user_id, post_id, claimed_at) VALUES (?, ?, ?)", (user_id, game_id, now))
    caption = f"Game #{game_id}\n\nSelection: {post[0]}\n\ndm @R1cta"
    await context.bot.send_photo(chat_id=user_id, photo=post[1], caption=caption)
    return None

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    
    if u.id == ADMIN_ID:
        await update.message.reply_text("⚡ **TERMINAL ONLINE**\nUse `/admin` for dashboard.")
    else:
        await update.message.reply_text("RICTA TERMINAL\nAccess restricted to approved partners.\n\nTo request access, use `/addme`.")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = (
        "⚙️ **ADMIN CONTROL**\n"
        "━━━━━━━━━━━━━━━\n"
        "👤 `/approve ID` | `/remove ID` | `/list`\n"
        "🎮 `/edit ID Text` | `/delete ID` | `/setid ID`\n"
        "📊 `/report` | `/audit` | `/clearstats`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = run_query("SELECT u.full_name, u.user_id, COUNT(DISTINCT c.post_id), GROUP_CONCAT(DISTINCT c.post_id) FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id LEFT JOIN claims c ON w.user_id = c.user_id GROUP BY w.user_id ORDER BY COUNT(DISTINCT c.post_id) DESC", fetch_all=True)
    res = ["📋 **PARTNER AUDIT**\n━━━━━━━━━━━━━━━"]
    for n, uid, count, games in data:
        res.append(f"👤 {n if n else 'Unknown'} (`{uid}`)\n└ {count} games: {games if games else 'None'}\n")
    await update.message.reply_text("\n".join(res), parse_mode="Markdown")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    rows = run_query("SELECT c.post_id, u.full_name, c.claimed_at FROM claims c JOIN users u ON c.user_id = u.user_id ORDER BY c.claimed_at DESC LIMIT 15", fetch_all=True)
    res = ["📊 **LIVE FEED**\n━━━━━━━━━━━━━━━"]
    for pid, name, ts in rows:
        # Convert Unix timestamp back to readable time for the report
        readable_time = time.strftime('%H:%M', time.localtime(ts))
        res.append(f"#{pid} | {name} | {readable_time}")
    await update.message.reply_text("\n".join(res) if rows else "No activity.", parse_mode="Markdown")

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addme", lambda u, c: u.message.reply_text(f"Your ID: `{u.effective_user.id}`\nSend to @R1cta.")))
    app.add_handler(CommandHandler("send", lambda u, c: deliver(u.effective_user.id, c.args[0], c) if run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.effective_user.id,), fetch_one=True) and c.args else None))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("approve", lambda u, c: run_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (c.args[0],)) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("remove", lambda u, c: run_query("DELETE FROM whitelist WHERE user_id = ?", (c.args[0],)) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("audit", audit))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("list", lambda u, c: u.message.reply_text("\n".join([f"• {r[0]} | {r[1]}" for r in run_query("SELECT w.user_id, u.full_name FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)]) or "Empty") if u.effective_user.id == ADMIN_ID else None))
    app.add_handler(CommandHandler("edit", lambda u, c: run_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (" ".join(c.args[1:]), c.args[0])) if u.effective_user.id == ADMIN_ID and len(c.args) >= 2 else None))
    app.add_handler(CommandHandler("delete", lambda u, c: run_query("DELETE FROM posts WHERE post_id = ?", (c.args[0],)) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("setid", lambda u, c: run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (c.args[0],)) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("clearstats", lambda u, c: run_query("DELETE FROM claims") if u.effective_user.id == ADMIN_ID else None))
    
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    app.add_handler(CallbackQueryHandler(lambda u, c: callback(u, c)))
    
    loop = asyncio.get_event_loop()
    loop.create_task(start_expiry_monitor(app))
    app.run_polling(drop_pending_updates=True)

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (q.from_user.id,), fetch_one=True):
        await q.answer("Access Denied.", show_alert=True)
        return
    err = await deliver(q.from_user.id, q.data.split("_")[1], context)
    if err: await q.answer(err, show_alert=True)
    else: await q.answer()

if __name__ == "__main__":
    main()
