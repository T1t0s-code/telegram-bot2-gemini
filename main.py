import os
import sqlite3
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
ADMIN_ID = 5024732090 

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
    run_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, channel_msg_id INTEGER, is_expired INTEGER DEFAULT 0)")
    run_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, PRIMARY KEY (user_id, post_id))")
    # New table to track admin notification messages for editing (counts)
    run_query("CREATE TABLE IF NOT EXISTS admin_notifications (user_id INTEGER, post_id INTEGER, admin_msg_id INTEGER, count INTEGER DEFAULT 1, PRIMARY KEY (user_id, post_id))")

# --- BROADCASTER ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message.photo: return
    
    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()
    
    max_id_row = run_query("SELECT MAX(post_id) FROM posts", fetch_one=True)
    post_id = (max_id_row[0] or 0) + 1
    
    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Unlock Selection", callback_data=f"GET_{post_id}")]])
        msg = f"🏆 <b>Game #{post_id}</b>\n━━━━━━━━━━━━━━━\nStatus: <b>ACTIVE</b>\nGet the game at @Ricta_Terminal_bot\nFor access dm @R1cta"
        
        try:
            sent = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=msg, reply_markup=keyboard, parse_mode="HTML")
            run_query("INSERT INTO posts (post_id, tip_text, photo_id, channel_msg_id, is_expired) VALUES (?, ?, ?, ?, 0)", 
                      (post_id, caption, photo_id, sent.message_id))
            await update.message.reply_text(f"✅ <b>Game #{post_id} published successfully.</b>", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Broadcast Error: {e}")

# --- DELIVERY & NOTIFICATION ---
async def deliver(user_id, game_id, context: ContextTypes.DEFAULT_TYPE):
    row = run_query("SELECT tip_text, photo_id, is_expired FROM posts WHERE post_id = ?", (game_id,), fetch_one=True)
    if not row: return "❌ Game not found."
    if row[2] == 1: return f"❌ Game #{game_id} has expired."
    
    # 1. Deliver to User
    run_query("INSERT OR IGNORE INTO claims (user_id, post_id) VALUES (?, ?)", (user_id, game_id))
    caption = f"Game #{game_id}\n\nSelection: {row[0]}\n\ndm @R1cta"
    await context.bot.send_photo(chat_id=user_id, photo=row[1], caption=caption)

    # 2. Notify Admin (with count/edit logic)
    user_row = run_query("SELECT full_name FROM users WHERE user_id = ?", (user_id,), fetch_one=True)
    user_name = user_row[0] if user_row else "Unknown"
    
    notif = run_query("SELECT admin_msg_id, count FROM admin_notifications WHERE user_id = ? AND post_id = ?", (user_id, game_id), fetch_one=True)
    
    if notif:
        msg_id, current_count = notif
        new_count = current_count + 1
        text = f"👤 <b>{user_name}</b> (<code>{user_id}</code>)\n📥 Got <b>Game #{game_id}</b>'s line ({new_count})"
        try:
            await context.bot.edit_message_text(chat_id=ADMIN_ID, message_id=msg_id, text=text, parse_mode="HTML")
            run_query("UPDATE admin_notifications SET count = ? WHERE user_id = ? AND post_id = ?", (new_count, user_id, game_id))
        except:
            # If edit fails (e.g. message deleted), send new
            sent = await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
            run_query("UPDATE admin_notifications SET admin_msg_id = ?, count = ? WHERE user_id = ? AND post_id = ?", (sent.message_id, new_count, user_id, game_id))
    else:
        text = f"👤 <b>{user_name}</b> (<code>{user_id}</code>)\n📥 Got <b>Game #{game_id}</b>'s line"
        sent = await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
        run_query("INSERT INTO admin_notifications (user_id, post_id, admin_msg_id, count) VALUES (?, ?, ?, 1)", (user_id, game_id, sent.message_id))
    
    return None

# --- ADMIN FUNCTIONS ---
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    menu = (
        "⚙️ <b>TERMINAL DASHBOARD</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👥 <b>PARTNERS</b>\n"
        "• /list — <i>View & Copy IDs</i>\n"
        "• /audit — <i>Claim Summary</i>\n"
        "• /approve <code>ID</code>\n"
        "• /remove <code>ID</code>\n\n"
        "🎮 <b>GAMES</b>\n"
        "• /online — <i>Active status</i>\n"
        "• /report — <i>Live Activity</i>\n"
        "• /edit <code>ID TEXT</code>\n"
        "• /expire <code>ID</code>\n"
        "• /delete <code>ID</code>\n\n"
        "🧹 <b>SYSTEM</b>\n"
        "• /clearstats — <i>Reset all activity</i>"
    )
    await update.message.reply_text(menu, parse_mode="HTML")

async def list_partners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = run_query("SELECT w.user_id, u.full_name FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)
    if not data:
        return await update.message.reply_text("Whitelist is clear.")
    res = ["📋 <b>WHITELISTED PARTNERS</b>\n<i>(Tap ID to copy)</i>\n"]
    for uid, name in data:
        res.append(f"• <code>{uid}</code> | {name if name else 'Unknown'}")
    await update.message.reply_text("\n".join(res), parse_mode="HTML")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        return await update.message.reply_text("Usage: /remove <code>ID</code>", parse_mode="HTML")
    uid = context.args[0]
    run_query("DELETE FROM whitelist WHERE user_id = ?", (uid,))
    await update.message.reply_text(f"✅ User <code>{uid}</code> has been removed from whitelist.", parse_mode="HTML")

async def online_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u_id = update.effective_user.id
    data = run_query("SELECT post_id FROM posts WHERE is_expired = 0", fetch_all=True)
    if not data:
        return await update.message.reply_text("No games are currently online.")
    
    if u_id == ADMIN_ID:
        res = ["🛰️ <b>ACTIVE GAMES (ADMIN)</b>\n━━━━━━━━━━━━━━━"]
        for (pid,) in data:
            count_row = run_query("SELECT COUNT(*) FROM claims WHERE post_id = ?", (pid,), fetch_one=True)
            res.append(f"• Game #{pid} | Total Claims: {count_row[0]}")
        await update.message.reply_text("\n".join(res), parse_mode="HTML")
    else:
        ids = ", ".join([str(r[0]) for r in data])
        await update.message.reply_text(f"🛰️ <b>Online Games:</b> {ids}", parse_mode="HTML")

async def expire_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args: return await update.message.reply_text("Usage: /expire <code>ID</code>", parse_mode="HTML")
    pid = context.args[0]
    row = run_query("SELECT channel_msg_id FROM posts WHERE post_id = ?", (pid,), fetch_one=True)
    if not row: return await update.message.reply_text(f"❌ Game #{pid} not found.")

    run_query("UPDATE posts SET is_expired = 1 WHERE post_id = ?", (pid,))
    if CHANNEL_ID and row[0]:
        try:
            txt = f"🏆 <b>Game #{pid}</b>\n━━━━━━━━━━━━━━━\nStatus: <b>EXPIRED</b>\nFor access dm @R1cta"
            await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=row[0], caption=txt, reply_markup=None, parse_mode="HTML")
        except: pass
    await update.message.reply_text(f"✅ Game #{pid} expired and Channel updated.")

async def edit_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if len(context.args) < 2: return await update.message.reply_text("Usage: /edit <code>ID TEXT</code>", parse_mode="HTML")
    pid, text = context.args[0], " ".join(context.args[1:])
    exists = run_query("SELECT 1 FROM posts WHERE post_id = ?", (pid,), fetch_one=True)
    if not exists: return await update.message.reply_text(f"❌ Game #{pid} not found.")
    
    run_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (text, pid))
    await update.message.reply_text(f"✅ Game #{pid} updated.\n<b>New Text:</b> {text}", parse_mode="HTML")

# --- CORE HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    if u.id == ADMIN_ID:
        await update.message.reply_text("⚡ <b>TERMINAL ONLINE</b>\nUse /admin to open the dashboard.", parse_mode="HTML")
    else:
        await update.message.reply_text("RICTA TERMINAL\nApproved partners only.\nUse /addme to get your ID.")

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u_id = update.effective_user.id
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u_id,), fetch_one=True):
        return await update.message.reply_text("❌ Access Denied.")
    if not context.args: return
    err = await deliver(u_id, context.args[0], context)
    if err: await update.message.reply_text(err)

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # User Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("online", online_status))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("addme", lambda u,c: u.message.reply_text(f"ID: <code>{u.effective_user.id}</code>", parse_mode="HTML")))

    # Admin Commands (Explicit Handlers)
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CommandHandler("approve", lambda u,c: (run_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (c.args[0],)), u.message.reply_text(f"✅ <code>{c.args[0]}</code> whitelisted.", parse_mode="HTML")) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("remove", remove_user)) # Fixed: Added explicit handler
    app.add_handler(CommandHandler("list", list_partners))
    app.add_handler(CommandHandler("audit", lambda u,c: u.message.reply_text("📋 <b>AUDIT</b>\n" + "\n".join([f"• {r[0]} (<code>{r[1]}</code>): {r[2]} claims" for r in run_query("SELECT u.full_name, w.user_id, COUNT(c.post_id) FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id LEFT JOIN claims c ON w.user_id = c.user_id GROUP BY w.user_id", fetch_all=True)]), parse_mode="HTML") if u.effective_user.id == ADMIN_ID else None))
    app.add_handler(CommandHandler("report", lambda u,c: u.message.reply_text("📊 <b>REPORT</b>\n" + "\n".join([f"#{r[0]} | {r[1]}" for r in run_query("SELECT c.post_id, u.full_name FROM claims c JOIN users u ON c.user_id = u.user_id ORDER BY c.rowid DESC LIMIT 15", fetch_all=True)]) or "No activity.", parse_mode="HTML") if u.effective_user.id == ADMIN_ID else None))
    app.add_handler(CommandHandler("expire", expire_manual))
    app.add_handler(CommandHandler("edit", edit_tip))
    app.add_handler(CommandHandler("delete", lambda u,c: (run_query("DELETE FROM posts WHERE post_id = ?", (c.args[0],)), u.message.reply_text(f"🗑️ Game #{c.args[0]} deleted.")) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("clearstats", lambda u,c: (run_query("DELETE FROM claims"), run_query("DELETE FROM admin_notifications"), u.message.reply_text("🧹 Stats cleared.")) if u.effective_user.id == ADMIN_ID else None))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    app.add_handler(CallbackQueryHandler(lambda u,c: callback(u,c)))
    
    app.run_polling(drop_pending_updates=True)

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (q.from_user.id,), fetch_one=True):
        return await q.answer("Access Denied.", show_alert=True)
    err = await deliver(q.from_user.id, q.data.split("_")[1], context)
    if err: await q.answer(err, show_alert=True)
    else: await q.answer()

if __name__ == "__main__":
    main()
