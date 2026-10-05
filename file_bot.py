# -*- coding: utf-8 -*-
"""
File_bot.py  -  Professional Telegram File Store Bot
=====================================================

Single-file bot (python-telegram-bot v21+, aiosqlite, Render ready).

User features
  * Upload any files (documents, videos, photos, audio, voice, GIF, stickers, albums)
  * One secure deep-link per upload (batch / folder links)
  * Optional password per link  (salted PBKDF2-SHA256 hash, never stored in plain text,
    brute-force lockout, password message auto-deleted from the chat)
  * Open limit, expiry time, auto-delete of delivered files, content protection
  * Link manager: rename, change password, revoke / delete, share button
  * Forced channel join before opening any link

Admin features
  * Dashboard, users list (who is using the bot), per-user profile & links
  * All links / files with uploader, access history, get-files, revoke, delete
  * Search (user id, @username, link code / URL), top uploaders, activity log
  * Broadcast (any content, copy-based), ban / unban, message a user
  * Force-join channel manager, admin manager, settings, DB backup & restore

Every text uses Telegram Premium custom emoji and every inline button is colored
(Bot API 9.4 "style") and carries a premium icon.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import logging
import os
import random
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiosqlite

try:  # .env support (Render env vars work without it)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Conflict, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

# ======================================================================
#  CONFIG
# ======================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "8200980090") or 8200980090)
FORCE_CHANNEL_ID = int(os.getenv("FORCE_CHANNEL_ID", "-1002740009398") or 0)
FORCE_CHANNEL_LINK = os.getenv("FORCE_CHANNEL_LINK", "https://t.me/+2Fxg6o4jEKAxOGQ1").strip()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "X_NAGI7").strip().lstrip("@")
DB_PATH = os.getenv("DB_PATH", "filestore.db").strip()
PORT = int(os.getenv("PORT", "10000") or 10000)
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID", "0") or 0)  # optional mirror channel
AUTO_BACKUP_HOURS = int(os.getenv("AUTO_BACKUP_HOURS", "24") or 0)
TZ = ZoneInfo(os.getenv("TIMEZONE", "UTC"))

PBKDF_ITERS = 200_000
MAX_PW_FAILS = 5
PW_LOCK_SECS = 15 * 60
PER_PAGE = 6

START_TS = time.time()
BOT_USERNAME = ""

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("file_bot")

# ======================================================================
#  PREMIUM EMOJI REGISTRY   name -> (custom_emoji_id, fallback emoji)
# ======================================================================
EMOJI: Dict[str, Tuple[str, str]] = {
    "ok": ("6309618368722770953", "✅"),
    "cross": ("5210952531676504517", "❌"),
    "warn": ("6309743751703042732", "⚠"),
    "star": ("6309569217117035347", "⭐"),
    "sparkle": ("6312315895947467982", "✨"),
    "crown": ("6310083389126876253", "👑"),
    "fire": ("6309811869884358547", "🔥"),
    "bolt": ("6309883501348919953", "⚡"),
    "rocket": ("6309870788245724520", "🚀"),
    "gift": ("6312282231993801242", "🎁"),
    "lock": ("6310091871687286225", "🔒"),
    "key": ("6309599324837781578", "🔐"),
    "link": ("6309893061946121745", "🔗"),
    "chart": ("6309843875980648126", "📊"),
    "bell": ("6311933167116753547", "🔔"),
    "mega": ("6309618596356039410", "📣"),
    "gem": ("6310032545304026289", "💎"),
    "trash": ("6309887495668505491", "🗑"),
    "shield": ("5251203410396458957", "🛡"),
    "user": ("5902335789798265487", "👤"),
    "gear": ("5893161718179173515", "⚙"),
    "clock": ("5902050947567194830", "⏰"),
    "hourglass": ("5386367538735104399", "⏳"),
    "cal": ("5413879192267805083", "📅"),
    "back": ("4956304191279596468", "👈"),
    "next": ("6311966281314606219", "➡"),
    "refresh": ("5375338737028841420", "🔄"),
    "down": ("5406745015365943482", "⬇"),
    "up": ("5415655814079723871", "⬆"),
    "plus": ("5397916757333654639", "➕"),
    "info": ("5334544901428229844", "ℹ"),
    "search": ("5231012545799666522", "🔍"),
    "folder": ("5222444124698853913", "📚"),
    "clip": ("5305265301917549162", "🖇"),
    "mail": ("5253742260054409879", "📬"),
    "phone": ("5895652322469482989", "📱"),
    "globe": ("5447410659077661506", "🌐"),
    "no": ("6312156428106737991", "🚫"),
    "stop": ("5260293700088511294", "⛔"),
    "green": ("5416081784641168838", "🟢"),
    "red": ("5411225014148014586", "🔴"),
    "bot": ("6309920764485180319", "🤖"),
    "pin": ("6309960153630251694", "📌"),
    "eyes": ("6309744151135001261", "👀"),
    "heart": ("6309591606781549616", "❤"),
    "party": ("6309961712703379735", "🎉"),
    "like": ("6309697344581410091", "👍"),
    "chat": ("6310075555106528104", "💬"),
    "tool": ("5341715473882955310", "🔧"),
    "bulb": ("5422439311196834318", "💡"),
    "trophy": ("6311834808070708173", "🏆"),
    "laptop": ("5282843764451195532", "💻"),
    "speaker": ("5388632425314140043", "🔊"),
    "trend": ("5244837092042750681", "📈"),
    "new": ("5382357040008021292", "🆕"),
    "wave": ("4958832114540741368", "👋"),
    "sliders": ("6311841903356680387", "🎚"),
    "puzzle": ("4958903389523018769", "🧩"),
    "cool": ("6309750421787254668", "😎"),
    "sad": ("6312088000687773817", "😭"),
    "alarm": ("6312127578811407558", "🚨"),
    "money": ("5893473283696759404", "💰"),
    "bookmark": ("6309928100289322286", "🔖"),
    "play": ("5264919878082509254", "▶"),
    "pause": ("5359543311897998264", "⏸"),
    "medal": ("5440539497383087970", "🎖"),
    "smile": ("6309630076803620076", "😊"),
    "diamond": ("6309958250959739643", "💠"),
    "check": ("6310103949135322908", "✔"),
}


def E(name: str) -> str:
    """Premium custom emoji for HTML text."""
    eid, fb = EMOJI[name]
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'


_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)


def plain(text: str) -> str:
    """Strip custom emoji tags -> normal emoji (fallback when custom emoji is rejected)."""
    return _TG_EMOJI_RE.sub(r"\1", text or "")


# ======================================================================
#  BUTTON / KEYBOARD HELPERS  (colored buttons + premium icon)
# ======================================================================
def B(text: str, cb: Optional[str] = None, *, url: Optional[str] = None,
      emoji: Optional[str] = None, style: str = "primary") -> InlineKeyboardButton:
    """style: primary (blue) | success (green) | danger (red)."""
    kw: Dict[str, Any] = {}
    if emoji:
        kw["icon_custom_emoji_id"] = EMOJI[emoji][0]
    if style:
        kw["style"] = style
    if url:
        return InlineKeyboardButton(text, url=url, api_kwargs=kw)
    return InlineKeyboardButton(text, callback_data=cb, api_kwargs=kw)


def KB(*rows: List[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([list(r) for r in rows if r])


def strip_markup(m):
    """Remove style/icon (fallback if the Bot API rejects them)."""
    if not isinstance(m, InlineKeyboardMarkup):
        return m
    rows = []
    for row in m.inline_keyboard:
        rows.append([InlineKeyboardButton(b.text, callback_data=b.callback_data, url=b.url) for b in row])
    return InlineKeyboardMarkup(rows)


def pager(prefix: str, page: int, total: int, per: int = PER_PAGE) -> List[InlineKeyboardButton]:
    pages = max(1, (total + per - 1) // per)
    row = []
    if page > 0:
        row.append(B("Prev", f"{prefix}|{page - 1}", emoji="back", style="primary"))
    row.append(B(f"{page + 1}/{pages}", "noop", emoji="bookmark", style="primary"))
    if page < pages - 1:
        row.append(B("Next", f"{prefix}|{page + 1}", emoji="next", style="primary"))
    return row


# ======================================================================
#  SMALL UTILS
# ======================================================================
NOPREVIEW = LinkPreviewOptions(is_disabled=True)
_FANCY_ERR = ("emoji", "entit", "style", "icon", "button")


def now() -> int:
    return int(time.time())


def h(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def to_int(s: Any, default: int = 0) -> int:
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return default


def fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, TZ).strftime("%d %b %Y, %I:%M %p")


def human_size(n: Optional[int]) -> str:
    if not n:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "—"


def human_dur(secs: int) -> str:
    secs = int(secs)
    if secs <= 0:
        return "Off"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 86400:
        return f"{secs // 3600} h"
    return f"{secs // 86400} d"


def human_left(ts: int) -> str:
    left = ts - now()
    if left <= 0:
        return "expired"
    return human_dur(left) + " left"


def mention(uid: int, name: Optional[str]) -> str:
    return f'<a href="tg://user?id={uid}">{h(name or "User")}</a>'


def user_label(row) -> str:
    name = row["first_name"] or "User"
    uname = f" @{row['username']}" if row["username"] else ""
    return f"{h(name)}{h(uname)}"


def link_url(code: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start={code}"


def onoff(v: Any) -> str:
    return f"{E('ok')} On" if v else f"{E('cross')} Off"


# ======================================================================
#  TELEGRAM SEND / EDIT WRAPPERS (flood-safe, premium-emoji fallback)
# ======================================================================
async def tg_retry(fn, *a, **kw):
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            return await fn(*a, **kw)
        except RetryAfter as e:
            last = e
            await asyncio.sleep(float(e.retry_after) + 0.5)
        except BadRequest:
            raise
        except NetworkError as e:
            last = e
            if attempt == 2:
                raise
            await asyncio.sleep(1 + attempt)
    if last:
        raise last


def _fancy_error(e: Exception) -> bool:
    s = str(e).lower()
    return any(k in s for k in _FANCY_ERR)


async def send(bot, chat_id: int, text: str, kb=None, **kw):
    kw.setdefault("link_preview_options", NOPREVIEW)
    try:
        return await tg_retry(bot.send_message, chat_id, text, parse_mode=ParseMode.HTML,
                              reply_markup=kb, **kw)
    except BadRequest as e:
        if _fancy_error(e):
            return await tg_retry(bot.send_message, chat_id, plain(text), parse_mode=ParseMode.HTML,
                                  reply_markup=strip_markup(kb), **kw)
        raise


async def edit(bot, chat_id: int, message_id: int, text: str, kb=None):
    async def go(t, k):
        return await tg_retry(bot.edit_message_text, t, chat_id=chat_id, message_id=message_id,
                              parse_mode=ParseMode.HTML, reply_markup=k, link_preview_options=NOPREVIEW)

    try:
        return await go(text, kb)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return None
        if _fancy_error(e):
            try:
                return await go(plain(text), strip_markup(kb))
            except BadRequest as e2:
                if "not modified" in str(e2).lower():
                    return None
                raise
        raise


async def edit_or_send(bot, chat_id: int, mid: Optional[int], text: str, kb=None):
    if mid:
        try:
            await edit(bot, chat_id, mid, text, kb)
            return
        except TelegramError:
            pass
    await send(bot, chat_id, text, kb)


async def show(context, update: Update, text: str, kb=None):
    """Edit the callback message in place, or send a fresh message."""
    q = update.callback_query
    if q and q.message:
        try:
            await edit(context.bot, q.message.chat.id, q.message.message_id, text, kb)
            return
        except TelegramError:
            pass
    await send(context.bot, update.effective_chat.id, text, kb)


async def safe_answer(q, text: Optional[str] = None, alert: bool = False):
    try:
        await q.answer(text=text, show_alert=alert)
    except TelegramError:
        pass


async def safe_delete(bot, chat_id: int, mid: int):
    try:
        await bot.delete_message(chat_id, mid)
    except TelegramError:
        pass


# ======================================================================
#  DATABASE
# ======================================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
    joined_at INTEGER, last_seen INTEGER,
    banned INTEGER DEFAULT 0, ban_reason TEXT, blocked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS links(
    code TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, title TEXT, created_at INTEGER,
    pw_hash TEXT, pw_salt TEXT, max_opens INTEGER DEFAULT 0, opens INTEGER DEFAULT 0,
    expires_at INTEGER DEFAULT 0, autodel INTEGER DEFAULT 0, protect INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active');
CREATE INDEX IF NOT EXISTS idx_links_owner ON links(owner_id);
CREATE TABLE IF NOT EXISTS items(
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, pos INTEGER, ftype TEXT,
    file_id TEXT, unique_id TEXT, file_name TEXT, file_size INTEGER, caption TEXT,
    storage_msg_id INTEGER);
CREATE INDEX IF NOT EXISTS idx_items_code ON items(code);
CREATE TABLE IF NOT EXISTS access_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, user_id INTEGER, ts INTEGER, result TEXT);
CREATE INDEX IF NOT EXISTS idx_access_code ON access_log(code);
CREATE TABLE IF NOT EXISTS pw_attempts(
    code TEXT, user_id INTEGER, fails INTEGER DEFAULT 0, locked_until INTEGER DEFAULT 0,
    PRIMARY KEY(code, user_id));
CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY, added_by INTEGER, added_at INTEGER);
CREATE TABLE IF NOT EXISTS fsub(chat_id INTEGER PRIMARY KEY, link TEXT, title TEXT, added_at INTEGER);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS activity(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, user_id INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS autodel(
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, message_id INTEGER, delete_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_autodel ON autodel(delete_at);
"""

DEFAULT_SETTINGS = {
    "maintenance": "0",
    "uploads_open": "1",
    "notify_uploads": "1",
    "max_files": "50",
    "max_links": "100",
    "default_autodel": "0",
    "welcome": "",
}
SETTINGS: Dict[str, str] = dict(DEFAULT_SETTINGS)
ADMINS: set = {OWNER_ID}


class DB:
    conn: Optional[aiosqlite.Connection] = None


async def db_open():
    DB.conn = await aiosqlite.connect(DB_PATH)
    DB.conn.row_factory = sqlite3.Row
    await DB.conn.execute("PRAGMA journal_mode=WAL")
    await DB.conn.execute("PRAGMA synchronous=NORMAL")
    await DB.conn.executescript(SCHEMA)
    await DB.conn.commit()


async def db_close():
    if DB.conn:
        await DB.conn.close()
        DB.conn = None


async def q_all(sql: str, args: tuple = ()) -> list:
    async with DB.conn.execute(sql, args) as cur:
        return await cur.fetchall()


async def q_one(sql: str, args: tuple = ()):
    async with DB.conn.execute(sql, args) as cur:
        return await cur.fetchone()


async def q_exec(sql: str, args: tuple = ()) -> int:
    cur = await DB.conn.execute(sql, args)
    await DB.conn.commit()
    n = cur.rowcount
    await cur.close()
    return n


def S(key: str) -> str:
    return SETTINGS.get(key, DEFAULT_SETTINGS.get(key, ""))


async def load_settings():
    for r in await q_all("SELECT key, value FROM settings"):
        SETTINGS[r["key"]] = r["value"]


async def set_setting(key: str, value: str):
    SETTINGS[key] = value
    await q_exec("INSERT INTO settings(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


async def load_admins():
    ADMINS.clear()
    ADMINS.add(OWNER_ID)
    for r in await q_all("SELECT user_id FROM admins"):
        ADMINS.add(r["user_id"])


def is_admin(uid: int) -> bool:
    return uid in ADMINS


async def seed_fsub():
    if FORCE_CHANNEL_ID and not await q_one("SELECT 1 FROM fsub WHERE chat_id=?", (FORCE_CHANNEL_ID,)):
        # seed only on a brand-new database (don't resurrect a channel the owner removed)
        if not await q_one("SELECT 1 FROM settings WHERE key='fsub_seeded'"):
            await q_exec("INSERT INTO fsub(chat_id,link,title,added_at) VALUES(?,?,?,?)",
                         (FORCE_CHANNEL_ID, FORCE_CHANNEL_LINK, "Our Channel", now()))
    await set_setting("fsub_seeded", "1")


async def log_act(kind: str, uid: int, detail: str = ""):
    await q_exec("INSERT INTO activity(ts,kind,user_id,detail) VALUES(?,?,?,?)",
                 (now(), kind, uid, detail[:200]))
    if random.random() < 0.01:
        await q_exec("DELETE FROM activity WHERE id < (SELECT MAX(id) - 5000 FROM activity)")
        await q_exec("DELETE FROM access_log WHERE id < (SELECT MAX(id) - 50000 FROM access_log)")


_seen: Dict[int, Tuple[float, str, str]] = {}


async def touch_user(u) -> None:
    uname, fname = (u.username or ""), (u.first_name or "")
    cached = _seen.get(u.id)
    t = time.time()
    if cached and t - cached[0] < 60 and cached[1] == uname and cached[2] == fname:
        return
    _seen[u.id] = (t, uname, fname)
    exists = await q_one("SELECT 1 FROM users WHERE user_id=?", (u.id,))
    await q_exec(
        "INSERT INTO users(user_id,username,first_name,joined_at,last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, "
        "last_seen=excluded.last_seen, blocked=0",
        (u.id, uname, fname, now(), now()))
    if not exists:
        await log_act("new_user", u.id, fname)


# ======================================================================
#  PASSWORD SECURITY
# ======================================================================
def _hash_pw(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF_ITERS).hex()


async def make_pw(password: str) -> Tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = await asyncio.to_thread(_hash_pw, password, salt)
    return digest, salt.hex()


async def check_pw(password: str, digest: str, salt_hex: str) -> bool:
    calc = await asyncio.to_thread(_hash_pw, password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(calc, digest)


# ======================================================================
#  FILE TYPES
# ======================================================================
SEND_MAP = {
    "document": ("send_document", "document"),
    "video": ("send_video", "video"),
    "audio": ("send_audio", "audio"),
    "photo": ("send_photo", "photo"),
    "voice": ("send_voice", "voice"),
    "animation": ("send_animation", "animation"),
    "video_note": ("send_video_note", "video_note"),
    "sticker": ("send_sticker", "sticker"),
}
TYPE_ICON = {"document": "clip", "video": "play", "audio": "speaker", "photo": "eyes",
             "voice": "chat", "animation": "sparkle", "video_note": "play", "sticker": "smile"}


def extract_item(m) -> Optional[Dict[str, Any]]:
    cap = m.caption_html if m.caption else None
    base = {"caption": cap, "src_chat": m.chat_id, "src_mid": m.message_id}
    if m.document:
        d = m.document
        return {**base, "ftype": "document", "file_id": d.file_id, "unique_id": d.file_unique_id,
                "file_name": d.file_name, "file_size": d.file_size}
    if m.video:
        d = m.video
        return {**base, "ftype": "video", "file_id": d.file_id, "unique_id": d.file_unique_id,
                "file_name": d.file_name, "file_size": d.file_size}
    if m.audio:
        d = m.audio
        return {**base, "ftype": "audio", "file_id": d.file_id, "unique_id": d.file_unique_id,
                "file_name": d.file_name or d.title, "file_size": d.file_size}
    if m.photo:
        d = m.photo[-1]
        return {**base, "ftype": "photo", "file_id": d.file_id, "unique_id": d.file_unique_id,
                "file_name": "Photo", "file_size": d.file_size}
    if m.voice:
        d = m.voice
        return {**base, "ftype": "voice", "file_id": d.file_id, "unique_id": d.file_unique_id,
                "file_name": "Voice message", "file_size": d.file_size}
    if m.animation:
        d = m.animation
        return {**base, "ftype": "animation", "file_id": d.file_id, "unique_id": d.file_unique_id,
                "file_name": d.file_name or "GIF", "file_size": d.file_size}
    if m.video_note:
        d = m.video_note
        return {**base, "ftype": "video_note", "file_id": d.file_id, "unique_id": d.file_unique_id,
                "file_name": "Video note", "file_size": d.file_size}
    if m.sticker:
        d = m.sticker
        return {**base, "ftype": "sticker", "file_id": d.file_id, "unique_id": d.file_unique_id,
                "file_name": "Sticker", "file_size": d.file_size}
    return None


async def send_item(bot, chat_id: int, it, protect: bool) -> Optional[int]:
    meth, param = SEND_MAP[it["ftype"]]
    kw: Dict[str, Any] = {param: it["file_id"], "protect_content": bool(protect)}
    attempts = []
    if it["ftype"] not in ("video_note", "sticker") and it["caption"]:
        kw["caption"] = it["caption"]
        kw["parse_mode"] = ParseMode.HTML
        attempts.append(dict(kw))
        k2 = dict(kw)
        k2["caption"] = plain(it["caption"])
        attempts.append(k2)
    else:
        attempts.append(kw)
    for k in attempts:
        try:
            m = await tg_retry(getattr(bot, meth), chat_id=chat_id, **k)
            return m.message_id
        except BadRequest:
            continue
    if it["storage_msg_id"] and STORAGE_CHANNEL_ID:
        try:
            m = await tg_retry(bot.copy_message, chat_id=chat_id, from_chat_id=STORAGE_CHANNEL_ID,
                               message_id=it["storage_msg_id"], protect_content=bool(protect))
            return m.message_id
        except TelegramError:
            pass
    return None


# ======================================================================
#  FORCE JOIN
# ======================================================================
_warned_fsub: set = set()


async def missing_channels(bot, uid: int) -> list:
    missing = []
    for r in await q_all("SELECT * FROM fsub"):
        try:
            m = await bot.get_chat_member(r["chat_id"], uid)
            ok = m.status in ("member", "administrator", "creator") or \
                 (m.status == "restricted" and getattr(m, "is_member", False))
            if not ok:
                missing.append(r)
        except (BadRequest, Forbidden) as e:
            log.warning("force-join check failed for %s: %s", r["chat_id"], e)
            if r["chat_id"] not in _warned_fsub:
                _warned_fsub.add(r["chat_id"])
                try:
                    await send(bot, OWNER_ID,
                               f"{E('warn')} <b>Force-join problem</b>\n\n"
                               f"I can't check membership for <code>{r['chat_id']}</code>.\n"
                               f"{E('info')} Make sure the bot is an <b>admin</b> in that channel.\n"
                               f"<code>{h(e)}</code>",
                               KB([B("Force-Join Settings", "A|fs", emoji="gear", style="primary")]))
                except TelegramError:
                    pass
        except TelegramError:
            pass
    return missing


def join_prompt(missing: list, code: str = ""):
    text = (f"{E('lock')} <b>Join required</b>\n\n"
            f"{E('mega')} To use this bot (and to open files) please join our channel"
            f"{'s' if len(missing) > 1 else ''} first, then tap <b>I've Joined</b>.")
    rows = []
    for r in missing:
        rows.append([B(f"Join {r['title'] or 'Channel'}", url=r["link"], emoji="mega", style="primary")])
    rows.append([B("I've Joined", f"chk|{code}", emoji="ok", style="success")])
    return text, InlineKeyboardMarkup(rows)


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs before every handler: registers users, bans, maintenance, force-join."""
    user, chat = update.effective_user, update.effective_chat
    if not user or user.is_bot or not chat or chat.type != ChatType.PRIVATE:
        return
    await touch_user(user)
    if is_admin(user.id):
        return
    row = await q_one("SELECT banned, ban_reason FROM users WHERE user_id=?", (user.id,))
    cb = update.callback_query
    if row and row["banned"]:
        if cb:
            await safe_answer(cb, "You are banned.", True)
        else:
            await send(context.bot, chat.id,
                       f"{E('stop')} <b>You are banned</b>\n\nYou can't use this bot."
                       + (f"\n{E('info')} Reason: {h(row['ban_reason'])}" if row["ban_reason"] else ""))
        raise ApplicationHandlerStop
    if S("maintenance") == "1":
        if cb:
            await safe_answer(cb, "Bot is under maintenance.", True)
        else:
            await send(context.bot, chat.id,
                       f"{E('tool')} <b>Maintenance mode</b>\n\nWe'll be back shortly. Please try again later.")
        raise ApplicationHandlerStop
    if cb and (cb.data or "").startswith("chk"):
        return
    missing = await missing_channels(context.bot, user.id)
    if missing:
        payload = ""
        msg = update.message
        if msg and msg.text and msg.text.startswith("/start"):
            parts = msg.text.split(maxsplit=1)
            if len(parts) > 1 and re.fullmatch(r"[A-Za-z0-9_-]{4,64}", parts[1].strip()):
                payload = parts[1].strip()
        text, kb = join_prompt(missing, payload)
        if cb:
            await safe_answer(cb, "Please join the channel first.", True)
        await send(context.bot, chat.id, text, kb)
        raise ApplicationHandlerStop


# ======================================================================
#  UI : HOME / HELP / STATS
# ======================================================================
def home_text(user) -> str:
    name = h(user.first_name or "friend")
    custom = S("welcome")
    if custom:
        return custom.replace("{name}", name)
    return (f"{E('wave')} <b>Welcome, {name}!</b>\n\n"
            f"{E('folder')} <b>Secure File Store</b>\n"
            f"Upload your files, get a private link and share it anywhere.\n\n"
            f"{E('key')} Protect links with a <b>password</b>\n"
            f"{E('eyes')} Set an <b>open limit</b> &amp; <b>expiry</b>\n"
            f"{E('hourglass')} <b>Auto-delete</b> delivered files\n"
            f"{E('shield')} <b>Content protection</b> (no forward / save)\n\n"
            f"{E('rocket')} Tap <b>Upload Files</b> to get started.")


def home_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [B("Upload Files", "up", emoji="rocket", style="success"),
         B("My Files", "mf|0", emoji="folder", style="primary")],
        [B("My Stats", "st", emoji="chart", style="primary"),
         B("Help", "help", emoji="bulb", style="primary")],
    ]
    last = [B("Our Channel", url=FORCE_CHANNEL_LINK, emoji="mega", style="primary")]
    if OWNER_USERNAME:
        last.append(B("Contact Owner", url=f"https://t.me/{OWNER_USERNAME}", emoji="chat", style="success"))
    rows.append(last)
    if is_admin(user_id):
        rows.append([B("Admin Panel", "A|dash", emoji="crown", style="danger")])
    return KB(*rows)


async def send_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await send(context.bot, update.effective_chat.id, home_text(u), home_kb(u.id))


HELP_TEXT = (
    f"{E('bulb')} <b>How it works</b>\n\n"
    f"{E('up')} <b>1. Upload</b> — tap <i>Upload Files</i>, send or forward your files, then tap <b>Done</b>.\n"
    f"{E('link')} <b>2. Get a link</b> — you instantly receive a private link for the whole batch.\n"
    f"{E('gear')} <b>3. Customize</b> — add a password, open limit, expiry, auto-delete or content protection.\n"
    f"{E('rocket')} <b>4. Share</b> — anyone who opens the link gets the files (after entering the password, if set).\n\n"
    f"{E('shield')} <b>Security</b>\n"
    f"{E('key')} Passwords are stored only as salted hashes — nobody can read them, not even admins.\n"
    f"{E('alarm')} Wrong passwords are rate-limited and temporarily locked.\n\n"
    f"{E('info')} Commands: /start /upload /myfiles /stats /cancel"
)


async def user_stats_text(uid: int) -> str:
    u = await q_one("SELECT * FROM users WHERE user_id=?", (uid,))
    st = await q_one("SELECT COUNT(*) l, COALESCE(SUM(opens),0) o, "
                     "SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) a FROM links WHERE owner_id=?", (uid,))
    fl = await q_one("SELECT COUNT(*) f, COALESCE(SUM(file_size),0) s FROM items "
                     "WHERE code IN (SELECT code FROM links WHERE owner_id=?)", (uid,))
    return (f"{E('chart')} <b>Your Statistics</b>\n\n"
            f"{E('cal')} Member since: <b>{fmt_ts(u['joined_at']) if u else '—'}</b>\n"
            f"{E('link')} Links: <b>{st['l']}</b> ({st['a'] or 0} active)\n"
            f"{E('folder')} Files stored: <b>{fl['f']}</b> · {human_size(fl['s'])}\n"
            f"{E('eyes')} Total opens received: <b>{st['o']}</b>")


BACK_HOME = KB([B("Back", "home", emoji="back", style="primary")])


# ======================================================================
#  CALLBACK ROUTER
# ======================================================================
ROUTES: Dict[str, Any] = {}
DONE = object()  # handler already answered the callback itself


def route(name: str):
    def deco(fn):
        ROUTES[name] = fn
        return fn
    return deco


async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = (q.data or "").split("|")
    fn = ROUTES.get(parts[0])
    if not fn:
        await safe_answer(q)
        return
    try:
        res = await fn(update, context, parts[1:])
    except ApplicationHandlerStop:
        raise
    except Exception:
        log.exception("callback error: %s", q.data)
        await safe_answer(q, "Something went wrong. Please try again.", True)
        return
    if res is DONE:
        return
    if isinstance(res, tuple):
        await safe_answer(q, res[0], res[1])
    else:
        await safe_answer(q)


@route("noop")
async def cb_noop(update, context, a):
    return None


@route("home")
async def cb_home(update, context, a):
    u = update.effective_user
    context.user_data.pop("aw", None)
    await show(context, update, home_text(u), home_kb(u.id))


@route("help")
async def cb_help(update, context, a):
    await show(context, update, HELP_TEXT, BACK_HOME)


@route("st")
async def cb_stats(update, context, a):
    await show(context, update, await user_stats_text(update.effective_user.id), BACK_HOME)


@route("cx")
async def cb_cancel_await(update, context, a):
    """Cancel a pending text prompt. cx|home | cx|ml|<code> | cx|adm"""
    context.user_data.pop("aw", None)
    target = a[0] if a else "home"
    if target == "ml" and len(a) > 1:
        return await cb_manage(update, context, [a[1]])
    if target == "adm":
        return await adm_dash(update, context, [])
    return await cb_home(update, context, [])


# ======================================================================
#  UPLOAD FLOW
# ======================================================================
def _new_session() -> Dict[str, Any]:
    return {"items": [], "mid": None, "v": 0, "closed": False, "full": False}


def upload_kb() -> InlineKeyboardMarkup:
    return KB([B("Done — Create Link", "upd", emoji="ok", style="success"),
               B("Cancel", "upc", emoji="cross", style="danger")])


def upload_text(up) -> str:
    n = len(up["items"])
    size = sum((i["file_size"] or 0) for i in up["items"])
    if n == 0:
        return (f"{E('up')} <b>Upload Mode</b>\n\n"
                f"{E('folder')} Send or forward me your files — documents, videos, photos, audio, voice, GIFs, "
                f"stickers. Albums work too.\n"
                f"{E('check')} When you're finished tap <b>Done</b>.\n\n"
                f"{E('info')} Max <b>{S('max_files')}</b> files per link.")
    return (f"{E('ok')} <b>{n}</b> file{'s' if n != 1 else ''} received · {human_size(size)}\n\n"
            f"{E('up')} Keep sending more, or tap <b>Done</b> to create your link.")


async def start_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    if S("uploads_open") != "1" and not is_admin(uid):
        await send(context.bot, chat_id, f"{E('lock')} <b>Uploads are currently closed.</b>\nPlease try again later.",
                   BACK_HOME)
        return
    ud = context.user_data
    ud.pop("aw", None)
    up = ud.get("up")
    if up and not up["closed"]:
        if up["mid"]:
            await safe_delete(context.bot, chat_id, up["mid"])
    else:
        up = ud["up"] = _new_session()
    m = await send(context.bot, chat_id, upload_text(up), upload_kb())
    up["mid"] = m.message_id


@route("up")
async def cb_upload(update, context, a):
    await safe_answer(update.callback_query)
    await start_upload(update, context)
    return DONE


@route("upc")
async def cb_upload_cancel(update, context, a):
    up = context.user_data.pop("up", None)
    if up:
        up["closed"] = True
    return await cb_home(update, context, [])


async def _status_later(bot, chat_id: int, up, v: int):
    await asyncio.sleep(0.8)
    if up["closed"] or up["v"] != v:
        return
    old = up["mid"]
    try:
        m = await send(bot, chat_id, upload_text(up), upload_kb())
        if up["closed"]:
            await safe_delete(bot, chat_id, m.message_id)
            return
        up["mid"] = m.message_id
        if old:
            await safe_delete(bot, chat_id, old)
    except TelegramError as e:
        log.warning("status update failed: %s", e)


async def add_to_session(update: Update, context: ContextTypes.DEFAULT_TYPE, it: Dict[str, Any]):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    if S("uploads_open") != "1" and not is_admin(uid):
        await send(context.bot, chat_id, f"{E('lock')} <b>Uploads are currently closed.</b>", BACK_HOME)
        return
    ud = context.user_data
    up = ud.get("up")
    if not up or up["closed"]:
        up = ud["up"] = _new_session()
    if len(up["items"]) >= to_int(S("max_files"), 50):
        if not up["full"]:
            up["full"] = True
            await send(context.bot, chat_id,
                       f"{E('warn')} <b>Limit reached</b> — max {S('max_files')} files per link.\n"
                       f"Tap <b>Done</b> to create your link.", upload_kb())
        return
    up["items"].append(it)
    up["v"] += 1
    asyncio.create_task(_status_later(context.bot, chat_id, up, up["v"]))


async def create_link(user_id: int, items: List[Dict[str, Any]]) -> str:
    autodel = to_int(S("default_autodel"), 0)
    code = ""
    for _ in range(8):
        code = secrets.token_urlsafe(6)
        try:
            await q_exec("INSERT INTO links(code,owner_id,created_at,autodel) VALUES(?,?,?,?)",
                         (code, user_id, now(), autodel))
            break
        except sqlite3.IntegrityError:
            code = ""
    if not code:
        raise RuntimeError("could not allocate link code")
    for pos, it in enumerate(items):
        await q_exec(
            "INSERT INTO items(code,pos,ftype,file_id,unique_id,file_name,file_size,caption) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (code, pos, it["ftype"], it["file_id"], it["unique_id"], it["file_name"],
             it["file_size"], it["caption"]))
    return code


async def mirror_to_storage(bot, code: str, user, items: List[Dict[str, Any]]):
    """Optional: copy uploads into a private storage channel (backup + audit)."""
    if not STORAGE_CHANNEL_ID:
        return
    try:
        await bot.send_message(STORAGE_CHANNEL_ID,
                               f"Link {code} | user {user.id} | {user.first_name or ''} "
                               f"@{user.username or '-'} | {len(items)} file(s)")
        rows = await q_all("SELECT id FROM items WHERE code=? ORDER BY pos", (code,))
        for row, it in zip(rows, items):
            try:
                mid = await tg_retry(bot.copy_message, chat_id=STORAGE_CHANNEL_ID,
                                     from_chat_id=it["src_chat"], message_id=it["src_mid"])
                await q_exec("UPDATE items SET storage_msg_id=? WHERE id=?", (mid.message_id, row["id"]))
            except TelegramError as e:
                log.warning("mirror failed: %s", e)
            await asyncio.sleep(0.1)
    except TelegramError as e:
        log.warning("storage channel error: %s", e)


@route("upd")
async def cb_upload_done(update, context, a):
    ud = context.user_data
    up = ud.get("up")
    user = update.effective_user
    if not up or not up["items"]:
        return ("Send at least one file first.", True)
    cnt = (await q_one("SELECT COUNT(*) c FROM links WHERE owner_id=? AND status='active'", (user.id,)))["c"]
    if cnt >= to_int(S("max_links"), 100) and not is_admin(user.id):
        return (f"Link limit reached ({S('max_links')}). Delete an old link first.", True)
    up["closed"] = True
    ud.pop("up", None)
    await safe_answer(update.callback_query)
    items = up["items"]
    code = await create_link(user.id, items)
    await log_act("upload", user.id, f"{code} · {len(items)} file(s)")
    if S("notify_uploads") == "1" and user.id != OWNER_ID:
        try:
            await send(context.bot, OWNER_ID,
                       f"{E('new')} <b>New upload</b>\n\n"
                       f"{E('user')} {mention(user.id, user.first_name)} · <code>{user.id}</code>\n"
                       f"{E('folder')} {len(items)} file(s) · <code>{code}</code>",
                       KB([B("Open Link", f"A|f|{code}", emoji="link", style="primary")]))
        except TelegramError:
            pass
    asyncio.create_task(mirror_to_storage(context.bot, code, user, items))
    text, kb = await render_panel(code)
    await show(context, update, text, kb)
    return DONE


# ======================================================================
#  LINK MANAGER
# ======================================================================
def link_text(l, n_files: int, total_size: int) -> str:
    title = h(l["title"]) if l["title"] else "Untitled"
    mx = l["max_opens"]
    limit = f"{l['opens']} / {mx}" if mx else f"{l['opens']} / ∞"
    exp = "Never" if not l["expires_at"] else f"{fmt_ts(l['expires_at'])} ({human_left(l['expires_at'])})"
    status = "" if l["status"] == "active" else f"\n{E('stop')} <b>Status: REVOKED</b>"
    return (f"{E('link')} <b>Link Manager</b> — {title}{status}\n\n"
            f"<code>{link_url(l['code'])}</code>\n\n"
            f"{E('folder')} Files: <b>{n_files}</b> · {human_size(total_size)}\n"
            f"{E('key')} Password: {onoff(l['pw_hash'])}\n"
            f"{E('eyes')} Opens: <b>{limit}</b>\n"
            f"{E('clock')} Expires: <b>{exp}</b>\n"
            f"{E('hourglass')} Auto-delete: <b>{human_dur(l['autodel'])}</b>\n"
            f"{E('shield')} Protect content: {onoff(l['protect'])}\n"
            f"{E('cal')} Created: {fmt_ts(l['created_at'])}")


def link_kb(l) -> InlineKeyboardMarkup:
    c = l["code"]
    share = ("https://t.me/share/url?url=" + quote(link_url(c)) + "&text=" + quote("Here are the files"))
    return KB(
        [B("Password", f"pw|{c}", emoji="key", style="primary"),
         B("Open Limit", f"lm|{c}", emoji="eyes", style="primary")],
        [B("Expiry", f"ex|{c}", emoji="clock", style="primary"),
         B("Auto-Delete", f"ad|{c}", emoji="hourglass", style="primary")],
        [B("Protect: " + ("ON" if l["protect"] else "OFF"), f"pr|{c}", emoji="shield",
           style="success" if l["protect"] else "primary"),
         B("Rename", f"rn|{c}", emoji="bookmark", style="primary")],
        [B("Share Link", url=share, emoji="rocket", style="success"),
         B("Get Files", f"vf|{c}", emoji="down", style="success")],
        [B("Delete", f"dl|{c}", emoji="trash", style="danger"),
         B("My Files", "mf|0", emoji="folder", style="primary")],
    )


async def render_panel(code: str):
    l = await q_one("SELECT * FROM links WHERE code=?", (code,))
    if not l:
        return f"{E('cross')} <b>Link not found.</b>", BACK_HOME
    st = await q_one("SELECT COUNT(*) n, COALESCE(SUM(file_size),0) s FROM items WHERE code=?", (code,))
    return link_text(l, st["n"], st["s"]), link_kb(l)


async def get_link_for(update: Update, code: str):
    uid = update.effective_user.id
    l = await q_one("SELECT * FROM links WHERE code=?", (code,))
    if not l or (l["owner_id"] != uid and not is_admin(uid)):
        return None
    return l


async def _panel_back(context, update, code: str):
    text, kb = await render_panel(code)
    await show(context, update, text, kb)


def _sub_back(code: str) -> List[InlineKeyboardButton]:
    return [B("Back", f"ml|{code}", emoji="back", style="primary")]


@route("ml")
async def cb_manage(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    context.user_data.pop("aw", None)
    await _panel_back(context, update, a[0])


# ---------- password ----------
@route("pw")
async def cb_pw(update, context, a):
    l = await get_link_for(update, a[0])
    if not l:
        return ("Link not found.", True)
    rows = [[B("Change Password" if l["pw_hash"] else "Set Password", f"pws|{a[0]}", emoji="key", style="success")]]
    if l["pw_hash"]:
        rows.append([B("Remove Password", f"pwr|{a[0]}", emoji="trash", style="danger")])
    rows.append(_sub_back(a[0]))
    await show(context, update,
               f"{E('key')} <b>Password Protection</b>\n\n"
               f"Status: {onoff(l['pw_hash'])}\n\n"
               f"{E('shield')} Passwords are saved only as <b>salted PBKDF2 hashes</b> — nobody can read them, "
               f"not even admins.\n"
               f"{E('alarm')} After {MAX_PW_FAILS} wrong attempts a user is locked out for "
               f"{PW_LOCK_SECS // 60} minutes.\n"
               f"{E('bulb')} Your password message is deleted from the chat automatically.",
               KB(*rows))


@route("pws")
async def cb_pw_set(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    q = update.callback_query
    context.user_data["aw"] = {"t": "set_pw", "code": a[0], "mid": q.message.message_id}
    await show(context, update,
               f"{E('key')} <b>Send the new password</b>\n\n"
               f"{E('info')} 4–64 characters. I'll delete your message right after saving it.",
               KB([B("Cancel", f"cx|ml|{a[0]}", emoji="cross", style="danger")]))


@route("pwr")
async def cb_pw_remove(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    await q_exec("UPDATE links SET pw_hash=NULL, pw_salt=NULL WHERE code=?", (a[0],))
    await q_exec("DELETE FROM pw_attempts WHERE code=?", (a[0],))
    await _panel_back(context, update, a[0])
    return ("Password removed.", False)


# ---------- open limit ----------
@route("lm")
async def cb_limit(update, context, a):
    l = await get_link_for(update, a[0])
    if not l:
        return ("Link not found.", True)
    c = a[0]
    opts = [("∞ Unlimited", 0), ("1", 1), ("5", 5), ("10", 10), ("25", 25), ("50", 50), ("100", 100), ("500", 500)]
    rows, row = [], []
    for label, n in opts:
        row.append(B(label, f"ls|{c}|{n}", emoji="eyes", style="success" if l["max_opens"] == n else "primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B("Custom", f"lmc|{c}", emoji="tool", style="primary")])
    rows.append(_sub_back(c))
    cur = f"{l['max_opens']}" if l["max_opens"] else "Unlimited"
    await show(context, update,
               f"{E('eyes')} <b>Open Limit</b>\n\n"
               f"Current: <b>{cur}</b> · used <b>{l['opens']}</b>\n\n"
               f"{E('info')} The link stops working after this many successful opens. "
               f"Your own opens are never counted.",
               KB(*rows))


@route("ls")
async def cb_limit_set(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    await q_exec("UPDATE links SET max_opens=? WHERE code=?", (max(0, to_int(a[1])), a[0]))
    await _panel_back(context, update, a[0])
    return ("Limit updated.", False)


@route("lmc")
async def cb_limit_custom(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    context.user_data["aw"] = {"t": "custom_limit", "code": a[0], "mid": update.callback_query.message.message_id}
    await show(context, update, f"{E('eyes')} <b>Send the open limit</b> as a number (1 – 1000000).",
               KB([B("Cancel", f"cx|ml|{a[0]}", emoji="cross", style="danger")]))


# ---------- expiry ----------
EXPIRY_OPTS = [("Never", 0), ("1 hour", 3600), ("6 hours", 21600), ("24 hours", 86400),
               ("3 days", 259200), ("7 days", 604800), ("30 days", 2592000)]


@route("ex")
async def cb_expiry(update, context, a):
    l = await get_link_for(update, a[0])
    if not l:
        return ("Link not found.", True)
    c = a[0]
    rows, row = [], []
    for label, secs in EXPIRY_OPTS:
        row.append(B(label, f"es|{c}|{secs}", emoji="clock", style="success" if secs == 0 and not l["expires_at"] else "primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(_sub_back(c))
    cur = "Never" if not l["expires_at"] else f"{fmt_ts(l['expires_at'])} ({human_left(l['expires_at'])})"
    await show(context, update,
               f"{E('clock')} <b>Link Expiry</b>\n\nCurrent: <b>{cur}</b>\n\n"
               f"{E('info')} Pick a duration — the timer starts <b>now</b>.", KB(*rows))


@route("es")
async def cb_expiry_set(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    secs = max(0, to_int(a[1]))
    await q_exec("UPDATE links SET expires_at=? WHERE code=?", (now() + secs if secs else 0, a[0]))
    await _panel_back(context, update, a[0])
    return ("Expiry updated.", False)


# ---------- auto delete ----------
AUTODEL_OPTS = [("Off", 0), ("30 sec", 30), ("1 min", 60), ("5 min", 300), ("10 min", 600),
                ("30 min", 1800), ("1 hour", 3600)]


@route("ad")
async def cb_autodel(update, context, a):
    l = await get_link_for(update, a[0])
    if not l:
        return ("Link not found.", True)
    c = a[0]
    rows, row = [], []
    for label, secs in AUTODEL_OPTS:
        row.append(B(label, f"as|{c}|{secs}", emoji="hourglass", style="success" if l["autodel"] == secs else "primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(_sub_back(c))
    await show(context, update,
               f"{E('hourglass')} <b>Auto-Delete</b>\n\nCurrent: <b>{human_dur(l['autodel'])}</b>\n\n"
               f"{E('info')} Files delivered to other users are deleted from their chat after this time.",
               KB(*rows))


@route("as")
async def cb_autodel_set(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    await q_exec("UPDATE links SET autodel=? WHERE code=?", (max(0, to_int(a[1])), a[0]))
    await _panel_back(context, update, a[0])
    return ("Auto-delete updated.", False)


# ---------- protect / rename ----------
@route("pr")
async def cb_protect(update, context, a):
    l = await get_link_for(update, a[0])
    if not l:
        return ("Link not found.", True)
    await q_exec("UPDATE links SET protect=? WHERE code=?", (0 if l["protect"] else 1, a[0]))
    await _panel_back(context, update, a[0])
    return ("Content protection " + ("disabled." if l["protect"] else "enabled."), False)


@route("rn")
async def cb_rename(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    context.user_data["aw"] = {"t": "rename", "code": a[0], "mid": update.callback_query.message.message_id}
    await show(context, update,
               f"{E('bookmark')} <b>Send a title</b> for this link (max 40 characters).\nSend <code>-</code> to clear it.",
               KB([B("Cancel", f"cx|ml|{a[0]}", emoji="cross", style="danger")]))


# ---------- get files / delete ----------
@route("vf")
async def cb_view_files(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    await safe_answer(update.callback_query)
    await deliver(update, context, a[0])
    return DONE


@route("dl")
async def cb_delete(update, context, a):
    if not await get_link_for(update, a[0]):
        return ("Link not found.", True)
    await show(context, update,
               f"{E('warn')} <b>Delete this link?</b>\n\n"
               f"The link and its file records will be removed permanently. This can't be undone.",
               KB([B("Yes, Delete", f"dly|{a[0]}", emoji="trash", style="danger"),
                   B("No, Keep", f"ml|{a[0]}", emoji="back", style="success")]))


@route("dly")
async def cb_delete_yes(update, context, a):
    l = await get_link_for(update, a[0])
    if not l:
        return ("Link not found.", True)
    await hard_delete_link(a[0])
    await log_act("delete", update.effective_user.id, a[0])
    await show(context, update, f"{E('ok')} <b>Link deleted.</b>",
               KB([B("My Files", "mf|0", emoji="folder", style="primary"),
                   B("Home", "home", emoji="back", style="primary")]))


async def hard_delete_link(code: str):
    await q_exec("DELETE FROM items WHERE code=?", (code,))
    await q_exec("DELETE FROM access_log WHERE code=?", (code,))
    await q_exec("DELETE FROM pw_attempts WHERE code=?", (code,))
    await q_exec("DELETE FROM links WHERE code=?", (code,))


# ---------- my files ----------
async def my_files_view(update, context, page: int):
    uid = update.effective_user.id
    total = (await q_one("SELECT COUNT(*) c FROM links WHERE owner_id=?", (uid,)))["c"]
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(max(page, 0), pages - 1)
    rows = await q_all(
        "SELECT l.*, (SELECT COUNT(*) FROM items i WHERE i.code=l.code) n FROM links l "
        "WHERE owner_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (uid, PER_PAGE, page * PER_PAGE))
    if not rows:
        await show(context, update,
                   f"{E('folder')} <b>My Files</b>\n\nYou haven't uploaded anything yet.\n"
                   f"{E('rocket')} Tap <b>Upload Files</b> to create your first link.",
                   KB([B("Upload Files", "up", emoji="rocket", style="success")],
                      [B("Back", "home", emoji="back", style="primary")]))
        return
    kb_rows = []
    for r in rows:
        name = r["title"] or r["code"]
        tag = "" if r["status"] == "active" else " [revoked]"
        lock = " 🔐" if r["pw_hash"] else ""
        kb_rows.append([B(f"{name} · {r['n']} file{'s' if r['n'] != 1 else ''}{lock}{tag}", f"ml|{r['code']}",
                          emoji="link", style="primary")])
    kb_rows.append(pager("mf", page, total))
    kb_rows.append([B("Upload Files", "up", emoji="rocket", style="success"),
                    B("Back", "home", emoji="back", style="primary")])
    await show(context, update,
               f"{E('folder')} <b>My Files</b> — {total} link{'s' if total != 1 else ''}\n\n"
               f"{E('pin')} Tap a link to manage it.", KB(*kb_rows))


@route("mf")
async def cb_my_files(update, context, a):
    await my_files_view(update, context, to_int(a[0] if a else 0))


# ======================================================================
#  DELIVERY (opening a shared link)
# ======================================================================
async def deliver(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str, after_pw: bool = False):
    bot = context.bot
    user = update.effective_user
    chat_id = update.effective_chat.id
    home_kb_ = KB([B("Home", "home", emoji="back", style="primary")])
    l = await q_one("SELECT * FROM links WHERE code=?", (code,))
    if not l or l["status"] != "active":
        await send(bot, chat_id, f"{E('cross')} <b>Link not found</b>\n\nThis link is invalid, revoked or deleted.",
                   home_kb_)
        return
    privileged = is_admin(user.id) or user.id == l["owner_id"]
    t = now()
    if not privileged:
        if l["expires_at"] and l["expires_at"] <= t:
            await q_exec("INSERT INTO access_log(code,user_id,ts,result) VALUES(?,?,?,?)", (code, user.id, t, "expired"))
            await send(bot, chat_id, f"{E('hourglass')} <b>Link expired</b>\n\nThis link is no longer available.", home_kb_)
            return
        if l["max_opens"] and l["opens"] >= l["max_opens"]:
            await q_exec("INSERT INTO access_log(code,user_id,ts,result) VALUES(?,?,?,?)", (code, user.id, t, "limit"))
            await send(bot, chat_id, f"{E('no')} <b>Limit reached</b>\n\nThis link has reached its open limit.", home_kb_)
            return
        if l["pw_hash"] and not after_pw:
            att = await q_one("SELECT * FROM pw_attempts WHERE code=? AND user_id=?", (code, user.id))
            if att and att["locked_until"] > t:
                await send(bot, chat_id,
                           f"{E('alarm')} <b>Too many wrong attempts</b>\n\n"
                           f"Try again in <b>{human_dur(att['locked_until'] - t)}</b>.", home_kb_)
                return
            context.user_data["aw"] = {"t": "enter_pw", "code": code}
            await send(bot, chat_id,
                       f"{E('lock')} <b>Password required</b>\n\n"
                       f"{E('key')} This link is protected. Send the password to unlock the files.",
                       KB([B("Cancel", "cx|home", emoji="cross", style="danger")]))
            return
        n = await q_exec("UPDATE links SET opens=opens+1 WHERE code=? AND (max_opens=0 OR opens<max_opens)", (code,))
        if n == 0:
            await send(bot, chat_id, f"{E('no')} <b>Limit reached</b>\n\nThis link has reached its open limit.", home_kb_)
            return
    items = await q_all("SELECT * FROM items WHERE code=? ORDER BY pos", (code,))
    if not items:
        await send(bot, chat_id, f"{E('cross')} <b>This link has no files.</b>", home_kb_)
        return
    notice = await send(bot, chat_id, f"{E('rocket')} <b>Sending {len(items)} file{'s' if len(items) != 1 else ''}…</b>")
    sent_ids: List[int] = []
    failed = 0
    for it in items:
        try:
            mid = await send_item(bot, chat_id, it, bool(l["protect"]) and not privileged)
        except Forbidden:
            return
        except TelegramError:
            mid = None
        if mid:
            sent_ids.append(mid)
        else:
            failed += 1
        await asyncio.sleep(0.05)
    await q_exec("INSERT INTO access_log(code,user_id,ts,result) VALUES(?,?,?,?)", (code, user.id, now(), "ok"))
    if not privileged:
        await log_act("open", user.id, code)
    lines = [f"{E('ok')} <b>Delivered {len(sent_ids)} file{'s' if len(sent_ids) != 1 else ''}</b>"]
    if failed:
        lines.append(f"{E('warn')} {failed} file{'s' if failed != 1 else ''} could not be sent.")
    delete_after = l["autodel"] if not privileged else 0
    if delete_after:
        lines.append(f"{E('hourglass')} These files will be deleted in <b>{human_dur(delete_after)}</b> — save them now.")
    if l["protect"] and not privileged:
        lines.append(f"{E('shield')} Content is protected (forwarding / saving is disabled).")
    final = await send(bot, chat_id, "\n".join(lines), home_kb_)
    if delete_after:
        due = now() + delete_after
        ids = sent_ids + [notice.message_id, final.message_id]
        for mid in ids:
            await q_exec("INSERT INTO autodel(chat_id,message_id,delete_at) VALUES(?,?,?)", (chat_id, mid, due))
    else:
        await safe_delete(bot, chat_id, notice.message_id)


@route("chk")
async def cb_check_join(update, context, a):
    q = update.callback_query
    missing = await missing_channels(context.bot, update.effective_user.id)
    if missing:
        return ("You haven't joined all the channels yet!", True)
    await safe_answer(q)
    if q.message:
        await safe_delete(context.bot, q.message.chat.id, q.message.message_id)
    code = a[0] if a else ""
    if code:
        await deliver(update, context, code)
    else:
        await send_home(update, context)
    return DONE


# ======================================================================
#  ADMIN PANEL
# ======================================================================
ADM: Dict[str, Any] = {}
OWNER_ONLY = {"adm", "admd", "adma", "set", "tg", "ns", "wl", "wlr", "bk"}


def adm(name: str):
    def deco(fn):
        ADM[name] = fn
        return fn
    return deco


@route("A")
async def cb_admin(update, context, a):
    uid = update.effective_user.id
    if not is_admin(uid):
        return ("Admins only.", True)
    sub = a[0] if a else "dash"
    if sub in OWNER_ONLY and uid != OWNER_ID:
        return ("Owner only.", True)
    fn = ADM.get(sub)
    if not fn:
        return None
    return await fn(update, context, a[1:])


ADM_BACK = KB([B("Admin Panel", "A|dash", emoji="crown", style="primary")])


def _adm_back_row(extra: Optional[List[InlineKeyboardButton]] = None):
    rows = []
    if extra:
        rows.append(extra)
    rows.append([B("Admin Panel", "A|dash", emoji="crown", style="primary")])
    return rows


@adm("dash")
async def adm_dash(update, context, a):
    context.user_data.pop("aw", None)
    day = now() - 86400
    u = await q_one("SELECT COUNT(*) c, COALESCE(SUM(banned),0) b, "
                    "COALESCE(SUM(CASE WHEN joined_at>? THEN 1 ELSE 0 END),0) n, "
                    "COALESCE(SUM(CASE WHEN last_seen>? THEN 1 ELSE 0 END),0) a FROM users", (day, day))
    l = await q_one("SELECT COUNT(*) c, COALESCE(SUM(CASE WHEN status='active' THEN 1 ELSE 0 END),0) a, "
                    "COALESCE(SUM(CASE WHEN pw_hash IS NOT NULL THEN 1 ELSE 0 END),0) p, "
                    "COALESCE(SUM(opens),0) o FROM links")
    f = await q_one("SELECT COUNT(*) c, COALESCE(SUM(file_size),0) s FROM items")
    w = await q_one("SELECT COUNT(*) c FROM access_log WHERE result='wrong_pw' AND ts>?", (day,))
    try:
        dbsize = human_size(os.path.getsize(DB_PATH))
    except OSError:
        dbsize = "—"
    text = (f"{E('crown')} <b>Admin Panel</b>\n\n"
            f"{E('user')} Users: <b>{u['c']}</b> · new 24h <b>{u['n']}</b> · active 24h <b>{u['a']}</b>\n"
            f"{E('stop')} Banned: <b>{u['b']}</b>\n"
            f"{E('link')} Links: <b>{l['a']}</b> active / {l['c']} total · {l['p']} password-protected\n"
            f"{E('folder')} Files: <b>{f['c']}</b> · {human_size(f['s'])}\n"
            f"{E('eyes')} Total opens: <b>{l['o']}</b>\n"
            f"{E('alarm')} Wrong passwords (24h): <b>{w['c']}</b>\n"
            f"{E('laptop')} DB: {dbsize} · {E('clock')} Uptime: {human_dur(int(time.time() - START_TS))}\n"
            f"{E('tool')} Maintenance: {onoff(S('maintenance') == '1')} · "
            f"Uploads: {onoff(S('uploads_open') == '1')}")
    kb = KB(
        [B("Users", "A|users|0", emoji="user", style="primary"),
         B("All Links", "A|links|0", emoji="link", style="primary")],
        [B("Search", "A|srch", emoji="search", style="primary"),
         B("Top Uploaders", "A|top", emoji="trophy", style="primary")],
        [B("Broadcast", "A|bc", emoji="speaker", style="success"),
         B("Activity Log", "A|log|0", emoji="trend", style="primary")],
        [B("Force-Join", "A|fs", emoji="mega", style="primary"),
         B("Admins", "A|adm", emoji="crown", style="primary")],
        [B("Settings", "A|set", emoji="sliders", style="primary"),
         B("Backup DB", "A|bk", emoji="down", style="success")],
        [B("Refresh", "A|dash", emoji="refresh", style="success"),
         B("Home", "home", emoji="back", style="danger")],
    )
    await show(context, update, text, kb)


# ---------- users ----------
@adm("users")
async def adm_users(update, context, a):
    page = to_int(a[0] if a else 0)
    total = (await q_one("SELECT COUNT(*) c FROM users"))["c"]
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(max(page, 0), pages - 1)
    rows = await q_all(
        "SELECT u.*, (SELECT COUNT(*) FROM links l WHERE l.owner_id=u.user_id) lc FROM users u "
        "ORDER BY last_seen DESC LIMIT ? OFFSET ?", (PER_PAGE, page * PER_PAGE))
    kb = []
    for r in rows:
        name = (r["first_name"] or "User")[:18] + (f" @{r['username']}" if r["username"] else "")
        mark = "[banned] " if r["banned"] else ""
        kb.append([B(f"{mark}{name} · {r['lc']} links", f"A|u|{r['user_id']}", emoji="user",
                     style="danger" if r["banned"] else "primary")])
    kb.append(pager("A|users", page, total))
    kb.append(_adm_back_row()[0])
    await show(context, update,
               f"{E('user')} <b>Users</b> — {total} total\n\n{E('pin')} Sorted by last activity. Tap a user for details.",
               KB(*kb))


async def user_profile(update, context, uid: int):
    u = await q_one("SELECT * FROM users WHERE user_id=?", (uid,))
    if not u:
        await show(context, update, f"{E('cross')} <b>User not found.</b>", ADM_BACK)
        return
    st = await q_one("SELECT COUNT(*) l, COALESCE(SUM(opens),0) o FROM links WHERE owner_id=?", (uid,))
    fl = await q_one("SELECT COUNT(*) f, COALESCE(SUM(file_size),0) s FROM items "
                     "WHERE code IN (SELECT code FROM links WHERE owner_id=?)", (uid,))
    ops = (await q_one("SELECT COUNT(*) c FROM access_log WHERE user_id=? AND result='ok'", (uid,)))["c"]
    role = "Owner" if uid == OWNER_ID else ("Admin" if is_admin(uid) else "User")
    text = (f"{E('user')} <b>User Profile</b>\n\n"
            f"{E('bookmark')} {mention(uid, u['first_name'])}"
            f"{' · @' + h(u['username']) if u['username'] else ''}\n"
            f"{E('key')} ID: <code>{uid}</code> · {role}\n"
            f"{E('cal')} Joined: {fmt_ts(u['joined_at'])}\n"
            f"{E('clock')} Last seen: {fmt_ts(u['last_seen'])}\n\n"
            f"{E('link')} Links: <b>{st['l']}</b> · opens received <b>{st['o']}</b>\n"
            f"{E('folder')} Files uploaded: <b>{fl['f']}</b> · {human_size(fl['s'])}\n"
            f"{E('eyes')} Links opened by them: <b>{ops}</b>\n"
            f"{E('stop')} Banned: {onoff(u['banned'])}"
            + (f"\n{E('info')} Reason: {h(u['ban_reason'])}" if u["ban_reason"] else "")
            + (f"\n{E('warn')} Blocked the bot" if u["blocked"] else ""))
    ban_btn = (B("Unban", f"A|unban|{uid}", emoji="ok", style="success") if u["banned"]
               else B("Ban", f"A|ban|{uid}", emoji="stop", style="danger"))
    rows = [
        [B("Their Links", f"A|ul|{uid}|0", emoji="link", style="primary"),
         B("Message", f"A|msg|{uid}", emoji="chat", style="success")],
    ]
    if uid != OWNER_ID and not is_admin(uid):
        rows.append([ban_btn, B("Revoke All Links", f"A|urev|{uid}", emoji="trash", style="danger")])
    rows.append([B("Users", "A|users|0", emoji="back", style="primary"),
                 B("Admin Panel", "A|dash", emoji="crown", style="primary")])
    await show(context, update, text, KB(*rows))


@adm("u")
async def adm_user(update, context, a):
    await user_profile(update, context, to_int(a[0]))


@adm("ban")
async def adm_ban(update, context, a):
    uid = to_int(a[0])
    if uid == OWNER_ID or is_admin(uid):
        return ("You can't ban an admin.", True)
    await q_exec("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
    await log_act("ban", update.effective_user.id, str(uid))
    try:
        await send(context.bot, uid, f"{E('stop')} <b>You have been banned</b> from this bot.")
    except TelegramError:
        pass
    await user_profile(update, context, uid)
    return ("User banned.", False)


@adm("unban")
async def adm_unban(update, context, a):
    uid = to_int(a[0])
    await q_exec("UPDATE users SET banned=0, ban_reason=NULL WHERE user_id=?", (uid,))
    await log_act("unban", update.effective_user.id, str(uid))
    try:
        await send(context.bot, uid, f"{E('ok')} <b>You have been unbanned.</b> Welcome back!")
    except TelegramError:
        pass
    await user_profile(update, context, uid)
    return ("User unbanned.", False)


@adm("urev")
async def adm_user_revoke_all(update, context, a):
    uid = to_int(a[0])
    n = await q_exec("UPDATE links SET status='revoked' WHERE owner_id=?", (uid,))
    await log_act("revoke_all", update.effective_user.id, f"{uid} ({n})")
    await user_profile(update, context, uid)
    return (f"{n} link(s) revoked.", False)


@adm("msg")
async def adm_msg_user(update, context, a):
    uid = to_int(a[0])
    context.user_data["aw"] = {"t": "msg_user", "uid": uid}
    await show(context, update,
               f"{E('chat')} <b>Send the message</b> for user <code>{uid}</code>.\n"
               f"{E('info')} Any content works — text, photo, file…",
               KB([B("Cancel", "cx|adm", emoji="cross", style="danger")]))


# ---------- links ----------
async def links_view(update, context, page: int, uid: Optional[int] = None):
    where = "WHERE l.owner_id=?" if uid else ""
    args: tuple = (uid,) if uid else ()
    total = (await q_one(f"SELECT COUNT(*) c FROM links l {where}", args))["c"]
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(max(page, 0), pages - 1)
    rows = await q_all(
        f"SELECT l.*, (SELECT COUNT(*) FROM items i WHERE i.code=l.code) n, "
        f"(SELECT first_name FROM users u WHERE u.user_id=l.owner_id) fn FROM links l {where} "
        f"ORDER BY l.created_at DESC LIMIT ? OFFSET ?", args + (PER_PAGE, page * PER_PAGE))
    kb = []
    for r in rows:
        tag = "" if r["status"] == "active" else "[revoked] "
        kb.append([B(f"{tag}{r['code']} · {r['n']}f · {(r['fn'] or 'User')[:14]}", f"A|f|{r['code']}",
                     emoji="link", style="primary" if r["status"] == "active" else "danger")])
    prefix = f"A|ul|{uid}" if uid else "A|links"
    kb.append(pager(prefix, page, total))
    if uid:
        kb.append([B("Profile", f"A|u|{uid}", emoji="user", style="primary"),
                   B("Admin Panel", "A|dash", emoji="crown", style="primary")])
    else:
        kb.append(_adm_back_row()[0])
    title = "Links of user " + str(uid) if uid else "All Links"
    await show(context, update, f"{E('link')} <b>{title}</b> — {total}\n\n{E('pin')} Tap a link to inspect it.", KB(*kb))


@adm("links")
async def adm_links(update, context, a):
    await links_view(update, context, to_int(a[0] if a else 0))


@adm("ul")
async def adm_user_links(update, context, a):
    await links_view(update, context, to_int(a[1] if len(a) > 1 else 0), to_int(a[0]))


async def admin_link_view(update, context, code: str):
    l = await q_one("SELECT * FROM links WHERE code=?", (code,))
    if not l:
        await show(context, update, f"{E('cross')} <b>Link not found.</b>", ADM_BACK)
        return
    owner = await q_one("SELECT * FROM users WHERE user_id=?", (l["owner_id"],))
    items = await q_all("SELECT * FROM items WHERE code=? ORDER BY pos", (code,))
    total = sum((i["file_size"] or 0) for i in items)
    lines = []
    for i in items[:8]:
        lines.append(f"{E(TYPE_ICON.get(i['ftype'], 'clip'))} {h((i['file_name'] or i['ftype'])[:34])} · {human_size(i['file_size'])}")
    if len(items) > 8:
        lines.append(f"… +{len(items) - 8} more")
    acc = await q_all(
        "SELECT a.*, u.first_name, u.username FROM access_log a LEFT JOIN users u ON u.user_id=a.user_id "
        "WHERE a.code=? ORDER BY a.id DESC LIMIT 6", (code,))
    acc_lines = []
    for r in acc:
        who = mention(r["user_id"], r["first_name"]) if r["user_id"] else "?"
        acc_lines.append(f"• {who} · {h(r['result'])} · {fmt_ts(r['ts'])}")
    mx = l["max_opens"]
    text = (f"{E('link')} <b>Link {h(code)}</b>"
            f"{'' if l['status'] == 'active' else '  ' + E('stop') + ' <b>REVOKED</b>'}\n\n"
            f"{E('user')} Owner: {mention(l['owner_id'], owner['first_name'] if owner else 'User')} · "
            f"<code>{l['owner_id']}</code>\n"
            f"{E('bookmark')} Title: {h(l['title']) if l['title'] else '—'}\n"
            f"{E('cal')} Created: {fmt_ts(l['created_at'])}\n"
            f"{E('key')} Password: {onoff(l['pw_hash'])} · {E('eyes')} Opens: <b>{l['opens']}/{mx if mx else '∞'}</b>\n"
            f"{E('clock')} Expires: {fmt_ts(l['expires_at']) if l['expires_at'] else 'Never'} · "
            f"{E('hourglass')} Auto-del: {human_dur(l['autodel'])}\n"
            f"{E('shield')} Protect: {onoff(l['protect'])}\n\n"
            f"{E('folder')} <b>Files ({len(items)} · {human_size(total)})</b>\n" + "\n".join(lines)
            + (f"\n\n{E('eyes')} <b>Recent access</b>\n" + "\n".join(acc_lines) if acc_lines else ""))
    rev_btn = (B("Restore", f"A|frev|{code}", emoji="ok", style="success") if l["status"] != "active"
               else B("Revoke", f"A|frev|{code}", emoji="stop", style="danger"))
    kb = KB(
        [B("Get Files", f"A|fget|{code}", emoji="down", style="success"), rev_btn],
        [B("Owner", f"A|u|{l['owner_id']}", emoji="user", style="primary"),
         B("Delete", f"A|fdel|{code}", emoji="trash", style="danger")],
        [B("All Links", "A|links|0", emoji="back", style="primary"),
         B("Admin Panel", "A|dash", emoji="crown", style="primary")],
    )
    await show(context, update, text, kb)


@adm("f")
async def adm_link(update, context, a):
    await admin_link_view(update, context, a[0])


@adm("fget")
async def adm_link_get(update, context, a):
    await safe_answer(update.callback_query, "Sending files…")
    await deliver(update, context, a[0])
    return DONE


@adm("frev")
async def adm_link_revoke(update, context, a):
    l = await q_one("SELECT status FROM links WHERE code=?", (a[0],))
    if not l:
        return ("Link not found.", True)
    new = "revoked" if l["status"] == "active" else "active"
    await q_exec("UPDATE links SET status=? WHERE code=?", (new, a[0]))
    await log_act("revoke" if new == "revoked" else "restore", update.effective_user.id, a[0])
    await admin_link_view(update, context, a[0])
    return ("Link " + new + ".", False)


@adm("fdel")
async def adm_link_delete(update, context, a):
    await show(context, update,
               f"{E('warn')} <b>Delete link {h(a[0])} permanently?</b>\n\nThis can't be undone.",
               KB([B("Yes, Delete", f"A|fdely|{a[0]}", emoji="trash", style="danger"),
                   B("No", f"A|f|{a[0]}", emoji="back", style="success")]))


@adm("fdely")
async def adm_link_delete_yes(update, context, a):
    await hard_delete_link(a[0])
    await log_act("admin_delete", update.effective_user.id, a[0])
    await show(context, update, f"{E('ok')} <b>Link deleted.</b>",
               KB([B("All Links", "A|links|0", emoji="link", style="primary"),
                   B("Admin Panel", "A|dash", emoji="crown", style="primary")]))


# ---------- search ----------
@adm("srch")
async def adm_search(update, context, a):
    context.user_data["aw"] = {"t": "search"}
    await show(context, update,
               f"{E('search')} <b>Search</b>\n\nSend a <b>user ID</b>, <b>@username</b>, or a <b>link code / URL</b>.",
               KB([B("Cancel", "cx|adm", emoji="cross", style="danger")]))


# ---------- top uploaders ----------
@adm("top")
async def adm_top(update, context, a):
    rows = await q_all(
        "SELECT u.user_id, u.first_name, u.username, COUNT(DISTINCT l.code) lc, COUNT(i.id) fc, "
        "COALESCE(SUM(i.file_size),0) sz FROM links l JOIN users u ON u.user_id=l.owner_id "
        "LEFT JOIN items i ON i.code=l.code GROUP BY u.user_id ORDER BY fc DESC LIMIT 10")
    lines = []
    for n, r in enumerate(rows, 1):
        lines.append(f"<b>{n}.</b> {mention(r['user_id'], r['first_name'])} — {r['lc']} links · "
                     f"{r['fc']} files · {human_size(r['sz'])}")
    kb = [[B(f"{n}. {(r['first_name'] or 'User')[:20]}", f"A|u|{r['user_id']}", emoji="user", style="primary")]
          for n, r in enumerate(rows[:5], 1)]
    kb.append(_adm_back_row()[0])
    await show(context, update,
               f"{E('trophy')} <b>Top Uploaders</b>\n\n" + ("\n".join(lines) if lines else "No uploads yet."), KB(*kb))


# ---------- activity ----------
@adm("log")
async def adm_log(update, context, a):
    page = to_int(a[0] if a else 0)
    per = 12
    total = (await q_one("SELECT COUNT(*) c FROM activity"))["c"]
    pages = max(1, (total + per - 1) // per)
    page = min(max(page, 0), pages - 1)
    rows = await q_all("SELECT a.*, u.first_name FROM activity a LEFT JOIN users u ON u.user_id=a.user_id "
                       "ORDER BY a.id DESC LIMIT ? OFFSET ?", (per, page * per))
    icon = {"upload": "up", "open": "eyes", "new_user": "new", "ban": "stop", "unban": "ok", "delete": "trash",
            "admin_delete": "trash", "revoke": "stop", "restore": "ok", "revoke_all": "stop", "wrong_pw": "alarm"}
    lines = []
    for r in rows:
        lines.append(f"{E(icon.get(r['kind'], 'pin'))} <b>{h(r['kind'])}</b> · "
                     f"{mention(r['user_id'], r['first_name'])} · {h(r['detail'])}\n"
                     f"<i>{fmt_ts(r['ts'])}</i>")
    kb = [pager("A|log", page, total, per), _adm_back_row()[0]]
    await show(context, update, f"{E('trend')} <b>Activity Log</b>\n\n" + ("\n".join(lines) if lines else "Nothing yet."),
               KB(*kb))


# ---------- broadcast ----------
@adm("bc")
async def adm_broadcast(update, context, a):
    context.user_data["aw"] = {"t": "broadcast"}
    n = (await q_one("SELECT COUNT(*) c FROM users WHERE banned=0 AND blocked=0"))["c"]
    await show(context, update,
               f"{E('speaker')} <b>Broadcast</b>\n\n"
               f"Send the message you want to deliver to <b>{n}</b> users.\n"
               f"{E('info')} Text, photos, videos, files, buttons and premium emoji are all copied exactly.",
               KB([B("Cancel", "cx|adm", emoji="cross", style="danger")]))


@adm("bcy")
async def adm_broadcast_go(update, context, a):
    bc = context.user_data.pop("bc", None)
    if not bc:
        return ("Nothing to send.", True)
    q = update.callback_query
    await safe_answer(q, "Broadcast started.")
    status = await send(context.bot, q.message.chat.id, f"{E('speaker')} <b>Broadcast starting…</b>")
    asyncio.create_task(run_broadcast(context.bot, q.message.chat.id, status.message_id, bc[0], bc[1]))
    await safe_delete(context.bot, q.message.chat.id, q.message.message_id)
    return DONE


async def run_broadcast(bot, admin_chat: int, status_mid: int, src_chat: int, src_mid: int):
    users = await q_all("SELECT user_id FROM users WHERE banned=0 AND blocked=0")
    total = len(users)
    sent = failed = blocked = 0
    t0 = time.time()
    for i, r in enumerate(users, 1):
        uid = r["user_id"]
        for attempt in range(2):
            try:
                await bot.copy_message(chat_id=uid, from_chat_id=src_chat, message_id=src_mid)
                sent += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(float(e.retry_after) + 1)
            except Forbidden:
                blocked += 1
                await q_exec("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
                break
            except TelegramError:
                failed += 1
                break
        await asyncio.sleep(0.05)
        if i % 40 == 0:
            try:
                await edit(bot, admin_chat, status_mid,
                           f"{E('speaker')} <b>Broadcasting…</b> {i}/{total}\n"
                           f"{E('ok')} {sent} · {E('stop')} {blocked} · {E('cross')} {failed}")
            except TelegramError:
                pass
    await log_act("broadcast", admin_chat, f"{sent}/{total}")
    try:
        await edit(bot, admin_chat, status_mid,
                   f"{E('party')} <b>Broadcast finished</b> in {human_dur(int(time.time() - t0)) if time.time() - t0 >= 1 else '1s'}\n\n"
                   f"{E('user')} Total: <b>{total}</b>\n{E('ok')} Delivered: <b>{sent}</b>\n"
                   f"{E('stop')} Blocked: <b>{blocked}</b>\n{E('cross')} Failed: <b>{failed}</b>", ADM_BACK)
    except TelegramError:
        pass


# ---------- force join ----------
@adm("fs")
async def adm_fsub(update, context, a):
    rows = await q_all("SELECT * FROM fsub ORDER BY added_at")
    kb = []
    lines = []
    for r in rows:
        lines.append(f"{E('mega')} <b>{h(r['title'])}</b> · <code>{r['chat_id']}</code>")
        kb.append([B(f"Remove {r['title'] or r['chat_id']}", f"A|fsd|{r['chat_id']}", emoji="trash", style="danger")])
    kb.append([B("Add Channel", "A|fsa", emoji="plus", style="success")])
    kb.append(_adm_back_row()[0])
    await show(context, update,
               f"{E('mega')} <b>Force-Join Channels</b>\n\n" + ("\n".join(lines) if lines else "No channel set — everyone can use the bot.")
               + f"\n\n{E('info')} The bot must be an <b>admin</b> in every channel so it can verify members.",
               KB(*kb))


@adm("fsa")
async def adm_fsub_add(update, context, a):
    context.user_data["aw"] = {"t": "fs_add"}
    await show(context, update,
               f"{E('plus')} <b>Add a channel</b>\n\nSend the <b>chat id</b> and the <b>invite link</b> separated by a space:\n"
               f"<code>-1001234567890 https://t.me/+AbCdEf</code>",
               KB([B("Cancel", "cx|adm", emoji="cross", style="danger")]))


@adm("fsd")
async def adm_fsub_del(update, context, a):
    await q_exec("DELETE FROM fsub WHERE chat_id=?", (to_int(a[0]),))
    await adm_fsub(update, context, [])
    return ("Channel removed.", False)


# ---------- admins (owner) ----------
@adm("adm")
async def adm_admins(update, context, a):
    rows = await q_all("SELECT a.*, u.first_name FROM admins a LEFT JOIN users u ON u.user_id=a.user_id")
    kb = []
    lines = [f"{E('crown')} Owner · <code>{OWNER_ID}</code>"]
    for r in rows:
        lines.append(f"{E('shield')} {mention(r['user_id'], r['first_name'])} · <code>{r['user_id']}</code>")
        kb.append([B(f"Remove {(r['first_name'] or str(r['user_id']))[:20]}", f"A|admd|{r['user_id']}",
                     emoji="trash", style="danger")])
    kb.append([B("Add Admin", "A|adma", emoji="plus", style="success")])
    kb.append(_adm_back_row()[0])
    await show(context, update, f"{E('crown')} <b>Admins</b>\n\n" + "\n".join(lines), KB(*kb))


@adm("adma")
async def adm_admin_add(update, context, a):
    context.user_data["aw"] = {"t": "admin_add"}
    await show(context, update, f"{E('plus')} <b>Send the Telegram user ID</b> of the new admin.",
               KB([B("Cancel", "cx|adm", emoji="cross", style="danger")]))


@adm("admd")
async def adm_admin_del(update, context, a):
    await q_exec("DELETE FROM admins WHERE user_id=?", (to_int(a[0]),))
    await load_admins()
    await adm_admins(update, context, [])
    return ("Admin removed.", False)


# ---------- settings (owner) ----------
async def settings_view(update, context):
    text = (f"{E('sliders')} <b>Settings</b>\n\n"
            f"{E('tool')} Maintenance mode: {onoff(S('maintenance') == '1')}\n"
            f"{E('up')} Uploads open: {onoff(S('uploads_open') == '1')}\n"
            f"{E('bell')} Upload alerts to owner: {onoff(S('notify_uploads') == '1')}\n"
            f"{E('folder')} Max files per link: <b>{S('max_files')}</b>\n"
            f"{E('link')} Max links per user: <b>{S('max_links')}</b>\n"
            f"{E('hourglass')} Default auto-delete: <b>{human_dur(to_int(S('default_autodel')))}</b>\n"
            f"{E('chat')} Custom welcome: {onoff(S('welcome'))}")
    kb = KB(
        [B("Maintenance", "A|tg|maintenance", emoji="tool", style="danger" if S("maintenance") == "1" else "primary"),
         B("Uploads", "A|tg|uploads_open", emoji="up", style="success" if S("uploads_open") == "1" else "danger")],
        [B("Upload Alerts", "A|tg|notify_uploads", emoji="bell", style="success" if S("notify_uploads") == "1" else "primary")],
        [B("Max Files", "A|ns|max_files", emoji="folder", style="primary"),
         B("Max Links", "A|ns|max_links", emoji="link", style="primary")],
        [B("Default Auto-Delete", "A|ns|default_autodel", emoji="hourglass", style="primary")],
        [B("Set Welcome", "A|wl", emoji="chat", style="success"),
         B("Reset Welcome", "A|wlr", emoji="refresh", style="danger")],
        [B("Admin Panel", "A|dash", emoji="crown", style="primary")],
    )
    await show(context, update, text, kb)


@adm("set")
async def adm_settings(update, context, a):
    context.user_data.pop("aw", None)
    await settings_view(update, context)


@adm("tg")
async def adm_toggle(update, context, a):
    key = a[0]
    if key not in ("maintenance", "uploads_open", "notify_uploads"):
        return None
    await set_setting(key, "0" if S(key) == "1" else "1")
    await settings_view(update, context)
    return ("Updated.", False)


@adm("ns")
async def adm_numeric(update, context, a):
    key = a[0]
    if key not in ("max_files", "max_links", "default_autodel"):
        return None
    hint = "seconds (0 = off)" if key == "default_autodel" else "a number (1 – 1000)"
    context.user_data["aw"] = {"t": "set_num", "key": key}
    await show(context, update, f"{E('sliders')} <b>Send the new value</b> for <code>{key}</code> — {hint}.",
               KB([B("Cancel", "A|set", emoji="cross", style="danger")]))


@adm("wl")
async def adm_welcome(update, context, a):
    context.user_data["aw"] = {"t": "set_welcome"}
    await show(context, update,
               f"{E('chat')} <b>Send the new welcome message</b>\n\n"
               f"{E('info')} Formatting and premium emoji are kept. Use <code>{{name}}</code> for the user's name.",
               KB([B("Cancel", "A|set", emoji="cross", style="danger")]))


@adm("wlr")
async def adm_welcome_reset(update, context, a):
    await set_setting("welcome", "")
    await settings_view(update, context)
    return ("Welcome message reset.", False)


# ---------- backup / restore (owner) ----------
def _copy_db(dst: str):
    src = sqlite3.connect(DB_PATH)
    out = sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


async def send_backup(bot, chat_id: int, reason: str = "Manual backup"):
    path = f"{DB_PATH}.backup"
    if os.path.exists(path):
        os.remove(path)
    await asyncio.to_thread(_copy_db, path)
    try:
        with open(path, "rb") as f:
            await bot.send_document(
                chat_id, f, filename=f"filestore_{datetime.now(TZ).strftime('%Y%m%d_%H%M')}.db",
                caption=f"{E('down')} <b>{h(reason)}</b>\n{E('info')} To restore: reply to this file with /restore",
                parse_mode=ParseMode.HTML)
    except BadRequest:
        with open(path, "rb") as f:
            await bot.send_document(chat_id, f, filename="filestore_backup.db", caption=plain(reason))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@adm("bk")
async def adm_backup(update, context, a):
    await safe_answer(update.callback_query, "Creating backup…")
    await send_backup(context.bot, update.effective_chat.id)
    return DONE


async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    if user.id != OWNER_ID:
        return
    rep = update.message.reply_to_message
    if not rep or not rep.document:
        await send(context.bot, chat_id,
                   f"{E('info')} Reply to a backup <b>.db</b> file with /restore to restore it.")
        return
    tmp = f"{DB_PATH}.restore"
    try:
        f = await rep.document.get_file()
        await f.download_to_drive(tmp)
        con = sqlite3.connect(tmp)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        if not ok or not {"users", "links", "items"} <= tables:
            raise ValueError("not a valid File Store backup")
        await db_close()
        for ext in ("-wal", "-shm"):
            try:
                os.remove(DB_PATH + ext)
            except OSError:
                pass
        os.replace(tmp, DB_PATH)
        await db_open()
        await load_settings()
        await load_admins()
        await send(context.bot, chat_id, f"{E('party')} <b>Database restored successfully.</b>")
    except Exception as e:  # noqa: BLE001
        log.exception("restore failed")
        if DB.conn is None:
            await db_open()
        try:
            os.remove(tmp)
        except OSError:
            pass
        await send(context.bot, chat_id, f"{E('cross')} <b>Restore failed</b>\n<code>{h(e)}</code>")


# ======================================================================
#  TEXT / MEDIA MESSAGES  (uploads + pending prompts)
# ======================================================================
async def handle_await(update: Update, context: ContextTypes.DEFAULT_TYPE, aw: Dict[str, Any]) -> bool:
    """Returns True if the message was consumed by a pending prompt."""
    m = update.message
    bot = context.bot
    ud = context.user_data
    user = update.effective_user
    chat_id = update.effective_chat.id
    t = aw["t"]
    text = (m.text or "").strip()

    # ----- enter password for a protected link -----
    if t == "enter_pw":
        if not text:
            await send(bot, chat_id, f"{E('key')} Please send the password as <b>text</b>.")
            return True
        code = aw["code"]
        l = await q_one("SELECT * FROM links WHERE code=?", (code,))
        if not l or l["status"] != "active" or not l["pw_hash"]:
            ud.pop("aw", None)
            await send(bot, chat_id, f"{E('cross')} <b>Link not found.</b>", BACK_HOME)
            return True
        att = await q_one("SELECT * FROM pw_attempts WHERE code=? AND user_id=?", (code, user.id))
        tnow = now()
        if att and att["locked_until"] > tnow:
            ud.pop("aw", None)
            await send(bot, chat_id,
                       f"{E('alarm')} <b>Locked</b> — try again in <b>{human_dur(att['locked_until'] - tnow)}</b>.",
                       BACK_HOME)
            return True
        await safe_delete(bot, chat_id, m.message_id)
        fails = att["fails"] if att and att["locked_until"] == 0 else 0
        if await check_pw(text[:128], l["pw_hash"], l["pw_salt"]):
            await q_exec("DELETE FROM pw_attempts WHERE code=? AND user_id=?", (code, user.id))
            ud.pop("aw", None)
            await deliver(update, context, code, after_pw=True)
            return True
        fails += 1
        await q_exec("INSERT INTO access_log(code,user_id,ts,result) VALUES(?,?,?,?)", (code, user.id, tnow, "wrong_pw"))
        await log_act("wrong_pw", user.id, code)
        if fails >= MAX_PW_FAILS:
            await q_exec("INSERT INTO pw_attempts(code,user_id,fails,locked_until) VALUES(?,?,?,?) "
                         "ON CONFLICT(code,user_id) DO UPDATE SET fails=excluded.fails, locked_until=excluded.locked_until",
                         (code, user.id, 0, tnow + PW_LOCK_SECS))
            ud.pop("aw", None)
            await send(bot, chat_id,
                       f"{E('alarm')} <b>Too many wrong attempts</b>\n\nYou're locked out for "
                       f"<b>{human_dur(PW_LOCK_SECS)}</b>.", BACK_HOME)
        else:
            await q_exec("INSERT INTO pw_attempts(code,user_id,fails,locked_until) VALUES(?,?,?,0) "
                         "ON CONFLICT(code,user_id) DO UPDATE SET fails=excluded.fails, locked_until=0",
                         (code, user.id, fails))
            await send(bot, chat_id,
                       f"{E('cross')} <b>Wrong password</b>\n\n{E('warn')} {MAX_PW_FAILS - fails} attempt"
                       f"{'s' if MAX_PW_FAILS - fails != 1 else ''} left. Try again:",
                       KB([B("Cancel", "cx|home", emoji="cross", style="danger")]))
        return True

    # ----- set / change password -----
    if t == "set_pw":
        l = await get_link_for(update, aw["code"])
        if not l or not text:
            ud.pop("aw", None)
            return True
        if not 4 <= len(text) <= 64:
            await send(bot, chat_id, f"{E('warn')} Password must be <b>4–64 characters</b>. Try again:")
            return True
        await safe_delete(bot, chat_id, m.message_id)
        digest, salt = await make_pw(text)
        await q_exec("UPDATE links SET pw_hash=?, pw_salt=? WHERE code=?", (digest, salt, aw["code"]))
        await q_exec("DELETE FROM pw_attempts WHERE code=?", (aw["code"],))
        ud.pop("aw", None)
        ptext, pkb = await render_panel(aw["code"])
        await edit_or_send(bot, chat_id, aw.get("mid"), ptext, pkb)
        await send(bot, chat_id, f"{E('lock')} <b>Password saved.</b> Your message was deleted for safety.")
        return True

    # ----- custom limit -----
    if t == "custom_limit":
        n = to_int(text, -1)
        if not 1 <= n <= 1_000_000:
            await send(bot, chat_id, f"{E('warn')} Send a number between <b>1</b> and <b>1000000</b>.")
            return True
        if await get_link_for(update, aw["code"]):
            await q_exec("UPDATE links SET max_opens=? WHERE code=?", (n, aw["code"]))
        ud.pop("aw", None)
        ptext, pkb = await render_panel(aw["code"])
        await edit_or_send(bot, chat_id, aw.get("mid"), ptext, pkb)
        return True

    # ----- rename -----
    if t == "rename":
        if not text:
            await send(bot, chat_id, f"{E('bookmark')} Send a title as text.")
            return True
        title = None if text == "-" else text[:40]
        if await get_link_for(update, aw["code"]):
            await q_exec("UPDATE links SET title=? WHERE code=?", (title, aw["code"]))
        ud.pop("aw", None)
        ptext, pkb = await render_panel(aw["code"])
        await edit_or_send(bot, chat_id, aw.get("mid"), ptext, pkb)
        return True

    # ----- everything below is admin-only -----
    if not is_admin(user.id):
        ud.pop("aw", None)
        return False

    if t == "broadcast":
        ud.pop("aw", None)
        n = (await q_one("SELECT COUNT(*) c FROM users WHERE banned=0 AND blocked=0"))["c"]
        ud["bc"] = (chat_id, m.message_id)
        await send(bot, chat_id,
                   f"{E('speaker')} <b>Ready to broadcast</b> to <b>{n}</b> users.\nThe message above is the preview.",
                   KB([B("Send Now", "A|bcy", emoji="rocket", style="success"),
                       B("Cancel", "cx|adm", emoji="cross", style="danger")]))
        return True

    if t == "msg_user":
        ud.pop("aw", None)
        try:
            await bot.copy_message(chat_id=aw["uid"], from_chat_id=chat_id, message_id=m.message_id)
            await send(bot, chat_id, f"{E('ok')} <b>Message delivered.</b>", ADM_BACK)
        except TelegramError as e:
            await send(bot, chat_id, f"{E('cross')} <b>Could not deliver</b>\n<code>{h(e)}</code>", ADM_BACK)
        return True

    if t == "search":
        ud.pop("aw", None)
        if not text:
            return True
        mm = re.search(r"start=([A-Za-z0-9_-]{4,64})", text)
        if mm:
            return await _search_link(update, context, mm.group(1))
        if text.lstrip("-").isdigit() and len(text) >= 5:
            await user_profile(update, context, int(text))
            return True
        if text.startswith("@"):
            r = await q_one("SELECT user_id FROM users WHERE lower(username)=lower(?)", (text[1:],))
            if r:
                await user_profile(update, context, r["user_id"])
            else:
                await send(bot, chat_id, f"{E('cross')} <b>No user with that username.</b>", ADM_BACK)
            return True
        return await _search_link(update, context, text)

    if t == "admin_add":
        if user.id != OWNER_ID:
            ud.pop("aw", None)
            return True
        uid = to_int(text, 0)
        if uid <= 0:
            await send(bot, chat_id, f"{E('warn')} Send a valid numeric user ID.")
            return True
        ud.pop("aw", None)
        await q_exec("INSERT OR IGNORE INTO admins(user_id,added_by,added_at) VALUES(?,?,?)", (uid, user.id, now()))
        await load_admins()
        try:
            await send(bot, uid, f"{E('crown')} <b>You are now an admin!</b> Use /admin to open the panel.")
        except TelegramError:
            pass
        await send(bot, chat_id, f"{E('ok')} <b>Admin added:</b> <code>{uid}</code>",
                   KB([B("Admins", "A|adm", emoji="crown", style="primary")]))
        return True

    if t == "fs_add":
        mm = re.match(r"^(-?\d{5,20})\s+(https?://\S+)$", text)
        if not mm:
            await send(bot, chat_id, f"{E('warn')} Format: <code>-1001234567890 https://t.me/+AbCdEf</code>")
            return True
        cid, link = int(mm.group(1)), mm.group(2)
        title = "Channel"
        warn = ""
        try:
            chat = await bot.get_chat(cid)
            title = chat.title or title
            me = await bot.get_chat_member(cid, bot.id)
            if me.status not in ("administrator", "creator"):
                warn = f"\n{E('warn')} The bot is not an admin there — membership checks may fail."
        except TelegramError:
            warn = f"\n{E('warn')} I couldn't access that chat. Add the bot as admin first."
        ud.pop("aw", None)
        await q_exec("INSERT OR REPLACE INTO fsub(chat_id,link,title,added_at) VALUES(?,?,?,?)",
                     (cid, link, title, now()))
        _warned_fsub.discard(cid)
        await send(bot, chat_id, f"{E('ok')} <b>Channel added:</b> {h(title)}{warn}",
                   KB([B("Force-Join", "A|fs", emoji="mega", style="primary")]))
        return True

    if t == "set_num" and user.id == OWNER_ID:
        n = to_int(text, -1)
        key = aw["key"]
        top = 86400 * 7 if key == "default_autodel" else 1000
        if n < 0 or n > top or (key != "default_autodel" and n < 1):
            await send(bot, chat_id, f"{E('warn')} Send a valid number.")
            return True
        ud.pop("aw", None)
        await set_setting(key, str(n))
        await send(bot, chat_id, f"{E('ok')} <b>Saved.</b>", KB([B("Settings", "A|set", emoji="sliders", style="primary")]))
        return True

    if t == "set_welcome" and user.id == OWNER_ID:
        if not m.text:
            await send(bot, chat_id, f"{E('warn')} Send the welcome message as text.")
            return True
        html_text = m.text_html
        try:
            await send(bot, chat_id, html_text.replace("{name}", h(user.first_name or "friend")))
        except BadRequest as e:
            await send(bot, chat_id, f"{E('cross')} <b>That message can't be used</b>\n<code>{h(e)}</code>")
            return True
        ud.pop("aw", None)
        await set_setting("welcome", html_text)
        await send(bot, chat_id, f"{E('ok')} <b>Welcome message saved</b> (preview above).",
                   KB([B("Settings", "A|set", emoji="sliders", style="primary")]))
        return True

    ud.pop("aw", None)
    return False


async def _search_link(update, context, code: str) -> bool:
    if await q_one("SELECT 1 FROM links WHERE code=?", (code,)):
        await admin_link_view(update, context, code)
    else:
        await send(context.bot, update.effective_chat.id, f"{E('cross')} <b>Nothing found.</b>", ADM_BACK)
    return True


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.message
    if not m:
        return
    aw = context.user_data.get("aw")
    if aw:
        if await handle_await(update, context, aw):
            return
    it = extract_item(m)
    if it:
        await add_to_session(update, context, it)
        return
    if m.text:
        await send(context.bot, update.effective_chat.id,
                   f"{E('bulb')} Send me files to store them, or use the menu below.", home_kb(update.effective_user.id))


# ======================================================================
#  COMMANDS
# ======================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("aw", None)
    if context.args:
        code = context.args[0].strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{4,64}", code):
            await deliver(update, context, code)
            return
    await send_home(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(context.bot, update.effective_chat.id, HELP_TEXT, BACK_HOME)


async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_upload(update, context)


async def cmd_myfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await my_files_view(update, context, 0)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send(context.bot, update.effective_chat.id, await user_stats_text(update.effective_user.id), BACK_HOME)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("aw", None)
    up = context.user_data.pop("up", None)
    if up:
        up["closed"] = True
    await send(context.bot, update.effective_chat.id, f"{E('ok')} <b>Cancelled.</b>", home_kb(update.effective_user.id))


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await adm_dash(update, context, [])


# ======================================================================
#  BACKGROUND TASKS / LIFECYCLE
# ======================================================================
_tasks: List[asyncio.Task] = []


async def autodel_loop(bot):
    while True:
        try:
            rows = await q_all("SELECT * FROM autodel WHERE delete_at<=? LIMIT 200", (now(),))
            for r in rows:
                await safe_delete(bot, r["chat_id"], r["message_id"])
            if rows:
                ids = [r["id"] for r in rows]
                await q_exec(f"DELETE FROM autodel WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
        except Exception:  # noqa: BLE001
            log.exception("autodel loop")
        await asyncio.sleep(5)


async def backup_loop(bot):
    if AUTO_BACKUP_HOURS <= 0:
        return
    while True:
        await asyncio.sleep(AUTO_BACKUP_HOURS * 3600)
        try:
            await send_backup(bot, OWNER_ID, "Automatic backup")
        except Exception:  # noqa: BLE001
            log.exception("auto backup")


async def post_init(app: Application):
    global BOT_USERNAME
    me = await app.bot.get_me()
    BOT_USERNAME = me.username
    await db_open()
    await load_settings()
    await seed_fsub()
    await load_admins()
    user_cmds = [BotCommand("start", "Home"), BotCommand("upload", "Upload files"),
                 BotCommand("myfiles", "My links"), BotCommand("stats", "My statistics"),
                 BotCommand("help", "How it works"), BotCommand("cancel", "Cancel current action")]
    try:
        await app.bot.set_my_commands(user_cmds)
        await app.bot.set_my_commands(
            user_cmds + [BotCommand("admin", "Admin panel"), BotCommand("restore", "Restore DB backup")],
            scope=BotCommandScopeChat(OWNER_ID))
    except TelegramError as e:
        log.warning("set_my_commands failed: %s", e)
    _tasks.append(asyncio.create_task(autodel_loop(app.bot)))
    _tasks.append(asyncio.create_task(backup_loop(app.bot)))
    log.info("Bot @%s is ready.", BOT_USERNAME)


async def post_shutdown(app: Application):
    for t in _tasks:
        t.cancel()
    await db_close()


_last_err_note = 0.0


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    global _last_err_note
    err = context.error
    if isinstance(err, Conflict):
        log.warning("Conflict: another instance is polling with this token.")
        return
    if isinstance(err, NetworkError) and not isinstance(err, BadRequest):
        log.warning("Network issue: %s", err)
        return
    log.error("Unhandled error", exc_info=err)
    if time.time() - _last_err_note > 60:
        _last_err_note = time.time()
        tb = "".join(traceback.format_exception(None, err, err.__traceback__))[-900:] if err else "?"
        try:
            await context.bot.send_message(OWNER_ID, f"⚠️ Bot error:\n{tb}")
        except TelegramError:
            pass


# ======================================================================
#  HEALTH ENDPOINT  (Render / UptimeRobot)
# ======================================================================
class HealthHandler(BaseHTTPRequestHandler):
    def _reply(self, with_body: bool):
        body = (f'{{"status":"ok","bot":"{BOT_USERNAME}","uptime":{int(time.time() - START_TS)}}}').encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._reply(True)

    def do_HEAD(self):  # noqa: N802
        self._reply(False)

    def log_message(self, *args):  # silence access logs
        pass


def start_health_server():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True, name="health").start()
    log.info("Health endpoint listening on :%s (/health)", PORT)


# ======================================================================
#  MAIN
# ======================================================================
def build_app() -> Application:
    app = (Application.builder().token(BOT_TOKEN).concurrent_updates(64)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    app.add_handler(TypeHandler(Update, gate), group=-1)
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start, filters=private))
    app.add_handler(CommandHandler("help", cmd_help, filters=private))
    app.add_handler(CommandHandler("upload", cmd_upload, filters=private))
    app.add_handler(CommandHandler("myfiles", cmd_myfiles, filters=private))
    app.add_handler(CommandHandler("stats", cmd_stats, filters=private))
    app.add_handler(CommandHandler("cancel", cmd_cancel, filters=private))
    app.add_handler(CommandHandler("admin", cmd_admin, filters=private))
    app.add_handler(CommandHandler("restore", cmd_restore, filters=private))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    return app


def main():
    if not BOT_TOKEN:
        sys.exit("BOT_TOKEN is missing. Add it to your .env file / Render environment variables.")
    start_health_server()
    app = build_app()
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
