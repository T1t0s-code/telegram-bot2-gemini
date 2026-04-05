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
    run_query("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    run_query("CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY, tip_text TEXT, photo_id TEXT, channel_msg_id INTEGER)")
    run_query("CREATE TABLE IF NOT EXISTS claims (user_id INTEGER, post_id INTEGER, PRIMARY KEY (user_id, post_id))")
    run_query("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_post_id', '0')")

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
            run_query("INSERT INTO posts (post_id, tip_text, photo_id, channel_msg_id) VALUES (?, ?, ?, ?)", 
                      (post_id, caption, photo_id, sent.message_id))
            run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (str(post_id),))
            await update.message.reply_text(f"✅ **Game #{post_id} published.**")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

# --- DELIVERY ---
async def deliver(user_id, game_id, context):
    post = run_query("SELECT tip_text, photo_id FROM posts WHERE post_id = ?", (game_id,), fetch_one=True)
    if not post:
        return "❌ Game not found."
    
    run_query("INSERT OR IGNORE INTO claims (user_id, post_id) VALUES (?, ?)", (user_id, game_id))
    caption = f"Game #{game_id}\n\nSelection: {post[0]}\n\ndm @R1cta"
    
    try:
        await context.bot.send_photo(chat_id=user_id, photo=post[1], caption=caption)
        return None
    except Exception as e:
        return f"❌ Delivery failed: {e}"

# --- USER COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    run_query("INSERT INTO users(user_id, full_name, username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username", (u.id, u.full_name, u.username))
    
    if u.id == ADMIN_ID:
        await update.message.reply_text("⚡ **TERMINAL ONLINE**\nUse `/admin` for the dashboard.")
    else:
        await update.message.reply_text("RICTA TERMINAL\nAccess restricted to approved partners.\n\nTo request access, use `/addme`.")

async def add_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your ID: `{update.effective_user.id}`\n\nSend this to @R1cta to request access.")

async def send_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u_id = update.effective_user.id
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (u_id,), fetch_one=True):
        await update.message.reply_text("❌ Access denied.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: `/send [number]`")
        return
    
    error = await deliver(u_id, context.args[0], context)
    if error:
        await update.message.reply_text(error)

# --- ADMIN COMMANDS ---
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

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage: `/approve ID`")
        return
    run_query("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (context.args[0],))
    await update.message.reply_text(f"✅ User `{context.args[0]}` whitelisted.")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage: `/remove ID`")
        return
    run_query("DELETE FROM whitelist WHERE user_id = ?", (context.args[0],))
    await update.message.reply_text(f"❌ User `{context.args[0]}` removed from whitelist.")

async def list_whitelisted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = run_query("SELECT w.user_id, u.full_name FROM whitelist w LEFT JOIN users u ON w.user_id = u.user_id", fetch_all=True)
    if not data:
        await update.message.reply_text("The whitelist is currently empty.")
        return
    res = "\n".join([f"• `{r[0]}` | {r[1] if r[1] else 'Unknown'}" for r in data])
    await update.message.reply_text(f"📋 **WHITELISTED PARTNERS**\n\n{res}", parse_mode="Markdown")

async def edit_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/edit ID New Text`")
        return
    run_query("UPDATE posts SET tip_text = ? WHERE post_id = ?", (" ".join(context.args[1:]), context.args[0]))
    await update.message.reply_text(f"✅ Game #{context.args[0]} updated.")

async def delete_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage: `/delete ID`")
        return
    run_query("DELETE FROM posts WHERE post_id = ?", (context.args[0],))
    await update.message.reply_text(f"🗑️ Game #{context.args[0]} deleted from database.")

async def set_id_counter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage: `/setid ID`")
        return
    run_query("UPDATE meta SET value = ? WHERE key = 'current_post_id'", (context.args[0],))
    await update.message.reply_text(f"🔢 Counter reset to: {context.args[0]}")

async def clear_all_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    run_query("DELETE FROM claims")
    await update.message.reply_text("🧹 All partner claim statistics have been cleared.")

async def audit_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = run_query("""
        SELECT u.full_name, u.user_id, COUNT(DISTINCT c.post_id), GROUP_CONCAT(DISTINCT c.post_id) 
        FROM whitelist w 
        LEFT JOIN users u ON w.user_id = u.user_id 
        LEFT JOIN claims c ON w.user_id = c.user_id 
        GROUP BY w.user_id 
        ORDER BY COUNT(DISTINCT c.post_id) DESC
    """, fetch_all=True)
    
    if not data or (len(data) == 1 and data[0][2] == 0):
        await update.message.reply_text("The audit list is clear.")
        return
    
    res = ["📋 **PARTNER AUDIT**\n━━━━━━━━━━━━━━━"]
    for name, uid, count, games in data:
        res.append(f"👤 {name if name else 'Unknown'} (`{uid}`)\n└ {count} games: {games if games else 'None'}\n")
    await update.message.reply_text("\n".join(res), parse_mode="Markdown")

async def live_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    rows = run_query("""
        SELECT c.post_id, u.full_name 
        FROM claims c 
        JOIN users u ON c.user_id = u.user_id 
        ORDER BY rowid DESC LIMIT 15
    """, fetch_all=True)
    
    if not rows:
        await update.message.reply_text("The activity report is clear.")
        return
        
    res = ["📊 **LIVE FEED**\n━━━━━━━━━━━━━━━"]
    for pid, name in rows:
        res.append(f"#{pid} | {name}")
    await update.message.reply_text("\n".join(res), parse_mode="Markdown")

# --- CALLBACK ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not run_query("SELECT 1 FROM whitelist WHERE user_id = ?", (q.from_user.id,), fetch_one=True):
        await q.answer("Access Denied.", show_alert=True)
        return
    
    game_id = q.data.split("_")[1]
    err = await deliver(q.from_user.id, game_id, context)
    if err:
        await q.answer(err, show_alert=True)
    else:
        await q.answer()

# --- MAIN ---
def main():
    db_init()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addme", add_me))
    app.add_handler(CommandHandler("send", send_game))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("approve", approve_user))
    app.add_handler(CommandHandler("remove", remove_user))
    app.add_handler(CommandHandler("list", list_whitelisted))
    app.add_handler(CommandHandler("edit", edit_game))
    app.add_handler(CommandHandler("delete", delete_game))
    app.add_handler(CommandHandler("setid", set_id_counter))
    app.add_handler(CommandHandler("clearstats", clear_all_stats))
    app.add_handler(CommandHandler("audit", audit_summary))
    app.add_handler(CommandHandler("report", live_report))
    
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_broadcast))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
