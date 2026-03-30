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
    if update.effective_user.id != ADMIN_ID: return
    if not update.message.photo: return

    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()

    res = run_query("SELECT value FROM meta WHERE key = 'current_post_id'", fetch_one=True)
    post_id = int(res[0]) + 1
    run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),))
    
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run_query("INSERT INTO posts (post_id, tip_text, photo_id, created_at) VALUES (?, ?, ?, ?)", 
              (post_id, caption, photo_id, now_str))

    if CHANNEL_ID:
        bot_user = await context.bot.get_me()
        # DEEP LINK: This forces the screen to switch to the bot
        jump_url = f"https://t.me/{bot_user.username}?start=GET_{post_id}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Unlock Selection", url=jump_url)]])
        
        channel_msg = f"🎯 **Game #{post_id}**\n━━━━━━━━━━━━━━━\n🔹 **Status:** Active\n🔹 **Problem? DM:** @R1cta\n\n⚠️ *Available for {EXPIRY_MINUTES} mins.*"
        
        try:
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=channel_msg, reply_markup=keyboard, parse_mode="Markdown")
            await update.message.reply_text(f"🚀 **Game #{post_id}** is now LIVE.")
        except Exception as e:
            await update.message.reply_text(f"❌ Broadcast Error: {e}")

# --- START & DEEP LINK HANDLER ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    
    # Check for Deep Link (e.g., /start GET_15)
    if context.args and context.args[0].startswith("GET_"):
        if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.id,), fetch_one=True):
            await update.message.reply_text("🚫 **Access Denied.** Contact @R1cta to be authorized.")
            return

        post_id = context.args[0].split("_")[1]
        post = run_query("SELECT tip_text, photo_id, created_at FROM posts WHERE post_id = ?", (post_id,), fetch_one=True)
        
        if post:
            created_dt = datetime.strptime(post[2], '%Y-%m-%d %H:%M:%S')
            if datetime.now() > created_dt + timedelta(minutes=EXPIRY_MINUTES):
                await update.message.reply_text("⌛ This selection has expired.")
                return

            run_query("INSERT OR IGNORE INTO claims (user_id, post_id, claimed_at) VALUES (?, ?, ?)", (u.id, post_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            await context.bot.send_photo(chat_id=u.id, photo=post[1], caption=f"📁 **Data Sheet #{post_id}**\n\n✅ **Selection:** {post[0]}\n\n🤝 Settlement: @R1cta", parse_mode="Markdown")
            return

    # Regular Start
    if u.id == ADMIN_ID:
        await update.message.reply_text("⚡ **TERMINAL ONLINE**\nUse `/admin` for commands.")
    else:
        await update.message.reply_text("🚫 **RICTA TERMINAL**\nAccess restricted to verified partners.\n\nTo apply, contact @R1cta.")

# --- ADMIN TOOLS ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = "**RICTA ADMIN**\n\n👤 `/approve ID`, `/remove ID`, `/list`\n📊 `/report`, `/clearreport`\n📝 `/edit ID Text`, `/delete ID`, `/setid ID`"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args: return
    run_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (int(context.args[0]),))
    await update.message.reply_text(f"✅ Partner `{context.args[0]}` Added.")

async def remove_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args: return
    run_query("DELETE FROM whitelist WHERE user_id = ?", (int(context.args[0]),))
    await update.message.reply_text(f"❌ Partner `{context.args[0]}` Removed.")

async def edit_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or len(context.args) < 2: return
    run_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (" ".join(context.args[1:]), context.args[0]))
    await update.message.reply_text(f"📝 Post #{context.args[0]} updated.")

async def delete_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args: return
    run_query("DELETE FROM posts WHERE post_id = ?", (context.args[0],))
    await update.message.reply_text(f"🗑 Post #{context.args[0]} deleted.")

async def list_partners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    rows = run_query("SELECT w.user_id, u.full_name, u.username FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)
    res = "\n".join([f"• `{r[0]}` | {r[1]} (@{r[2] if r[2] else 'None'})" for r in rows]) if rows else "None."
    await update.message.reply_text(f"👥 **Partners:**\n{res}", parse_mode="Markdown")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    rows = run_query("SELECT c.post_id, u.full_name, u.username, c.claimed_at FROM claims c JOIN users u ON c.user_id = u.user_id ORDER BY c.claimed_at DESC LIMIT 20", fetch_all=True)
    res = "\n".join([f"#{r[0]} | {r[1]} | {r[3][11:16]}" for r in rows]) if rows else "Empty."
    await update.message.reply_text(f"📊 **Recent Activity:**\n{res}", parse_mode="Markdown")

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("remove", remove_partner))
    app.add_handler(CommandHandler("edit", edit_post))
    app.add_handler(CommandHandler("delete", delete_post))
    app.add_handler(CommandHandler("list", list_partners))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("clearreport", lambda u, c: run_query("DELETE FROM claims")))
    app.add_handler(CommandHandler("setid", lambda u, c: run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (c.args[0],)) if c.args else None))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
