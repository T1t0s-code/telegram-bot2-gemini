import os
import sqlite3
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

# --- BROADCASTER ---
async def handle_photo_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not update.message.photo: return
    
    photo_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "No Selection").strip()
    
    max_id_row = run_query("SELECT MAX(post_id) FROM posts", fetch_one=True)
    post_id = (max_id_row[0] or 0) + 1
    
    if CHANNEL_ID:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Unlock Selection", callback_data=f"GET_{post_id}")]])
        msg = f"🏆 **Game #{post_id}**\n━━━━━━━━━━━━━━━\nStatus: **ACTIVE**\nGet the game at @Ricta_Terminal_bot\nFor access dm @R1cta"
        
        try:
            sent = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_id, caption=msg, reply_markup=keyboard, parse_mode="Markdown")
            run_query("INSERT INTO posts (post_id, tip_text, photo_id, channel_msg_id, is_expired) VALUES (?, ?, ?, ?, 0)", 
                      (post_id, caption, photo_id, sent.message_id))
            await update.message.reply_text(f"✅ **Game #{post_id} published successfully.**")
        except Exception as e:
            await update.message.reply_text(f"❌ Broadcast Error: {e}")

# --- DELIVERY ---
async def deliver(user_id, game_id, context):
    row = run_query("SELECT tip_text, photo_id, is_expired FROM posts WHERE post_id = ?", (game_id,), fetch_one=True)
    if not row: return "❌ Game not found."
    if row[2] == 1: return f"❌ Game #{game_id} has expired."
    
    run_query("INSERT OR IGNORE INTO claims (user_id, post_id) VALUES (?, ?)", (user_id, game_id))
    caption = f"Game #{game_id}\n\nSelection: {row[0]}\n\ndm @R1cta"
    await context.bot.send_photo(chat_id=user_id, photo=row[1], caption=caption)
    return None

# --- USER COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    
    if u.id == ADMIN_ID:
        await update.message.reply_text("⚡ **TERMINAL ONLINE (ADMIN)**\nUse `/admin` for control.")
    else:
        await update.message.reply_text("RICTA TERMINAL\nAccess restricted to approved partners.\n\nTo request access, use `/addme`.")

async def online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u_id = update.effective_user.id
    if u_id == ADMIN_ID:
        data = run_query("SELECT post_id, (SELECT COUNT(*) FROM claims WHERE claims.post_id = posts.post_id) FROM posts WHERE is_expired = 0", fetch_all=True)
        if not data:
            return await update.message.reply_text("No games are currently online.")
        res = ["🛰️ **ACTIVE GAMES (ADMIN VIEW)**\n━━━━━━━━━━━━━━━"]
        for pid, count in data:
            res.append(f"Game #{pid} | Claims: {count}")
        await update.message.reply_text("\n".join(res), parse_mode="Markdown")
    else:
        # User View
        data = run_query("SELECT post_id FROM posts WHERE is_expired = 0", fetch_all=True)
        if not data:
            return await update.message.reply_text("No games are currently online.")
        ids = ", ".join([str(r[0]) for r in data])
        await update.message.reply_text(f"🛰️ **Online Games:** {ids}")

# --- ADMIN SECURE COMMANDS ---
async def expire_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args: return await update.message.reply_text("Usage: `/expire ID`")
    
    p_id = context.args[0]
    row = run_query("SELECT channel_msg_id FROM posts WHERE post_id = ?", (p_id,), fetch_one=True)
    
    if not row:
        return await update.message.reply_text(f"❌ Game #{p_id} not found.")

    run_query("UPDATE posts SET is_expired = 1 WHERE post_id = ?", (p_id,))
    
    if CHANNEL_ID and row[0]:
        try:
            new_text = f"🏆 **Game #{p_id}**\n━━━━━━━━━━━━━━━\nStatus: **EXPIRED**\nFor access dm @R1cta"
            await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=row[0], caption=new_text, reply_markup=None, parse_mode="Markdown")
        except: pass
    
    await update.message.reply_text(f"✅ Game #{p_id} expired and Channel post updated.")

async def delete_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args: return await update.message.reply_text("Usage: `/delete ID`")
    
    p_id = context.args[0]
    exists = run_query("SELECT 1 FROM posts WHERE post_id = ?", (p_id,), fetch_one=True)
    if not exists: return await update.message.reply_text(f"❌ Game #{p_id} not found.")

    run_query("DELETE FROM posts WHERE post_id = ?", (p_id,))
    run_query("DELETE FROM claims WHERE post_id = ?", (p_id,))
    await update.message.reply_text(f"🗑️ Game #{p_id} completely removed from database.")

async def edit_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if len(context.args) < 2: return await update.message.reply_text("Usage: `/edit ID NEW TEXT`")
    
    p_id, new_text = context.args[0], " ".join(context.args[1:])
    exists = run_query("SELECT 1 FROM posts WHERE post_id = ?", (p_id,), fetch_one=True)
    if not exists: return await update.message.reply_text(f"❌ Game #{p_id} not found.")

    run_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (new_text, p_id))
    await update.message.reply_text(f"📝 Game #{p_id} text updated to: {new_text}")

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args: return await update.message.reply_text("Usage: `/approve ID`")
    
    run_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (context.args[0],))
    await update.message.reply_text(f"✅ User `{context.args[0]}` is now a partner.")

async def audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = run_query("SELECT u.full_name, u.user_id, COUNT(c.post_id) FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id LEFT JOIN claims c ON w.user_id = c.user_id GROUP BY w.user_id", fetch_all=True)
    if not data: return await update.message.reply_text("No partners to audit.")
    res = ["📋 **AUDIT SUMMARY**\n━━━━━━━━━━━━━━━"]
    for n, uid, count in data:
        res.append(f"👤 {n if n else 'Unknown'} (`{uid}`): {count} claims")
    await update.message.reply_text("\n".join(res), parse_mode="Markdown")

async def clear_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    run_query("DELETE FROM claims")
    await update.message.reply_text("🧹 All claim statistics have been cleared.")

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Core
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("online", online))
    app.add_handler(CommandHandler("addme", lambda u,c: u.message.reply_text(f"ID: `{u.effective_user.id}`\nSend to @R1cta.")))
    
    # User-Whitelisted
    app.add_handler(CommandHandler("send", lambda u,c: deliver(u.effective_user.id, c.args[0], c) if run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u.effective_user.id,), fetch_one=True) and c.args else None))

    # Admin Only
    app.add_handler(CommandHandler("admin", lambda u,c: u.message.reply_text("⚙️ **ADMIN PANEL**\n`/approve ID` | `/remove ID` | `/list`\n`/edit ID TEXT` | `/delete ID` | `/expire ID`\n`/report` | `/audit` | `/clearstats`") if u.effective_user.id == ADMIN_ID else None))
    app.add_handler(CommandHandler("approve", approve_user))
    app.add_handler(CommandHandler("expire", expire_game))
    app.add_handler(CommandHandler("delete", delete_game))
    app.add_handler(CommandHandler("edit", edit_game))
    app.add_handler(CommandHandler("audit", audit))
    app.add_handler(CommandHandler("clearstats", clear_stats))
    app.add_handler(CommandHandler("report", lambda u,c: u.message.reply_text("\n".join([f"#{r[0]} | {r[1]}" for r in run_query("SELECT c.post_id, u.full_name FROM claims c JOIN users u ON c.user_id = u.user_id ORDER BY c.rowid DESC LIMIT 15", fetch_all=True)]) or "No activity.") if u.effective_user.id == ADMIN_ID else None))
    app.add_handler(CommandHandler("list", lambda u,c: u.message.reply_text("\n".join([f"• `{r[0]}` | {r[1]}" for r in run_query("SELECT w.user_id, u.full_name FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)]) or "Empty") if u.effective_user.id == ADMIN_ID else None))

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
