import os
import sqlite3
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
ADMIN_ID = 5024732090 

# Ensure this is EXACTLY your bot handle without the '@'
BOT_USERNAME = "RictaTerminalbot" 

# --- DB ENGINE ---
def run_query(query, params=(), fetch_one=False, fetch_all=False):
    with sqlite3.connect(DB_PATH, timeout=20) as con:
        cur = con.cursor()
        cur.execute(query, params)
        if fetch_one: return cur.fetchone()
        if fetch_all: return cur.fetchall()
        con.commit()

def db_init():
    run_query("CREATE TABLE IF NOT EXISTS whitelist (user_id INTEGER PRIMARY KEY, added_at TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, channel_msg_id INTEGER, is_expired INTEGER DEFAULT 0)")
    run_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, claimed_at TEXT, PRIMARY KEY (user_id, post_id))")
    run_query("CREATE TABLE IF NOT EXISTS admin_notifications (user_id INTEGER, post_id INTEGER, admin_msg_id INTEGER, count INTEGER DEFAULT 1, PRIMARY KEY (user_id, post_id))")

# --- UTILS ---
async def notify_admin_security(u, context, action):
    """Sends a security alert to admin when unwhitelisted users try to access."""
    text = (
        f"🚫 <b>INTRUDER ALERT</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>User:</b> {u.full_name}\n"
        f"<b>ID:</b> <code>{u.id}</code>\n"
        f"<b>Handle:</b> @{u.username if u.username else 'None'}\n"
        f"<b>Action:</b> {action}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")

# --- DELIVERY ---
async def deliver(user_id, game_id, context):
    row = run_query("SELECT tip_text, photo_id, is_expired FROM posts WHERE post_id = ?", (game_id,), fetch_one=True)
    if not row: return "❌ Game not found."
    if row[2] == 1: return f"❌ Game #{game_id} has expired."
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_query("INSERT OR IGNORE INTO claims (user_id, post_id, claimed_at) VALUES (?, ?, ?)", (user_id, game_id, now))
    
    caption = f"🏆 Game #{game_id}\n\nSelection: {row[0]}\n\ndm @R1cta"
    await context.bot.send_photo(chat_id=user_id, photo=row[1], caption=caption)

    # Admin Claim Alert (Edit previous message if exists)
    user_row = run_query("SELECT full_name FROM users WHERE user_id = ?", (user_id,), fetch_one=True)
    user_name = user_row[0] if user_row else "Unknown"
    notif = run_query("SELECT admin_msg_id, count FROM admin_notifications WHERE user_id = ? AND post_id = ?", (user_id, game_id), fetch_one=True)
    
    if notif:
        msg_id, current_count = notif
        new_count = current_count + 1
        txt = f"👤 <b>{user_name}</b> (<code>{user_id}</code>)\n📥 Got <b>Game #{game_id}</b>'s line ({new_count})"
        try:
            await context.bot.edit_message_text(chat_id=ADMIN_ID, message_id=msg_id, text=txt, parse_mode="HTML")
            run_query("UPDATE admin_notifications SET count = ? WHERE user_id = ? AND post_id = ?", (new_count, user_id, game_id))
        except:
            sent = await context.bot.send_message(chat_id=ADMIN_ID, text=txt, parse_mode="HTML")
            run_query("UPDATE admin_notifications SET admin_msg_id = ?, count = ? WHERE user_id = ? AND post_id = ?", (sent.message_id, new_count, user_id, game_id))
    else:
        txt = f"👤 <b>{user_name}</b> (<code>{user_id}</code>)\n📥 Got <b>Game #{game_id}</b>'s line"
        sent = await context.bot.send_message(chat_id=ADMIN_ID, text=txt, parse_mode="HTML")
        run_query("INSERT INTO admin_notifications (user_id, post_id, admin_msg_id, count) VALUES (?, ?, ?, 1)", (user_id, game_id, sent.message_id))
    return None

# --- CORE HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    
    if context.args and context.args[0].startswith("game_"):
        is_white = run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.id,), fetch_one=True)
        game_id = context.args[0].split("_")[1]
        
        if not is_white:
            await notify_admin_security(u, context, f"Tried to Quick-Claim Game #{game_id}")
            return await update.message.reply_text("❌ Access Denied. Contact @R1cta.")
        
        err = await deliver(u.id, game_id, context)
        if err: await update.message.reply_text(err)
        return

    if u.id == ADMIN_ID:
        await update.message.reply_text("⚡ <b>TERMINAL ONLINE</b>\nUse /admin for the Control Center.", parse_mode="HTML")
    else:
        await update.message.reply_text("RICTA TERMINAL\nAccess Restricted.\nUse /addme to get your ID.")

async def profile_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    is_white = run_query("SELECT added_at FROM whitelist WHERE user_id = ?", (u.id,), fetch_one=True)
    
    if not is_white:
        return await update.message.reply_text("❌ You are not a verified partner.")
    
    total = run_query("SELECT COUNT(*) FROM claims WHERE user_id = ?", (u.id,), fetch_one=True)[0]
    
    card = (
        f"💳 <b>PARTNER PROFILE</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>Name:</b> {u.full_name}\n"
        f"<b>Status:</b> ✅ Verified Partner\n"
        f"<b>Partner ID:</b> <code>{u.id}</code>\n"
        f"<b>Joined:</b> {is_white[0]}\n"
        f"<b>Total Claims:</b> {total}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<i>Terminal v6.0 Online</i>"
    )
    await update.message.reply_text(card, parse_mode="HTML")

# --- INTERACTIVE ADMIN PANEL ---
async def admin_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    kb = [
        [InlineKeyboardButton("👥 Manage Partners", callback_data="ADM_PARTNERS")],
        [InlineKeyboardButton("🎮 Manage Games", callback_data="ADM_GAMES")],
        [InlineKeyboardButton("💾 System & Stats", callback_data="ADM_SYSTEM")]
    ]
    await update.message.reply_text("🎮 <b>TERMINAL CONTROL CENTER</b>\nSelect a category:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID: return await query.answer("Unauthorized.")
    
    data = query.data

    # --- PARTNER SUBMENU ---
    if data == "ADM_PARTNERS":
        kb = [
            [InlineKeyboardButton("📋 List All", callback_data="ADM_LIST_P"), InlineKeyboardButton("📊 Audit", callback_data="ADM_AUDIT")],
            [InlineKeyboardButton("⬅️ Back", callback_data="ADM_MAIN")]
        ]
        await query.edit_message_text("👥 <b>PARTNER MANAGEMENT</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data == "ADM_LIST_P":
        rows = run_query("SELECT w.user_id, u.full_name FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)
        text = "📋 <b>ACTIVE PARTNERS</b>\n\n"
        if not rows: text += "<i>No partners whitelisted yet.</i>"
        else:
            for uid, name in rows: text += f"• <code>{uid}</code> | {name if name else 'Unknown'}\n"
        kb = [[InlineKeyboardButton("⬅️ Back", callback_data="ADM_PARTNERS")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    # --- GAME SUBMENU ---
    elif data == "ADM_GAMES":
        active_games = run_query("SELECT post_id FROM posts WHERE is_expired = 0", fetch_all=True)
        if not active_games:
            kb = [[InlineKeyboardButton("⬅️ Back", callback_data="ADM_MAIN")]]
            return await query.edit_message_text("🛰️ <b>No games currently online.</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        
        buttons = []
        for (pid,) in active_games:
            buttons.append(InlineKeyboardButton(f"Game #{pid}", callback_data=f"GAME_MANAGE_{pid}"))
        
        # Grid layout (2 per row)
        kb = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="ADM_MAIN")])
        await query.edit_message_text("🎮 <b>ACTIVE GAMES</b>\nTap a game to manage it:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data.startswith("GAME_MANAGE_"):
        pid = data.split("_")[2]
        kb = [
            [InlineKeyboardButton("⏳ Expire", callback_data=f"GAME_EXP_{pid}"), InlineKeyboardButton("🗑️ Delete", callback_data=f"GAME_DEL_{pid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="ADM_GAMES")]
        ]
        await query.edit_message_text(f"⚙️ <b>MANAGING GAME #{pid}</b>\nChoose an action:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data.startswith("GAME_EXP_"):
        pid = data.split("_")[2]
        run_query("UPDATE posts SET is_expired = 1 WHERE post_id = ?", (pid,))
        # Attempt to edit channel
        row = run_query("SELECT channel_msg_id FROM posts WHERE post_id = ?", (pid,), fetch_one=True)
        if row and row[0]:
            try: await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=row[0], caption=f"🏆 <b>Game #{pid}</b>\n━━━━━━━━━━━━━━━\nStatus: <b>EXPIRED</b>", parse_mode="HTML")
            except: pass
        await query.answer(f"Game #{pid} Expired!")
        await admin_callback(update, context) # Refresh menu

    elif data.startswith("GAME_DEL_"):
        pid = data.split("_")[2]
        run_query("DELETE FROM posts WHERE post_id = ?", (pid,))
        await query.answer(f"Game #{pid} Deleted!")
        await admin_callback(update, context)

    # --- SYSTEM SUBMENU ---
    elif data == "ADM_SYSTEM":
        kb = [
            [InlineKeyboardButton("📦 Manual Backup", callback_data="ADM_BACKUP")],
            [InlineKeyboardButton("🧹 Clear Stats", callback_data="ADM_CLEAR")],
            [InlineKeyboardButton("⬅️ Back", callback_data="ADM_MAIN")]
        ]
        await query.edit_message_text("💾 <b>SYSTEM TOOLS</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data == "ADM_BACKUP":
        try:
            with open(DB_PATH, 'rb') as f:
                await context.bot.send_document(chat_id=ADMIN_ID, document=f, caption="Manual Backup File")
            await query.answer("Backup sent to your DM!")
        except: await query.answer("Backup failed.")

    elif data == "ADM_MAIN":
        kb = [
            [InlineKeyboardButton("👥 Manage Partners", callback_data="ADM_PARTNERS")],
            [InlineKeyboardButton("🎮 Manage Games", callback_data="ADM_GAMES")],
            [InlineKeyboardButton("💾 System & Stats", callback_data="ADM_SYSTEM")]
        ]
        await query.edit_message_text("🎮 <b>TERMINAL CONTROL CENTER</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

# --- BROADCASTER ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message.photo: return
    
    photo_id = update.message.photo[-1].file_id
    caption_input = (update.message.caption or "No Selection").strip()
    
    max_id_row = run_query("SELECT MAX(post_id) FROM posts", fetch_one=True)
    post_id = (max_id_row[0] or 0) + 1
    
    quick_link = f"https://t.me/{BOT_USERNAME}?start=game_{post_id}"
    
    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Quick Claim", url=quick_link)],
            [InlineKeyboardButton("Unlock Selection", callback_data=f"GET_{post_id}")]
        ])
        
        msg = (
            f"🏆 <b>Game #{post_id}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Status: <b>ACTIVE</b>\n\n"
            f"Get the game at @{BOT_USERNAME}\n"
            f"For access dm @R1cta"
        )
        
        try:
            sent = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=msg, reply_markup=keyboard, parse_mode="HTML")
            run_query("INSERT INTO posts (post_id, tip_text, photo_id, channel_msg_id, is_expired) VALUES (?, ?, ?, ?, 0)", 
                      (post_id, caption_input, photo_id, sent.message_id))
            await update.message.reply_text(f"✅ <b>Game #{post_id} Published.</b>", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Broadcast Error: {e}")

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # User Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("me", profile_me))
    app.add_handler(CommandHandler("addme", lambda u,c: u.message.reply_text(f"ID: <code>{u.effective_user.id}</code>", parse_mode="HTML")))
    app.add_handler(CommandHandler("send", lambda u,c: deliver(u.effective_user.id, c.args[0], c) if run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.effective_user.id,), fetch_one=True) and c.args else None))

    # Admin Control Center
    app.add_handler(CommandHandler("admin", admin_main_menu))
    app.add_handler(CommandHandler("approve", lambda u,c: (run_query("INSERT OR IGNORE INTO whitelist (user_id, added_at) VALUES (?, ?)", (c.args[0], datetime.now().strftime("%Y-%m-%d"))), u.message.reply_text("✅ Approved.")) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("remove", lambda u,c: (run_query("DELETE FROM whitelist WHERE user_id = ?", (c.args[0],)), u.message.reply_text("❌ User Removed.")) if u.effective_user.id == ADMIN_ID and c.args else None))
    app.add_handler(CommandHandler("edit", lambda u,c: (run_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (" ".join(c.args[1:]), c.args[0])), u.message.reply_text("✅ Updated.")) if u.effective_user.id == ADMIN_ID and len(c.args) > 1 else None))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    
    # Unified Callback Handler
    app.add_handler(CallbackQueryHandler(lambda u,c: admin_callback(u,c) if u.callback_query.data.startswith("ADM_") or u.callback_query.data.startswith("GAME_") else callback_user(u,c)))

    app.run_polling(drop_pending_updates=True)

async def callback_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (q.from_user.id,), fetch_one=True):
        await notify_admin_security(q.from_user, context, "Clicked button in Channel")
        return await q.answer("Access Denied.", show_alert=True)
    
    err = await deliver(q.from_user.id, q.data.split("_")[1], context)
    if err: await q.answer(err, show_alert=True)
    else: await q.answer()

if __name__ == "__main__":
    main()
