"""
StreetBoss — Main App Entry Point
Railway auto-detects this file and runs it via uvicorn.
Serves: Frontend HTML + REST API + Admin panel
"""

import os
import sqlite3
import hmac
import hashlib
import json
import random
from datetime import datetime, timedelta
from urllib.parse import unquote, parse_qs
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
OWNER_WALLET     = "UQCvimmPAInaVHWUGPMfLu06m7k62bZ27_Asyob6w9_N7Ykw"
OWNER_TG_ID      = int(os.getenv("OWNER_TELEGRAM_ID", "0"))
ADMIN_PASSWORD   = os.getenv("ADMIN_PASSWORD", "streetboss2026")
DB_PATH          = os.getenv("DB_PATH", "streetboss.db")
PORT             = int(os.getenv("PORT", "8000"))

GAME_FEE         = 0.05   # 5% on every transaction
REFERRAL_REWARD  = 0.001  # TON per referral

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="StreetBoss", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DB init ───────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS players (
        user_id          INTEGER PRIMARY KEY,
        username         TEXT NOT NULL DEFAULT 'player',
        boss_type        TEXT DEFAULT NULL,
        ton_address      TEXT DEFAULT NULL,
        ton_balance      REAL DEFAULT 0.0,
        accrued_ton      REAL DEFAULT 0.0,
        level            INTEGER DEFAULT 1,
        xp               INTEGER DEFAULT 0,
        pvp_rank         INTEGER DEFAULT 999,
        pvp_wins         INTEGER DEFAULT 0,
        pvp_losses       INTEGER DEFAULT 0,
        referrals        INTEGER DEFAULT 0,
        referral_earnings REAL DEFAULT 0.0,
        referred_by      INTEGER DEFAULT NULL,
        last_collect     TEXT DEFAULT NULL,
        last_active      TEXT DEFAULT NULL,
        created_at       TEXT DEFAULT NULL,
        is_banned        INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS player_businesses (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        type         TEXT NOT NULL,
        level        INTEGER DEFAULT 1,
        last_earn    TEXT DEFAULT NULL,
        UNIQUE(user_id, type)
    );
    CREATE TABLE IF NOT EXISTS player_nfts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        nft_type     TEXT NOT NULL,
        name         TEXT,
        power        INTEGER DEFAULT 0,
        rarity       TEXT DEFAULT 'Common',
        purchased_at TEXT
    );
    CREATE TABLE IF NOT EXISTS transactions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        label        TEXT NOT NULL,
        amount       REAL NOT NULL,
        owner_fee    REAL DEFAULT 0.0,
        tx_hash      TEXT DEFAULT NULL,
        created_at   TEXT DEFAULT NULL
    );
    CREATE TABLE IF NOT EXISTS withdraw_requests (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER,
        username     TEXT,
        amount       REAL,
        wallet_addr  TEXT,
        status       TEXT DEFAULT 'pending',
        requested_at TEXT,
        resolved_at  TEXT DEFAULT NULL
    );
    CREATE TABLE IF NOT EXISTS pvp_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        attacker_id  INTEGER,
        defender_id  INTEGER,
        success      INTEGER,
        loot         REAL DEFAULT 0,
        penalty      REAL DEFAULT 0,
        created_at   TEXT
    );
    """)
    conn.commit()
    conn.close()

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def now_iso():
    return datetime.utcnow().isoformat()

def verify_init_data(init_data: str) -> dict:
    """Verify Telegram WebApp initData HMAC-SHA256."""
    if not init_data or not BOT_TOKEN:
        # Dev mode - return mock user
        return {"id": 0, "username": "dev_player", "first_name": "Dev"}
    try:
        parsed = parse_qs(unquote(init_data))
        data_check = "\n".join(
            f"{k}={v[0]}" for k, v in sorted(parsed.items()) if k != "hash"
        )
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if computed != parsed.get("hash", [""])[0]:
            raise ValueError("Invalid hash")
        return json.loads(parsed.get("user", ["{}"])[0])
    except Exception:
        # Allow in dev — in production set BOT_TOKEN
        return {"id": 0, "username": "player", "first_name": "Player"}

def get_or_create_player(user_id: int, username: str = "player") -> dict:
    conn = db()
    row = conn.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        n = now_iso()
        conn.execute(
            "INSERT INTO players (user_id,username,created_at,last_active,last_collect) VALUES(?,?,?,?,?)",
            (user_id, username, n, n, n)
        )
        conn.commit()
        # Give starter business
        conn.execute(
            "INSERT OR IGNORE INTO player_businesses (user_id,type,level,last_earn) VALUES(?,?,1,?)",
            (user_id, "food", n)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row)

def add_tx(user_id: int, label: str, amount: float, fee: float = 0.0):
    conn = db()
    conn.execute(
        "INSERT INTO transactions (user_id,label,amount,owner_fee,created_at) VALUES(?,?,?,?,?)",
        (user_id, label, amount, fee, now_iso())
    )
    conn.commit()
    conn.close()

def get_businesses(user_id: int) -> list:
    EARN = {"food": 0.010, "club": 0.020, "salon": 0.008, "garage": 0.035}
    conn = db()
    rows = conn.execute(
        "SELECT * FROM player_businesses WHERE user_id=?", (user_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        btype = r["type"]
        level = r["level"]
        base = EARN.get(btype, 0.01)
        earn = round(base * (1.5 ** (level - 1)), 6)
        result.append({
            "type": btype, "level": level,
            "earn_per_hour": earn,
            "defense": 20 * level,
            "upgrade_cost": round(0.5 * (1.8 ** level), 4),
            "last_earn": r["last_earn"]
        })
    return result

def accrue_income(user_id: int):
    """Calculate and credit passive income since last earn."""
    businesses = get_businesses(user_id)
    now = datetime.utcnow()
    total = 0.0
    conn = db()
    for b in businesses:
        last = datetime.fromisoformat(b["last_earn"]) if b["last_earn"] else now
        hours = min((now - last).total_seconds() / 3600, 12)
        earned = round(b["earn_per_hour"] * hours, 6)
        total += earned
        conn.execute(
            "UPDATE player_businesses SET last_earn=? WHERE user_id=? AND type=?",
            (now.isoformat(), user_id, b["type"])
        )
    if total > 0:
        conn.execute(
            "UPDATE players SET accrued_ton=accrued_ton+? WHERE user_id=?",
            (total, user_id)
        )
    conn.commit()
    conn.close()
    return total

# ── Auth dependency ───────────────────────────────────────────────────────────
async def get_user(x_telegram_initdata: Optional[str] = Header(None)) -> dict:
    return verify_init_data(x_telegram_initdata or "")

# ── Models ────────────────────────────────────────────────────────────────────
class BossSelect(BaseModel):
    boss_type: str

class BuyBusiness(BaseModel):
    biz_type: str

class UpgradeBusiness(BaseModel):
    biz_type: str

class AttackRequest(BaseModel):
    target_id: int

class BuyNFT(BaseModel):
    nft_type: str
    tx_hash: Optional[str] = None

class ConnectWallet(BaseModel):
    ton_address: str

class WithdrawRequest(BaseModel):
    amount: float
    wallet_addr: str

class AdminWithdraw(BaseModel):
    request_id: int
    action: str  # 'approve' or 'reject'

# ══════════════════════════════════════════════════════════════════════════════
#  PLAYER ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/player")
async def get_player(user: dict = Header(None), x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    player = get_or_create_player(uid, u.get("username", "player"))
    accrue_income(uid)
    conn = db()
    player = dict(conn.execute("SELECT * FROM players WHERE user_id=?", (uid,)).fetchone())
    businesses = get_businesses(uid)
    nfts = conn.execute("SELECT * FROM player_nfts WHERE user_id=?", (uid,)).fetchall()
    txs = conn.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (uid,)
    ).fetchall()
    conn.close()

    earn_per_hour = sum(b["earn_per_hour"] for b in businesses)
    nft_power = sum(n["power"] for n in nfts)
    biz_power = sum(b["defense"] for b in businesses)

    return {
        **player,
        "businesses": businesses,
        "nfts": [dict(n) for n in nfts],
        "transactions": [dict(t) for t in txs],
        "earn_per_hour": round(earn_per_hour, 6),
        "total_power": biz_power + nft_power,
    }

@app.post("/api/player/boss")
async def select_boss(body: BossSelect, x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    if body.boss_type not in ("hood", "tatted", "don"):
        raise HTTPException(400, "Invalid boss type")
    conn = db()
    conn.execute("UPDATE players SET boss_type=? WHERE user_id=?", (body.boss_type, uid))
    conn.commit()
    conn.close()
    return {"success": True, "boss_type": body.boss_type}

@app.post("/api/collect")
async def collect(x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    accrue_income(uid)
    conn = db()
    row = conn.execute("SELECT accrued_ton FROM players WHERE user_id=?", (uid,)).fetchone()
    if not row or row[0] <= 0:
        conn.close()
        raise HTTPException(400, "Nothing to collect")
    collected = round(row[0], 6)
    fee = round(collected * GAME_FEE, 6)
    net = round(collected - fee, 6)
    conn.execute(
        "UPDATE players SET ton_balance=ton_balance+?, accrued_ton=0, last_collect=? WHERE user_id=?",
        (net, now_iso(), uid)
    )
    conn.commit()
    conn.close()
    add_tx(uid, "Passive income collected", net, fee)
    return {"collected": net, "fee": fee, "gross": collected}

# ══════════════════════════════════════════════════════════════════════════════
#  BUSINESS ROUTES
# ══════════════════════════════════════════════════════════════════════════════

BUSINESS_CONFIGS = {
    "food":   {"name": "Street Burger", "emoji": "🍔", "base_cost": 0.5,  "unlock_level": 1},
    "club":   {"name": "Club Neon",     "emoji": "🎵", "base_cost": 1.2,  "unlock_level": 2},
    "salon":  {"name": "VIP Salon",     "emoji": "💈", "base_cost": 0.3,  "unlock_level": 1},
    "garage": {"name": "Tuning Shop",   "emoji": "🔧", "base_cost": 5.0,  "unlock_level": 5},
}

@app.post("/api/business/buy")
async def buy_business(body: BuyBusiness, x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    if body.biz_type not in BUSINESS_CONFIGS:
        raise HTTPException(400, "Unknown business type")
    cfg = BUSINESS_CONFIGS[body.biz_type]
    conn = db()
    existing = conn.execute(
        "SELECT id FROM player_businesses WHERE user_id=? AND type=?", (uid, body.biz_type)
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(400, "Business already owned")
    player = conn.execute("SELECT ton_balance, level FROM players WHERE user_id=?", (uid,)).fetchone()
    if not player or player["ton_balance"] < cfg["base_cost"]:
        conn.close()
        raise HTTPException(400, f"Need {cfg['base_cost']} TON")
    n = now_iso()
    conn.execute(
        "UPDATE players SET ton_balance=ton_balance-?, xp=xp+50 WHERE user_id=?",
        (cfg["base_cost"], uid)
    )
    conn.execute(
        "INSERT INTO player_businesses (user_id,type,level,last_earn) VALUES(?,?,1,?)",
        (uid, body.biz_type, n)
    )
    conn.commit()
    conn.close()
    add_tx(uid, f"Bought {cfg['name']}", -cfg["base_cost"], cfg["base_cost"] * GAME_FEE)
    return {"success": True, "name": cfg["name"], "cost": cfg["base_cost"]}

@app.post("/api/business/upgrade")
async def upgrade_business(body: UpgradeBusiness, x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    conn = db()
    biz = conn.execute(
        "SELECT * FROM player_businesses WHERE user_id=? AND type=?", (uid, body.biz_type)
    ).fetchone()
    if not biz:
        conn.close()
        raise HTTPException(404, "Business not found")
    level = biz["level"]
    if level >= 10:
        conn.close()
        raise HTTPException(400, "Max level reached")
    cost = round(0.5 * (1.8 ** level), 4)
    player = conn.execute("SELECT ton_balance FROM players WHERE user_id=?", (uid,)).fetchone()
    if player["ton_balance"] < cost:
        conn.close()
        raise HTTPException(400, f"Need {cost} TON")
    fee = round(cost * GAME_FEE, 6)
    conn.execute(
        "UPDATE players SET ton_balance=ton_balance-?, xp=xp+30 WHERE user_id=?", (cost, uid)
    )
    conn.execute(
        "UPDATE player_businesses SET level=level+1 WHERE user_id=? AND type=?", (uid, body.biz_type)
    )
    conn.commit()
    conn.close()
    add_tx(uid, f"Upgraded {body.biz_type} to Lv.{level+1}", -cost, fee)
    return {"success": True, "new_level": level + 1, "cost": cost}

# ══════════════════════════════════════════════════════════════════════════════
#  PVP ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/pvp/targets")
async def pvp_targets(x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    conn = db()
    targets = conn.execute(
        "SELECT user_id, username, pvp_rank, accrued_ton, boss_type FROM players "
        "WHERE user_id!=? AND accrued_ton>=0.05 AND is_banned=0 "
        "ORDER BY accrued_ton DESC LIMIT 5",
        (uid,)
    ).fetchall()
    conn.close()
    return [dict(t) for t in targets]

@app.post("/api/pvp/attack")
async def pvp_attack(body: AttackRequest, x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    if uid == body.target_id:
        raise HTTPException(400, "Can't attack yourself")
    conn = db()
    target = conn.execute(
        "SELECT accrued_ton FROM players WHERE user_id=?", (body.target_id,)
    ).fetchone()
    if not target or target["accrued_ton"] < 0.05:
        conn.close()
        raise HTTPException(400, "Target has nothing to steal")
    wins = random.random() > 0.40
    n = now_iso()
    if wins:
        loot = round(target["accrued_ton"] * 0.15, 6)
        fee = round(loot * GAME_FEE, 6)
        net = round(loot - fee, 6)
        conn.execute(
            "UPDATE players SET ton_balance=ton_balance+?, pvp_wins=pvp_wins+1 WHERE user_id=?",
            (net, uid)
        )
        conn.execute(
            "UPDATE players SET accrued_ton=MAX(0,accrued_ton-?), pvp_losses=pvp_losses+1 WHERE user_id=?",
            (loot, body.target_id)
        )
        conn.execute(
            "INSERT INTO pvp_log (attacker_id,defender_id,success,loot,created_at) VALUES(?,?,1,?,?)",
            (uid, body.target_id, net, n)
        )
        conn.commit(); conn.close()
        add_tx(uid, "PvP raid victory", net, fee)
        return {"success": True, "loot": net, "fee": fee}
    else:
        penalty = 0.01
        conn.execute(
            "UPDATE players SET ton_balance=MAX(0,ton_balance-?), pvp_losses=pvp_losses+1 WHERE user_id=?",
            (penalty, uid)
        )
        conn.execute(
            "UPDATE players SET pvp_wins=pvp_wins+1 WHERE user_id=?", (body.target_id,)
        )
        conn.execute(
            "INSERT INTO pvp_log (attacker_id,defender_id,success,penalty,created_at) VALUES(?,?,0,?,?)",
            (uid, body.target_id, penalty, n)
        )
        conn.commit(); conn.close()
        return {"success": False, "penalty": penalty}

# ══════════════════════════════════════════════════════════════════════════════
#  NFT ROUTES
# ══════════════════════════════════════════════════════════════════════════════

NFT_CONFIGS = {
    "sentinel":     {"name": "Bot Sentinel",  "power": 40,  "price": 0.30, "rarity": "Common"},
    "iron_mihai":   {"name": "Iron Mihai",    "power": 80,  "price": 0.80, "rarity": "Rare"},
    "shadow_viktor":{"name": "Shadow Viktor", "power": 150, "price": 1.20, "rarity": "Epic"},
    "wolf_andrei":  {"name": "Wolf Andrei",   "power": 200, "price": 1.80, "rarity": "Epic"},
    "lion_dragos":  {"name": "Lion Dragos",   "power": 320, "price": 2.50, "rarity": "Legendary"},
}

@app.get("/api/nft/market")
async def nft_market():
    return [
        {"type": k, **v} for k, v in NFT_CONFIGS.items()
    ]

@app.get("/api/nft/mine")
async def my_nfts(x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    conn = db()
    nfts = conn.execute("SELECT * FROM player_nfts WHERE user_id=?", (uid,)).fetchall()
    conn.close()
    return [dict(n) for n in nfts]

@app.post("/api/nft/buy")
async def buy_nft(body: BuyNFT, x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    if body.nft_type not in NFT_CONFIGS:
        raise HTTPException(400, "Unknown NFT")
    cfg = NFT_CONFIGS[body.nft_type]
    conn = db()
    player = conn.execute("SELECT ton_balance FROM players WHERE user_id=?", (uid,)).fetchone()
    if not player or player["ton_balance"] < cfg["price"]:
        conn.close()
        raise HTTPException(400, f"Need {cfg['price']} TON")
    fee = round(cfg["price"] * GAME_FEE, 6)
    conn.execute(
        "UPDATE players SET ton_balance=ton_balance-? WHERE user_id=?", (cfg["price"], uid)
    )
    conn.execute(
        "INSERT INTO player_nfts (user_id,nft_type,name,power,rarity,purchased_at) VALUES(?,?,?,?,?,?)",
        (uid, body.nft_type, cfg["name"], cfg["power"], cfg["rarity"], now_iso())
    )
    conn.commit(); conn.close()
    add_tx(uid, f"NFT {cfg['name']} purchased", -cfg["price"], fee)
    return {"success": True, "nft": cfg, "fee": fee}

# ══════════════════════════════════════════════════════════════════════════════
#  WALLET ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/wallet/connect")
async def connect_wallet(body: ConnectWallet, x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    conn = db()
    conn.execute("UPDATE players SET ton_address=? WHERE user_id=?", (body.ton_address, uid))
    conn.commit(); conn.close()
    return {"success": True, "address": body.ton_address}

@app.post("/api/wallet/withdraw")
async def request_withdraw(body: WithdrawRequest, x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    conn = db()
    player = conn.execute("SELECT ton_balance, username FROM players WHERE user_id=?", (uid,)).fetchone()
    if not player or player["ton_balance"] < body.amount:
        conn.close()
        raise HTTPException(400, "Insufficient balance")
    conn.execute(
        "INSERT INTO withdraw_requests (user_id,username,amount,wallet_addr,status,requested_at) VALUES(?,?,?,?,?,?)",
        (uid, player["username"], body.amount, body.wallet_addr, "pending", now_iso())
    )
    conn.commit(); conn.close()
    return {"success": True, "status": "pending", "message": "Awaiting admin approval"}

@app.get("/api/wallet/transactions")
async def wallet_transactions(x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    conn = db()
    txs = conn.execute(
        "SELECT * FROM transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (uid,)
    ).fetchall()
    conn.close()
    return [dict(t) for t in txs]

# ══════════════════════════════════════════════════════════════════════════════
#  REFERRAL ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/referral")
async def referral_info(x_telegram_initdata: Optional[str] = Header(None)):
    u = verify_init_data(x_telegram_initdata or "")
    uid = u.get("id", 0)
    player = get_or_create_player(uid)
    conn = db()
    friends = conn.execute(
        "SELECT username, level, created_at FROM players WHERE referred_by=?", (uid,)
    ).fetchall()
    conn.close()
    return {
        "referral_link": f"https://t.me/StreetBossBot?start=ref_{uid}",
        "referrals": player.get("referrals", 0),
        "referral_earnings": player.get("referral_earnings", 0.0),
        "friends": [dict(f) for f in friends],
    }

# ══════════════════════════════════════════════════════════════════════════════
#  LEADERBOARD
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/leaderboard")
async def leaderboard():
    conn = db()
    rows = conn.execute(
        "SELECT user_id, username, ton_balance, pvp_wins, level, boss_type "
        "FROM players WHERE is_banned=0 ORDER BY ton_balance DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN ROUTES (protected by X-Admin-Key header)
# ══════════════════════════════════════════════════════════════════════════════

def check_admin(key: str):
    if key != ADMIN_PASSWORD:
        raise HTTPException(403, "Admin access denied")

@app.get("/api/admin/stats")
async def admin_stats(x_admin_key: Optional[str] = Header(None)):
    check_admin(x_admin_key or "")
    conn = db()
    players   = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    revenue   = conn.execute("SELECT COALESCE(SUM(owner_fee),0) FROM transactions").fetchone()[0]
    pending   = conn.execute("SELECT COUNT(*) FROM withdraw_requests WHERE status='pending'").fetchone()[0]
    txs_today = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE created_at>=?",
        ((datetime.utcnow() - timedelta(days=1)).isoformat(),)
    ).fetchone()[0]
    conn.close()
    return {
        "total_players": players,
        "total_revenue_ton": round(revenue, 4),
        "pending_withdrawals": pending,
        "transactions_24h": txs_today,
        "owner_wallet": OWNER_WALLET,
    }

@app.get("/api/admin/players")
async def admin_players(x_admin_key: Optional[str] = Header(None)):
    check_admin(x_admin_key or "")
    conn = db()
    rows = conn.execute(
        "SELECT * FROM players ORDER BY ton_balance DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/admin/withdrawals")
async def admin_withdrawals(x_admin_key: Optional[str] = Header(None)):
    check_admin(x_admin_key or "")
    conn = db()
    rows = conn.execute(
        "SELECT * FROM withdraw_requests ORDER BY requested_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/admin/withdraw/{req_id}/{action}")
async def admin_handle_withdraw(req_id: int, action: str, x_admin_key: Optional[str] = Header(None)):
    check_admin(x_admin_key or "")
    if action not in ("approve", "reject"):
        raise HTTPException(400, "Action must be approve or reject")
    conn = db()
    req = conn.execute("SELECT * FROM withdraw_requests WHERE id=?", (req_id,)).fetchone()
    if not req:
        conn.close()
        raise HTTPException(404, "Request not found")
    if req["status"] != "pending":
        conn.close()
        raise HTTPException(400, "Already processed")
    conn.execute(
        "UPDATE withdraw_requests SET status=?, resolved_at=? WHERE id=?",
        (action + "d", now_iso(), req_id)
    )
    if action == "approve":
        conn.execute(
            "UPDATE players SET ton_balance=MAX(0,ton_balance-?) WHERE user_id=?",
            (req["amount"], req["user_id"])
        )
        add_tx(req["user_id"], f"Withdrawal {action}d", -req["amount"])
    conn.commit(); conn.close()
    return {"success": True, "action": action, "amount": req["amount"]}

@app.post("/api/admin/ban/{user_id}")
async def admin_ban(user_id: int, x_admin_key: Optional[str] = Header(None)):
    check_admin(x_admin_key or "")
    conn = db()
    conn.execute("UPDATE players SET is_banned=1 WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()
    return {"success": True, "banned": user_id}

@app.get("/api/admin/transactions")
async def admin_transactions(x_admin_key: Optional[str] = Header(None)):
    check_admin(x_admin_key or "")
    conn = db()
    rows = conn.execute(
        "SELECT t.*, p.username FROM transactions t "
        "JOIN players p ON t.user_id=p.user_id "
        "ORDER BY t.created_at DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ══════════════════════════════════════════════════════════════════════════════
#  SERVE FRONTEND
# ══════════════════════════════════════════════════════════════════════════════

# Serve static files
frontend_path = Path(__file__).parent / "frontend"
admin_path = Path(__file__).parent / "admin"

if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index = frontend_path / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text())
    return HTMLResponse("<h1>StreetBoss</h1><p>Frontend not found.</p>")

@app.get("/admin", response_class=HTMLResponse)
async def serve_admin(request: Request):
    panel = admin_path / "panel.html"
    if panel.exists():
        return HTMLResponse(panel.read_text())
    return HTMLResponse("<h1>Admin panel not found</h1>")

@app.get("/health")
async def health():
    return {"status": "ok", "game": "StreetBoss", "version": "2.0"}

@app.get("/tonconnect-manifest.json")
async def ton_manifest():
    return {
        "url": "https://streetboss-production.up.railway.app",
        "name": "StreetBoss",
        "iconUrl": "https://streetboss-production.up.railway.app/static/streetboss_icon.png",
        "termsOfUseUrl": "https://streetboss-production.up.railway.app",
        "privacyPolicyUrl": "https://streetboss-production.up.railway.app"
    }

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
