import os
import sqlite3
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
ADMIN_ID = 5024732090 
EXPIRY_MINUTES = 120  # 2-hour window for partners to unlock

# --- DATABASE ENGINE (Anti-Lock) ---
def run_query(query, params=(), fetch_one=False, fetch_all=False):
    with sqlite3.connect(DB_PATH, timeout=20) as con:
        cur = con.cursor()
        cur.execute(query, params)
        if fetch_one: return cur.fetchone()
        if fetch_all: return cur.fetchall()
        con.commit()

def db_init():
    # Tables for Whitelist, User Profiles, Meta IDs, Post Data, and Claim History
    run_query("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY)")
    run_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
    run_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, claimed_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, post_id))")
    run_query("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_post_id', '0')")
    
    # Migrations for existing DBs
    try: run_query("ALTER TABLE posts ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
    except: pass
    try: run_query("ALTER TABLE posts ADD COLUMN photo_id TEXT")
    except: pass

# --- BROADCASTER ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not update.message.photo: return

    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection Provided").strip()

    # Get Next Post ID
    res = run_query("SELECT value FROM meta WHERE key = 'current_post_id'", fetch_one=True)
    post_id = int(res[0]) + 1
    run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),))
    
    # Save Post
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run_query("INSERT INTO posts (post_id, tip_text, photo_id, created_at) VALUES (?, ?, ?, ?)", 
              (post_id, caption, photo_id, now_str))

    # Channel Output (Professional Formatting)
    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📩 Unlock Selection", callback_data=f"GET_{post_id}")]])
        channel_msg = (
            f"🎯 **Game #{post_id}**\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔹 **Status:** Active\n"
            f"🔹 **Support:** @R1cta\n\n"
            f"⚠️ *Available for {EXPIRY_MINUTES} minutes.*"
        )
        try:
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=channel_msg, reply_markup=keyboard, parse_mode="Markdown")
            await update.message.reply_text(f"🚀 **Game #{post_id}** is now LIVE.")
        except Exception as e:
            await update.message.reply_text(f"❌ Broadcast Error: {e}")

# --- ADMIN COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    
    if u.id == ADMIN_ID:
        await update.message.reply_text("⚡ **TERMINAL ONLINE**\nUse `/admin` to view the control panel.")
    else:
        await update.message.reply_text("🚫 **RICTA TERMINAL**\nAccess is limited to verified partners.\n\nTo request access, contact @R1cta.")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = (
        "🛠 **ADMIN CONTROL PANEL**\n\n"
        "👤 **Users:**\n"
        "• `/approve ID` - Add partner\n"
        "• `/list` - View all partners\n\n"
        "📊 **Data:**\n"
        "• `/report` - Recent claims\n"
        "• `/clearreport` - Wipe history\n\n"
        "📝 **Post Management:**\n"
        "• `/edit ID NewText` - Change selection\n"
        "• `/delete ID` - Wipe post\n"
        "• `/setid ID` - Force counter\n"
        "• `/postid` - Current count"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def edit_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or len(context.args) < 2:
        await update.message.reply_text("Usage: `/edit [ID] [New Text]`")
        return
    pid = context.args[0]
    new_text = " ".join(context.args[1:])
    run_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (new_text, pid))
    await update.message.reply_text(f"✅ **Post #{pid}** has been updated.")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args: return
    uid = int(context.args[0])
    run_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,))
    await update.message.reply_text(f"✅ Partner `{uid}` Authorized.")

async def list_partners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    rows = run_query("SELECT w.user_id, u.full_name, u.username FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)
    res = "\n".join([f"• `{r[0]}` | {r[1]} (@{r[2] if r[2] else 'None'})" for r in rows]) if rows else "No partners."
    await update.message.reply_text(f"👥 **Authorized Partners:**\n{res}", parse_mode="Markdown")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    rows = run_query("""
        SELECT c.post_id, u.full_name, u.username, c.claimed_at 
        FROM claims c JOIN users u ON c.user_id = u.user_id 
        ORDER BY c.claimed_at DESC LIMIT 25""", fetch_all=True)
    res = "\n".join([f"#{r[0]} | {r[1]} (@{r[2] if r[2] else 'None'}) | {r[3][11:16]}" for r in rows]) if rows else "No activity."
    await update.message.reply_text(f"📈 **Activity Report:**\n{res}", parse_mode="Markdown")

async def post_id_mgmt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    res = run_query("SELECT value FROM meta WHERE key = 'current_post_id'", fetch_one=True)
    await update.message.reply_text(f"🔢 **Current Post ID:** {res[0]}")

async def set_post_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args: return
    run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (context.args[0],))
    await update.message.reply_text(f"✅ Post ID set to **{context.args[0]}**")

async def delete_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args: return
    run_query("DELETE FROM posts WHERE post_id = ?", (context.args[0],))
    await update.message.reply_text(f"🗑 Post #{context.args[0]} deleted.")

async def clear_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    run_query("DELETE FROM claims")
    await update.message.reply_text("🧹 History cleared.")

# --- CALLBACK (The Security Gate) ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    u_id = query.from_user.id
    
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u_id,), fetch_one=True):
        await query.answer("❌ Access Denied. Contact @R1cta", show_alert=True)
        return
    
    post_id = int(query.data.split("_")[1])
    post = run_query("SELECT tip_text, photo_id, created_at FROM posts WHERE post_id = ?", (post_id,), fetch_one=True)
    
    if post:
        # Expiry Check
        created_dt = datetime.strptime(post[2], '%Y-%m-%d %H:%M:%S')
        if datetime.now() > created_dt + timedelta(minutes=EXPIRY_MINUTES):
            await query.answer("⌛ This selection has expired.", show_alert=True)
            return

        run_query("INSERT OR IGNORE INTO claims (user_id, post_id, claimed_at) VALUES (?, ?, ?)", 
                  (u_id, post_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        
        await query.answer()
        await context.bot.send_photo(
            chat_id=u_id, 
            photo=post[1], 
            caption=f"📁 **Data Sheet #{post_id}**\n\n✅ **Selection:** {post[0]}\n\n🤝 Settlement: @R1cta", 
            parse_mode="Markdown"
        )

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Register Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("list", list_partners))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("clearreport", clear_report))
    app.add_handler(CommandHandler("edit", edit_post))
    app.add_handler(CommandHandler("delete", delete_post))
    app.add_handler(CommandHandler("postid", post_id_mgmt))
    app.add_handler(CommandHandler("setid", set_post_id))
    
    # Register Media & Callbacks
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
