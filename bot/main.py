"""
StreetBoss — Telegram Bot (v2)
All in English. All payments route to owner wallet.
Withdraw requests require admin approval.
"""

import logging
import asyncio
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────
import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN        = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBAPP_URL       = os.getenv("WEBAPP_URL", "https://your-domain.com")
ADMIN_PANEL_URL  = os.getenv("ADMIN_PANEL_URL", "https://your-domain.com/admin")
OWNER_WALLET     = "UQCvimmPAInaVHWUGPMfLu06m7k62bZ27_Asyob6w9_N7Ykw"
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "0"))  # Your Telegram user ID
DB_PATH          = "streetboss.db"


# ── DB INIT ───────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS players (
        user_id         INTEGER PRIMARY KEY,
        username        TEXT NOT NULL,
        ton_address     TEXT,
        ton_balance     REAL DEFAULT 0.0,
        accrued_ton     REAL DEFAULT 0.0,
        level           INTEGER DEFAULT 1,
        xp              INTEGER DEFAULT 0,
        pvp_rank        INTEGER DEFAULT 999,
        pvp_wins        INTEGER DEFAULT 0,
        pvp_losses      INTEGER DEFAULT 0,
        referrals       INTEGER DEFAULT 0,
        referral_earnings REAL DEFAULT 0.0,
        referred_by     INTEGER,
        last_collect    TEXT,
        last_active     TEXT,
        created_at      TEXT,
        is_banned       INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS transactions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        label       TEXT,
        amount      REAL,
        owner_fee   REAL DEFAULT 0,
        tx_hash     TEXT,
        created_at  TEXT
    );
    CREATE TABLE IF NOT EXISTS withdraw_requests (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        username    TEXT,
        amount      REAL,
        wallet_addr TEXT,
        status      TEXT DEFAULT 'pending',
        requested_at TEXT,
        resolved_at  TEXT,
        tx_hash      TEXT
    );
    CREATE TABLE IF NOT EXISTS player_businesses (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        type        TEXT,
        level       INTEGER DEFAULT 1,
        last_earn   TEXT,
        UNIQUE(user_id, type)
    );
    CREATE TABLE IF NOT EXISTS player_nfts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        nft_type    TEXT,
        name        TEXT,
        power       INTEGER,
        rarity      TEXT,
        purchased_at TEXT
    );
    """)
    conn.commit()
    conn.close()

init_db()


# ── HELPERS ───────────────────────────────────────────────────────────────────
def get_player(user_id: int, username: str = "player") -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    row = c.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        now = datetime.utcnow().isoformat()
        c.execute("""INSERT INTO players (user_id, username, ton_balance, level, pvp_rank, created_at, last_active, last_collect)
                     VALUES (?, ?, 0.0, 1, 999, ?, ?, ?)""", (user_id, username, now, now, now))
        conn.commit()
        row = c.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row)

def add_tx(user_id: int, label: str, amount: float, fee: float = 0):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO transactions (user_id, label, amount, owner_fee, created_at) VALUES (?, ?, ?, ?, ?)",
                 (user_id, label, amount, fee, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_TELEGRAM_ID


# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    player = get_player(user.id, user.username or user.first_name)

    # Handle referral
    if ctx.args and ctx.args[0].startswith("ref_"):
        ref_id = int(ctx.args[0].replace("ref_", ""))
        if ref_id != user.id:
            conn = sqlite3.connect(DB_PATH)
            existing = conn.execute("SELECT referred_by FROM players WHERE user_id = ?", (user.id,)).fetchone()
            if existing and not existing[0]:
                conn.execute("UPDATE players SET referred_by = ? WHERE user_id = ?", (ref_id, user.id))
                conn.execute("UPDATE players SET referrals = referrals + 1 WHERE user_id = ?", (ref_id,))
                conn.execute("UPDATE players SET ton_balance = ton_balance + 0.05, referral_earnings = referral_earnings + 0.05 WHERE user_id = ?", (ref_id,))
                conn.commit()
                try:
                    await ctx.bot.send_message(chat_id=ref_id,
                        text=f"👊 *New referral!*\n\n@{user.username} joined via your link.\n+*0.05 TON* added to your balance!",
                        parse_mode="Markdown")
                except: pass
            conn.close()

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏙 Play StreetBoss", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])

    await update.message.reply_text(
        f"👊 *Welcome to StreetBoss, {user.first_name}!*\n\n"
        f"Build your street empire. Open businesses, hire NFT bodyguards, "
        f"raid rivals and earn real *TON* — sent directly to your wallet.\n\n"
        f"💼 Businesses: *{player.get('business_count', 0)}*\n"
        f"💰 Balance: *{player['ton_balance']:.4f} TON*\n"
        f"🏆 Rank: *#{player['pvp_rank']}*\n\n"
        f"Tap below to enter the game:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    try:
        await ctx.bot.set_chat_menu_button(
            chat_id=update.effective_chat.id,
            menu_button=MenuButtonWebApp(text="🏙 StreetBoss", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except: pass


# ── /wallet ───────────────────────────────────────────────────────────────────
async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    player = get_player(update.effective_user.id)
    addr = player.get("ton_address")
    text = (
        f"💳 *Your TON Wallet*\n\n"
        f"Address: `{addr}`\n"
        f"Balance: *{player['ton_balance']:.4f} TON*\n\n"
        f"All purchases and earnings are processed through this wallet.\n"
        f"All payments go to the game owner wallet:\n"
        f"`{OWNER_WALLET[:16]}...`"
    ) if addr else (
        f"💳 *Connect TON Wallet*\n\n"
        f"Connect your TON wallet to start playing.\n"
        f"All game payments route to the owner's wallet:\n`{OWNER_WALLET[:16]}...`"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔗 Connect Wallet", web_app=WebAppInfo(url=f"{WEBAPP_URL}?page=wallet"))
    ]])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


# ── /collect ──────────────────────────────────────────────────────────────────
async def cmd_collect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT accrued_ton, ton_balance FROM players WHERE user_id = ?", (user_id,)).fetchone()
    if not row or row[0] <= 0:
        await update.message.reply_text("⏳ Nothing to collect yet. Your businesses are earning — check back later!")
        conn.close()
        return
    accrued = row[0]
    conn.execute("UPDATE players SET ton_balance = ton_balance + ?, accrued_ton = 0, last_collect = ? WHERE user_id = ?",
                 (accrued, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()
    add_tx(user_id, "Passive income collected", accrued)
    await update.message.reply_text(
        f"✅ *+{accrued:.4f} TON collected!*\n\n"
        f"New balance: *{(row[1]+accrued):.4f} TON*\n"
        f"Your earnings are recorded on-chain.",
        parse_mode="Markdown"
    )


# ── /attack @username ─────────────────────────────────────────────────────────
async def cmd_attack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /attack @username"); return
    import random
    target_username = ctx.args[0].lstrip("@")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    target = conn.execute("SELECT * FROM players WHERE username = ?", (target_username,)).fetchone()
    conn.close()
    if not target:
        await update.message.reply_text(f"Player @{target_username} not found."); return
    target = dict(target)
    if target["user_id"] == update.effective_user.id:
        await update.message.reply_text("You can't attack yourself!"); return
    if target["accrued_ton"] < 0.05:
        await update.message.reply_text(f"@{target_username} has no accumulated TON to steal."); return

    wins = random.random() > 0.4
    if wins:
        loot = round(target["accrued_ton"] * 0.15, 6)
        fee  = round(loot * 0.05, 6)
        net  = round(loot - fee, 6)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE players SET ton_balance = ton_balance + ? WHERE user_id = ?", (net, update.effective_user.id))
        conn.execute("UPDATE players SET accrued_ton = MAX(0, accrued_ton - ?) WHERE user_id = ?", (loot, target["user_id"]))
        conn.commit(); conn.close()
        add_tx(update.effective_user.id, f"PvP raid win vs @{target_username}", net, fee)
        await update.message.reply_text(
            f"⚔️ *Victory!*\n\nYou raided @{target_username} and stole\n"
            f"*+{net:.4f} TON* (after 5% game fee)!", parse_mode="Markdown")
        try:
            await ctx.bot.send_message(chat_id=target["user_id"],
                text=f"🚨 *You were raided!*\n\n@{update.effective_user.username} stole *{loot:.4f} TON*.\n"
                     f"Hire more NFT bodyguards to defend your turf!", parse_mode="Markdown")
        except: pass
    else:
        await update.message.reply_text(
            f"🛡 *Attack repelled!*\n\n@{target_username}'s bodyguards defended successfully.\nNo TON lost — better luck next time.", parse_mode="Markdown")


# ── /nft ──────────────────────────────────────────────────────────────────────
async def cmd_nft(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🛡 NFT Marketplace", web_app=WebAppInfo(url=f"{WEBAPP_URL}?page=nft"))
    ], [
        InlineKeyboardButton("💪 My Bodyguards", callback_data="nft_mine")
    ]])
    await update.message.reply_text(
        f"🛡 *NFT Bodyguard Marketplace*\n\n"
        f"Hire bodyguards to protect your businesses 24/7.\n"
        f"Each NFT has unique defense power and abilities.\n\n"
        f"💳 Payments go to owner wallet:\n`{OWNER_WALLET[:20]}...`",
        parse_mode="Markdown", reply_markup=keyboard)


# ── /top ──────────────────────────────────────────────────────────────────────
async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT username, ton_balance, pvp_wins, level FROM players ORDER BY ton_balance DESC LIMIT 10").fetchall()
    conn.close()
    medals = {1:"🥇", 2:"🥈", 3:"🥉"}
    lines = ["🏆 *StreetBoss Leaderboard*\n"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{medals.get(i, str(i)+'.'):3} @{r[0]} — {r[1]:.2f} TON | Lv.{r[3]}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /referral ─────────────────────────────────────────────────────────────────
async def cmd_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    player = get_player(uid)
    link = f"https://t.me/StreetBossBot?start=ref_{uid}"
    await update.message.reply_text(
        f"🤝 *Referral Program*\n\n"
        f"Earn *0.05 TON* for every friend who joins and plays!\n\n"
        f"Your link:\n`{link}`\n\n"
        f"Friends invited: *{player.get('referrals', 0)}*\n"
        f"TON earned from referrals: *{player.get('referral_earnings', 0):.4f} TON*",
        parse_mode="Markdown")


# ── /withdraw <amount> ────────────────────────────────────────────────────────
async def cmd_withdraw(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    player = get_player(user.id)
    if not ctx.args:
        await update.message.reply_text("Usage: /withdraw 1.5\nEnter the amount of TON you want to withdraw."); return
    try:
        amount = float(ctx.args[0])
    except:
        await update.message.reply_text("Invalid amount."); return
    if amount <= 0 or amount > player["ton_balance"]:
        await update.message.reply_text(f"Insufficient balance. You have {player['ton_balance']:.4f} TON."); return
    if not player.get("ton_address"):
        await update.message.reply_text("Connect your TON wallet first using /wallet."); return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO withdraw_requests (user_id, username, amount, wallet_addr, status, requested_at) VALUES (?,?,?,?,?,?)",
                 (user.id, user.username, amount, player["ton_address"], "pending", datetime.utcnow().isoformat()))
    conn.commit(); conn.close()

    await update.message.reply_text(
        f"📤 *Withdrawal request submitted!*\n\n"
        f"Amount: *{amount:.4f} TON*\n"
        f"To: `{player['ton_address'][:16]}...`\n\n"
        f"⏳ Awaiting admin approval. You will be notified when processed.", parse_mode="Markdown")

    # Notify owner
    try:
        await ctx.bot.send_message(chat_id=OWNER_TELEGRAM_ID,
            text=f"💸 *New withdrawal request!*\n\n"
                 f"Player: @{user.username}\n"
                 f"Amount: *{amount:.4f} TON*\n"
                 f"Wallet: `{player['ton_address']}`\n\n"
                 f"Approve in admin panel: {ADMIN_PANEL_URL}",
            parse_mode="Markdown")
    except: pass


# ── /admin — owner only ───────────────────────────────────────────────────────
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied."); return
    conn = sqlite3.connect(DB_PATH)
    players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    txs = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    pending_wd = conn.execute("SELECT COUNT(*) FROM withdraw_requests WHERE status='pending'").fetchone()[0]
    total_ton = conn.execute("SELECT SUM(owner_fee) FROM transactions").fetchone()[0] or 0
    conn.close()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🖥 Open Admin Panel", web_app=WebAppInfo(url=ADMIN_PANEL_URL))
    ], [
        InlineKeyboardButton(f"📤 Pending withdrawals ({pending_wd})", callback_data="admin_withdrawals")
    ]])
    await update.message.reply_text(
        f"🔐 *Admin Panel*\n\n"
        f"Players: *{players}*\n"
        f"Total transactions: *{txs}*\n"
        f"Owner wallet fees collected: *{total_ton:.4f} TON*\n"
        f"Pending withdrawals: *{pending_wd}*\n\n"
        f"Owner wallet:\n`{OWNER_WALLET}`",
        parse_mode="Markdown", reply_markup=keyboard)


# ── Callback handler ──────────────────────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "nft_mine":
        conn = sqlite3.connect(DB_PATH)
        nfts = conn.execute("SELECT name, power, rarity FROM player_nfts WHERE user_id = ?", (q.from_user.id,)).fetchall()
        conn.close()
        if not nfts:
            await q.edit_message_text("You don't have any bodyguards yet. Buy from the marketplace!")
            return
        lines = ["🛡 *Your Bodyguards:*\n"]
        for n in nfts:
            lines.append(f"• {n[0]} — Power: {n[1]} | {n[2]}")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown")
    elif q.data == "admin_withdrawals" and is_owner(q.from_user.id):
        conn = sqlite3.connect(DB_PATH)
        reqs = conn.execute("SELECT username, amount, wallet_addr, requested_at FROM withdraw_requests WHERE status='pending'").fetchall()
        conn.close()
        if not reqs:
            await q.edit_message_text("No pending withdrawals.")
            return
        lines = ["📤 *Pending Withdrawals:*\n"]
        for r in reqs:
            lines.append(f"• @{r[0]} — {r[1]:.4f} TON → `{r[2][:10]}...`")
        lines.append(f"\nApprove in admin panel: {ADMIN_PANEL_URL}")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown")


# ── Passive income job ────────────────────────────────────────────────────────
async def accrue_passive_income(ctx: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    biz = conn.execute("SELECT pb.user_id, pb.type, pb.level, pb.last_earn FROM player_businesses pb").fetchall()
    now = datetime.utcnow()
    for row in biz:
        uid, biz_type, level, last_earn_str = row
        if not last_earn_str: continue
        last_earn = datetime.fromisoformat(last_earn_str)
        hours = min((now - last_earn).total_seconds() / 3600, 12)
        earn_map = {"food": 0.01, "club": 0.02, "salon": 0.008, "garage": 0.035}
        base = earn_map.get(biz_type, 0.01)
        earned = round(base * (1.5 ** (level - 1)) * hours, 6)
        if earned > 0:
            conn.execute("UPDATE players SET accrued_ton = accrued_ton + ? WHERE user_id = ?", (earned, uid))
            conn.execute("UPDATE player_businesses SET last_earn = ? WHERE user_id = ? AND type = ?",
                         (now.isoformat(), uid, biz_type))
    conn.commit()
    conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("collect", cmd_collect))
    app.add_handler(CommandHandler("attack", cmd_attack))
    app.add_handler(CommandHandler("nft", cmd_nft))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("referral", cmd_referral))
    app.add_handler(CommandHandler("withdraw", cmd_withdraw))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.job_queue.run_repeating(accrue_passive_income, interval=3600, first=30)
    logger.info("StreetBoss bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
