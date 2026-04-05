import os
import sqlite3
import asyncio
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
ADMIN_ID = 5024732090 
BOT_USERNAME = "Ricta_Terminal_bot" # Ensure this matches your bot handle

# --- DB ENGINE ---
def run_query(query, params=(), fetch_one=False, fetch_all=False):
    with sqlite3.connect(DB_PATH, timeout=20) as con:
        cur = con.cursor()
        cur.execute(query, params)
        if fetch_one: return cur.fetchone()
        if fetch_all: return cur.fetchall()
        con.commit()

def db_init():
    # Core Tables
    run_query("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY, added_at TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, channel_msg_id INTEGER, is_expired INTEGER DEFAULT 0)")
    run_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, claimed_at TEXT, PRIMARY KEY (user_id, post_id))")
    run_query("CREATE TABLE IF NOT EXISTS admin_notifications (user_id INTEGER, post_id INTEGER, admin_msg_id INTEGER, count INTEGER DEFAULT 1, PRIMARY KEY (user_id, post_id))")
    
    # Migration: Add timestamp columns if they don't exist
    try: run_query("ALTER TABLE whitelist ADD COLUMN added_at TEXT")
    except: pass
    try: run_query("ALTER TABLE claims ADD COLUMN claimed_at TEXT")
    except: pass

# --- AUTO-BACKUP TASK ---
async def backup_loop(app):
    while True:
        await asyncio.sleep(86400) # Wait 24 hours
        try:
            with open(DB_PATH, 'rb') as f:
                await app.bot.send_document(chat_id=ADMIN_ID, document=f, caption=f"📅 Scheduled Database Backup\n{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        except Exception as e:
            print(f"Backup failed: {e}")

# --- BROADCASTER ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message.photo: return
    
    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()
    
    max_id_row = run_query("SELECT MAX(post_id) FROM posts", fetch_one=True)
    post_id = (max_id_row[0] or 0) + 1
    
    # Deep Link URL
    quick_link = f"https://t.me/{BOT_USERNAME}?start=game_{post_id}"
    
    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Quick Claim", url=quick_link)],
            [InlineKeyboardButton("Unlock Selection", callback_data=f"GET_{post_id}")]
        ])
        msg = f"🏆 <b>Game #{post_id}</b>\n━━━━━━━━━━━━━━━\nStatus: <b>ACTIVE</b>\n\nClick below to claim instantly."
        
        try:
            sent = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=msg, reply_markup=keyboard, parse_mode="HTML")
            run_query("INSERT INTO posts (post_id, tip_text, photo_id, channel_msg_id, is_expired) VALUES (?, ?, ?, ?, 0)", 
                      (post_id, caption, photo_id, sent.message_id))
            await update.message.reply_text(f"✅ <b>Game #{post_id} Live.</b>\nLink: <code>{quick_link}</code>", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Broadcast Error: {e}")

# --- DELIVERY LOGIC ---
async def deliver(user_id, game_id, context):
    row = run_query("SELECT tip_text, photo_id, is_expired FROM posts WHERE post_id = ?", (game_id,), fetch_one=True)
    if not row: return "❌ Game not found."
    if row[2] == 1: return f"❌ Game #{game_id} has expired."
    
    # 1. Delivery
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_query("INSERT OR IGNORE INTO claims (user_id, post_id, claimed_at) VALUES (?, ?, ?)", (user_id, game_id, now))
    caption = f"Game #{game_id}\n\nSelection: {row[0]}\n\ndm @R1cta"
    await context.bot.send_photo(chat_id=user_id, photo=row[1], caption=caption)

    # 2. Admin Alert Logic
    user_row = run_query("SELECT full_name FROM users WHERE user_id = ?", (user_id,), fetch_one=True)
    user_name = user_row[0] if user_row else "Unknown"
    notif = run_query("SELECT admin_msg_id, count FROM admin_notifications WHERE user_id = ? AND post_id = ?", (user_id, game_id), fetch_one=True)
    
    if notif:
        msg_id, current_count = notif
        new_count = current_count + 1
        text = f"👤 <b>{user_name}</b> (<code>{user_id}</code>)\n📥 Claimed <b>Game #{game_id}</b> ({new_count}x)"
        try:
            await context.bot.edit_message_text(chat_id=ADMIN_ID, message_id=msg_id, text=text, parse_mode="HTML")
            run_query("UPDATE admin_notifications SET count = ? WHERE user_id = ? AND post_id = ?", (new_count, user_id, game_id))
        except:
            sent = await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
            run_query("UPDATE admin_notifications SET admin_msg_id = ?, count = ? WHERE user_id = ? AND post_id = ?", (sent.message_id, new_count, user_id, game_id))
    else:
        text = f"👤 <b>{user_name}</b> (<code>{user_id}</code>)\n📥 Claimed <b>Game #{game_id}</b>"
        sent = await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
        run_query("INSERT INTO admin_notifications (user_id, post_id, admin_msg_id, count) VALUES (?, ?, ?, 1)", (user_id, game_id, sent.message_id))
    
    return None

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    
    # Deep-Link Check (game_X)
    if context.args and context.args[0].startswith("game_"):
        if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.id,), fetch_one=True):
            return await update.message.reply_text("❌ Access Denied. Apply at @R1cta.")
        game_id = context.args[0].split("_")[1]
        err = await deliver(u.id, game_id, context)
        if err: await update.message.reply_text(err)
        return

    if u.id == ADMIN_ID:
        await update.message.reply_text("⚡ <b>TERMINAL ONLINE</b>\n/admin for dashboard.", parse_mode="HTML")
    else:
        await update.message.reply_text("RICTA TERMINAL\nAccess Restricted.\nUse /addme to get your ID.")

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    menu = (
        "⚙️ <b>TERMINAL DASHBOARD</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👥 <b>PARTNERS</b>\n"
        "• /list | /audit\n"
        "• /whois <code>ID</code>\n"
        "• /approve <code>ID</code> | /remove <code>ID</code>\n\n"
        "🎮 <b>GAMES</b>\n"
        "• /online | /report\n"
        "• /edit <code>ID TEXT</code>\n"
        "• /expire <code>ID</code> | /delete <code>ID</code>\n\n"
        "💾 <b>SYSTEM</b>\n"
        "• /backup — <i>Manual export</i>\n"
        "• /clearstats — <i>Reset activity</i>"
    )
    await update.message.reply_text(menu, parse_mode="HTML")

async def whois_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args: return await update.message.reply_text("Usage: /whois <code>ID</code>", parse_mode="HTML")
    uid = context.args[0]
    
    user = run_query("SELECT full_name, username FROM users WHERE user_id = ?", (uid,), fetch_one=True)
    white = run_query("SELECT added_at FROM whitelist WHERE user_id = ?", (uid,), fetch_one=True)
    claims = run_query("SELECT post_id, claimed_at FROM claims WHERE user_id = ? ORDER BY claimed_at DESC LIMIT 5", (uid,), fetch_all=True)
    total = run_query("SELECT COUNT(*) FROM claims WHERE user_id = ?", (uid,), fetch_one=True)

    if not white: return await update.message.reply_text("❌ This ID is not in the whitelist.")
    
    res = [
        f"👤 <b>PARTNER DEEP-DIVE</b>",
        f"━━━━━━━━━━━━━━━",
        f"<b>Name:</b> {user[0] if user else 'Unknown'}",
        f"<b>User:</b> @{user[1] if user and user[1] else 'None'}",
        f"<b>ID:</b> <code>{uid}</code>",
        f"<b>Joined:</b> {white[0] if white[0] else 'Unknown'}",
        f"<b>Total Claims:</b> {total[0]}",
        f"\n📌 <b>Recent Activity:</b>"
    ]
    if not claims: res.append("<i>No claims recorded.</i>")
    for pid, ts in claims:
        res.append(f"• Game #{pid} at {ts}")
        
    await update.message.reply_text("\n".join(res), parse_mode="HTML")

async def manual_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with open(DB_PATH, 'rb') as f:
            await context.bot.send_document(chat_id=ADMIN_ID, document=f, caption="📦 Manual Database Backup")
    except Exception as e:
        await update.message.reply_text(f"Backup failed: {e}")

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("whois", whois_partner))
    app.add_handler(CommandHandler("backup", manual_backup))
    app.add_handler(CommandHandler("online", online_status)) # See below
    app.add_handler(CommandHandler("addme", lambda u,c: u.message.reply_text(f"ID: <code>{u.effective_user.id}</code>", parse_mode="HTML")))
    
    # Partner Management
    app.add_handler(CommandHandler("approve", lambda u,c: (run_query("INSERT OR IGNORE INTO whitelist (user_id, added_at) VALUES (?, ?)", (c.args[0], datetime.now().strftime("%Y-%m-%d"))), u.message.reply_text(f"✅ <code>{c.args[0]}</code> whitelisted.", parse_mode="HTML")) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("remove", lambda u,c: (run_query("DELETE FROM whitelist WHERE user_id = ?", (c.args[0],)), u.message.reply_text(f"❌ <code>{c.args[0]}</code> removed.", parse_mode="HTML")) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("list", list_partners)) # Using previous list logic
    
    # Game Management
    app.add_handler(CommandHandler("send", lambda u,c: deliver(u.effective_user.id, c.args[0], c) if run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.effective_user.id,), fetch_one=True) and c.args else None))
    app.add_handler(CommandHandler("edit", edit_tip))
    app.add_handler(CommandHandler("expire", expire_manual))
    app.add_handler(CommandHandler("delete", lambda u,c: (run_query("DELETE FROM posts WHERE post_id = ?", (c.args[0],)), u.message.reply_text(f"🗑️ Game #{c.args[0]} deleted.")) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("clearstats", lambda u,c: (run_query("DELETE FROM claims"), run_query("DELETE FROM admin_notifications"), u.message.reply_text("🧹 Stats cleared.")) if u.effective_user.id == ADMIN_ID else None))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    app.add_handler(CallbackQueryHandler(lambda u,c: callback(u,c)))
    
    loop = asyncio.get_event_loop()
    loop.create_task(backup_loop(app))
    
    app.run_polling(drop_pending_updates=True)

# (Re-use list_partners, online_status, edit_tip, expire_manual, callback from v4 build)
# Note: Ensure functions like list_partners are defined or use previous versions
async def list_partners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = run_query("SELECT w.user_id, u.full_name FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)
    if not data: return await update.message.reply_text("Whitelist empty.")
    res = ["📋 <b>PARTNERS</b> (Tap ID to copy)\n"]
    for uid, name in data: res.append(f"• <code>{uid}</code> | {name if name else 'Unknown'}")
    await update.message.reply_text("\n".join(res), parse_mode="HTML")

async def online_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = run_query("SELECT post_id FROM posts WHERE is_expired = 0", fetch_all=True)
    if not data: return await update.message.reply_text("No games online.")
    ids = ", ".join([str(r[0]) for r in data])
    await update.message.reply_text(f"🛰️ <b>Online Games:</b> {ids}", parse_mode="HTML")

async def edit_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or len(context.args) < 2: return
    run_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (" ".join(context.args[1:]), context.args[0]))
    await update.message.reply_text(f"✅ Game #{context.args[0]} updated.")

async def expire_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args: return
    pid = context.args[0]
    row = run_query("SELECT channel_msg_id FROM posts WHERE post_id = ?", (pid,), fetch_one=True)
    run_query("UPDATE posts SET is_expired = 1 WHERE post_id = ?", (pid,))
    if row and row[0]:
        try: await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=row[0], caption=f"🏆 <b>Game #{pid}</b>\n━━━━━━━━━━━━━━━\nStatus: <b>EXPIRED</b>", reply_markup=None, parse_mode="HTML")
        except: pass
    await update.message.reply_text(f"✅ Game #{pid} expired.")

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (q.from_user.id,), fetch_one=True):
        return await q.answer("Access Denied.", show_alert=True)
    err = await deliver(q.from_user.id, q.data.split("_")[1], context)
    if err: await q.answer(err, show_alert=True)
    else: await q.answer()

if __name__ == "__main__":
    main()
