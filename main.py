#!/usr/bin/env python3
"""
=============================================================
   🔥 ULTIMATE TELEGRAM FIREBASE & APK EXTRACTOR BOT 🔥
=============================================================
"""

import base64
import codecs
from concurrent.futures import ThreadPoolExecutor
import html
import json
import logging
import os
import random
import re
import secrets
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bson.json_util import dumps as bson_dumps
from pymongo import MongoClient, ReturnDocument

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION & LOGGING
# ═══════════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("firebase-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8216354175:AAH_pilA68YbcWAE1vgXcwmlZK92loHaJvU").strip()
MONGO_URI = os.getenv("MONGODB_URI", "mongodb+srv://Tokishaki:Shadow123@cluster0.2dtvfy1.mongodb.net/?appName=Cluster0").strip()
ADMIN_IDS = {int(x) for x in os.getenv("TELEGRAM_ADMIN_ID", "5759232282").split(",") if x.strip().isdigit()}
SUPPORT_URL = os.getenv("SUPPORT_GROUP_URL", "https://t.me/+bOhnrl3FeJk5ODll").strip()

def env_int(name, default=0):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        LOG.warning("Ignoring invalid integer environment variable %s", name)
        return default

# Dump Channels (Configurable via Environment Variables or Admin)
USER_DUMP_CHANNEL = env_int("USER_DUMP_CHANNEL")
ADMIN_DUMP_CHANNEL = env_int("ADMIN_DUMP_CHANNEL")
BACKUP_INTERVAL_SECONDS = 24 * 60 * 60

API = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
FIREBASE_REGEX = re.compile(r"(^|\.)firebaseio\.com$|(^|\.)firebasedatabase\.app$|(^|\.)firebaseapp\.com$", re.I)

MAX_FILE = 100 * 1024 * 1024  # Extended to 100MB File Limit
MAX_ENTRY = 50 * 1024 * 1024
MAX_SCAN = 100 * 1024 * 1024
MAX_VIEWER_SCAN = 5 * 1024 * 1024
MAX_VIEWER_RESPONSE_BYTES = 100 * 1024 * 1024
MAX_VIEWER_ENTRY_BYTES = 2 * 1024 * 1024
MAX_VIEWER_TARGETS = min(10000, max(1, env_int("MAX_VIEWER_TARGETS", 10000)))
MAX_VIEWER_RESULT_SESSIONS = 64
MAX_PENDING_APKS_PER_USER = max(1, env_int("MAX_PENDING_APKS_PER_USER", 3))
MAX_QUEUED_WORK = max(8, env_int("MAX_QUEUED_WORK", 64))
START_REPLY_DELAY_SECONDS = 0
CALLBACK_REPLY_DELAY_SECONDS = 0
MAX_ARCHIVE_ENTRIES = 10000
MAX_PRINTABLE_SEGMENTS = 10000

mongo = db = users = channels = admins = orders = firebase = events = settings = None
fallback_users = {}
fallback_channels = []
fallback_admins = set()
fallback_orders = {}
fallback_firebase = {}
fallback_events = []
fallback_dump_sent = set()
fallback_settings = {
    "welcome_bonus": 10,
    "refer_bonus": 5,
    "cost_per_extract": 1,
    "qr_file_id": "",
    "welcome_image": "",
    "panel_image": "",
    "viewer_sticker": "",
    "dump_channel_id": "",
    "dump_channel_title": "",
    "welcome_text": "Your Firebase extraction workspace is ready.",
}
captcha_challenges = {}
verified_users = set()
BOT_USERNAME = "your_bot"
WORKERS = ThreadPoolExecutor(max_workers=16, thread_name_prefix="bot-worker")
WORK_DISPATCH_SLOTS = threading.BoundedSemaphore(MAX_QUEUED_WORK)
CALLBACK_WORKERS = ThreadPoolExecutor(max_workers=16, thread_name_prefix="bot-callback")
CALLBACK_DISPATCH_SLOTS = threading.BoundedSemaphore(64)
CALLBACK_ACK_WORKERS = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bot-callback-ack")
CALLBACK_ACK_SLOTS = threading.BoundedSemaphore(64)
EXTRACTION_SLOTS = threading.BoundedSemaphore(4)
VIEWER_SLOTS = threading.BoundedSemaphore(6)
LIMIT_LOCK = threading.Lock()
MEMBERSHIP_CACHE_LOCK = threading.Lock()
MEMBERSHIP_CACHE_TTL_SECONDS = 30
MAX_MEMBERSHIP_CACHE_ENTRIES = 4096
membership_cache = {}
active_extraction_users = set()
active_viewer_users = set()
user_update_locks = {}
MAX_DAILY_APKS = 100
last_poll_at = time.monotonic()
EXTRACT_WAIT_SECONDS = 90
extract_timeout_timers = {}
pending_media_groups = {}
media_group_timers = {}
viewer_result_pages = {}
apk_queues = {}
apk_queue_workers = set()

DEVICE_PATH_KEYS = ("All_User", "all_user", "All_Users", "bots", "devices", "users", "clients", "agents")
PHONE_FIELD_KEYS = ("mobNo", "mobileNumber", "phoneNumber", "mobile", "phone", "number", "mno", "contact", "sim", "sim1", "sim2", "simNo", "simNumber")
ONLINE_KEYS = ("online", "isOnline", "active", "is_active", "on_off", "state", "status", "Status")

def now(): return datetime.now(timezone.utc)
def esc(value): return html.escape(str(value), quote=False)
def btn(text, data, style=None):
    button = {"text": text, "callback_data": data}
    if style:
        button["style"] = style
    return button
def kb(rows): return {"inline_keyboard": rows}
def is_private_chat(message): return (message.get("chat") or {}).get("type") == "private"

def submit_work(function, *args):
    """Keep Telegram update bursts from creating an unbounded work queue."""
    if not WORK_DISPATCH_SLOTS.acquire(blocking=False):
        LOG.warning("Dropping background task because the work queue is full")
        return False

    def runner():
        try:
            function(*args)
        except Exception:
            LOG.exception("Background task failed: %s", getattr(function, "__name__", function))
        finally:
            WORK_DISPATCH_SLOTS.release()

    try:
        WORKERS.submit(runner)
    except Exception:
        WORK_DISPATCH_SLOTS.release()
        LOG.exception("Could not submit background task")
        return False
    return True

def submit_user_work(function, payload):
    """Serialize updates from one Telegram user without blocking polling."""
    if isinstance(payload, dict) and payload.get("from"):
        uid = payload["from"].get("id")
    elif isinstance(payload, list) and payload and payload[0].get("from"):
        uid = payload[0]["from"].get("id")
    else:
        uid = None
    if not uid:
        return submit_work(function, payload)
    with LIMIT_LOCK:
        lock = user_update_locks.setdefault(uid, threading.RLock())

    def run_serialized():
        with lock:
            function(payload)

    return submit_work(run_serialized)

def submit_callback(query):
    msg = query.get("message", {})
    if not CALLBACK_DISPATCH_SLOTS.acquire(blocking=False):
        submit_callback_ack(query, "Bot is busy. Please try again in a moment.")
        LOG.warning("Dropping callback because the callback queue is full")
        return False

    submit_callback_ack(query, "" if is_private_chat(msg) else "Please use the bot in private chat.")
    submitted_at = time.monotonic()

    def runner():
        started_at = time.monotonic()
        try:
            if is_private_chat(msg):
                callback(query, callback_answered=True)
        except Exception:
            LOG.exception("Callback task failed: %s", query.get("data", ""))
        finally:
            elapsed = time.monotonic() - started_at
            if elapsed >= 2:
                action = query.get("data", "").split(":", 1)[0]
                LOG.warning(
                    "Slow button callback action=%s queue_wait=%.2fs duration=%.2fs",
                    action,
                    started_at - submitted_at,
                    elapsed,
                )
            CALLBACK_DISPATCH_SLOTS.release()

    try:
        CALLBACK_WORKERS.submit(runner)
    except Exception:
        CALLBACK_DISPATCH_SLOTS.release()
        LOG.exception("Could not submit callback task")
        answer(query, "Bot is busy. Please try again in a moment.")
        return False
    return True

def submit_callback_ack(query, text):
    if not CALLBACK_ACK_SLOTS.acquire(blocking=False):
        LOG.warning("Could not acknowledge callback because the acknowledgment queue is full")
        return False

    def runner():
        started_at = time.monotonic()
        try:
            answer(query, text)
        finally:
            elapsed = time.monotonic() - started_at
            if elapsed >= 2:
                LOG.warning("Slow Telegram callback acknowledgment: %.2fs", elapsed)
            CALLBACK_ACK_SLOTS.release()

    try:
        CALLBACK_ACK_WORKERS.submit(runner)
    except Exception:
        CALLBACK_ACK_SLOTS.release()
        LOG.exception("Could not submit callback acknowledgment")
        return False
    return True

# ═══════════════════════════════════════════════════════════════
# TELEGRAM API WRAPPERS
# ═══════════════════════════════════════════════════════════════
def tg(method, payload=None, timeout=35):
    if not API: raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    req = Request(f"{API}/{method}", data=json.dumps(payload or {}).encode(), headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as response: result = json.loads(response.read().decode())
    if not result.get("ok"): raise RuntimeError(result.get("description", f"{method} failed"))
    return result.get("result")

def delete_message(chat_id, message_id):
    try:
        tg("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception:
        LOG.debug("Unable to delete Telegram message %s", message_id, exc_info=True)

def send_document(chat_id, file_path):
    boundary = "----FirebaseBackupBoundary"
    content = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode()]
    mime_type = "text/plain" if Path(file_path).suffix.lower() in {".txt", ".text", ".log"} else "application/json"
    content.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{Path(file_path).name}\"\r\nContent-Type: {mime_type}\r\n\r\n".encode() + Path(file_path).read_bytes() + b"\r\n")
    content.append(f"--{boundary}--\r\n".encode())
    with urlopen(Request(f"{API}/sendDocument", data=b"".join(content), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=35) as response:
        result = json.loads(response.read().decode())
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram file upload failed"))
    return result.get("result")

def backup_admin_ids():
    recipients = set(ADMIN_IDS)
    if admins is not None:
        recipients.update(item["user_id"] for item in admins.find({}, {"user_id": 1}) if item.get("user_id"))
    return recipients

def send_database_backup():
    if db is None:
        LOG.warning("Database backup skipped because MongoDB is not connected")
        return
    backup_path = None
    try:
        backup = {"created_at": now().isoformat(), "database": db.name, "collections": {}}
        for collection_name in db.list_collection_names():
            backup["collections"][collection_name] = list(db[collection_name].find({}))
        with tempfile.NamedTemporaryFile(prefix="firebase-backup-", suffix=".json", mode="w", encoding="utf-8", delete=False) as handle:
            backup_path = Path(handle.name)
            handle.write(bson_dumps(backup, indent=2))
        recipients = backup_admin_ids()
        for admin_id in recipients:
            try:
                send_document(admin_id, backup_path)
            except Exception:
                LOG.exception("Database backup delivery failed for admin %s", admin_id)
        LOG.info("MongoDB backup sent to %d admin(s)", len(recipients))
    except Exception:
        LOG.exception("MongoDB backup generation failed")
    finally:
        if backup_path:
            backup_path.unlink(missing_ok=True)

def backup_loop():
    while True:
        time.sleep(BACKUP_INTERVAL_SECONDS)
        send_database_backup()

def send(chat, text, markup=None, **extra):
    payload = {"chat_id": chat, "text": text[:4090], "parse_mode": "HTML", "disable_web_page_preview": True, **extra}
    if markup: payload["reply_markup"] = markup
    return tg("sendMessage", payload)

def send_sticker(chat, sticker):
    return tg("sendSticker", {"chat_id": chat, "sticker": sticker})

def send_photo(chat, photo, caption, markup=None):
    payload = {
        "chat_id": chat,
        "photo": photo,
        "caption": caption[:1024],
        "parse_mode": "HTML",
    }
    if markup:
        payload["reply_markup"] = markup
    return tg("sendPhoto", payload)

def edit_caption(chat, message_id, caption, markup=None):
    payload = {
        "chat_id": chat,
        "message_id": message_id,
        "caption": caption[:1024],
        "parse_mode": "HTML",
    }
    if markup is not None:
        payload["reply_markup"] = markup
    return tg("editMessageCaption", payload)

def edit(chat, message_id, text, markup=None):
    payload = {"chat_id": chat, "message_id": message_id, "text": text[:4090], "parse_mode": "HTML"}
    if markup is not None: payload["reply_markup"] = markup
    try:
        return tg("editMessageText", payload)
    except Exception as exc:
        # Telegram returns this when Verify is tapped without changing
        # anything. Do not create a duplicate message in that case.
        if "message is not modified" in str(exc).lower():
            return {"message_id": message_id}
        # A welcome screen can be a photo with a caption. Telegram does not
        # allow editMessageText on media messages, but the caption and its
        # inline keyboard can be edited in place.
        if message_id:
            try:
                return edit_caption(chat, message_id, text, markup)
            except Exception:
                pass
        if message_id:
            delete_message(chat, message_id)
        return send(chat, text, markup)

def answer(query, text=""):
    try: tg("answerCallbackQuery", {"callback_query_id": query["id"], "text": text[:190]}, timeout=5)
    except Exception: pass

# ═══════════════════════════════════════════════════════════════
# DATABASE INITIALIZATION
# ═══════════════════════════════════════════════════════════════
def setup_db():
    global mongo, db, users, channels, admins, orders, firebase, events, settings
    if not MONGO_URI:
        LOG.warning("MONGODB_URI not found. Running in ephemeral/fallback mode.")
        return
    try:
        mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
        mongo.admin.command("ping")
        db = mongo[os.getenv("MONGODB_DB", "firebase_inventory_bot")]
        users, channels, admins = db.users, db.channels, db.admins
        orders, firebase, events, settings = db.orders, db.firebase_urls, db.events, db.settings
        users.create_index("user_id", unique=True); channels.create_index("chat_id", unique=True)
        admins.create_index("user_id", unique=True); orders.create_index("order_id", unique=True)
        firebase.create_index("url", unique=True)
        for uid in ADMIN_IDS: admins.update_one({"user_id": uid}, {"$setOnInsert": {"user_id": uid}}, upsert=True)
        settings.update_one({"_id": "config"}, {"$setOnInsert": {
            "welcome_bonus": 10, "refer_bonus": 5, "cost_per_extract": 1, "qr_file_id": "", "welcome_image": "",
            "panel_image": "", "viewer_sticker": "", "welcome_text": "Your Firebase extraction workspace is ready."
        }}, upsert=True)
    except Exception:
        LOG.exception("MongoDB connection failed. Running in ephemeral/fallback mode.")
        mongo = db = users = channels = admins = orders = firebase = events = settings = None

def fallback_user(uid, user=None):
    uid = int(uid)
    record = fallback_users.setdefault(uid, {
        "user_id": uid,
        "first_name": "",
        "username": "",
        "diamonds": 0,
        "referrals": 0,
        "extracts": 0,
        "apk_extracts": 0,
        "web_extracts": 0,
        "welcome_awarded": False,
        "referral_rewarded": False,
        "created_at": now(),
    })
    if user:
        record["first_name"] = user.get("first_name", record.get("first_name", ""))
        record["username"] = user.get("username", record.get("username", ""))
    return record

def cfg():
    return settings.find_one({"_id": "config"}) or {} if settings is not None else fallback_settings

def is_admin(uid):
    if uid in ADMIN_IDS or uid in fallback_admins:
        return True
    return admins is not None and admins.count_documents({"user_id": uid}, limit=1) > 0

def ensure_user(user):
    uid = int(user["id"])
    if users is not None:
        users.update_one({"user_id": uid}, {"$set": {"first_name": user.get("first_name", ""), "username": user.get("username", "")},
            "$setOnInsert": {"user_id": uid, "diamonds": 0, "referrals": 0, "extracts": 0, "apk_extracts": 0, "web_extracts": 0,
                             "welcome_awarded": False, "referral_rewarded": False, "created_at": now()}}, upsert=True)
        return users.find_one({"user_id": uid}) or {}
    return fallback_user(uid, user)

def is_banned(uid):
    user = users.find_one({"user_id": uid}, {"banned": 1}) if users is not None else fallback_user(uid)
    return bool((user or {}).get("banned"))

def claim_apk_slot(uid):
    today = now().date().isoformat()
    with LIMIT_LOCK:
        if users is not None:
            user = users.find_one({"user_id": uid}, {"apk_daily_date": 1}) or {}
            if user.get("apk_daily_date") != today:
                users.update_one({"user_id": uid}, {"$set": {"apk_daily_date": today, "apk_daily_count": 0}})
            result = users.update_one(
                {"user_id": uid, "apk_daily_date": today, "apk_daily_count": {"$lt": MAX_DAILY_APKS}},
                {"$inc": {"apk_daily_count": 1}},
            )
            return result.modified_count > 0
        user = fallback_user(uid)
        if user.get("apk_daily_date") != today:
            user["apk_daily_date"], user["apk_daily_count"] = today, 0
        if user.get("apk_daily_count", 0) >= MAX_DAILY_APKS:
            return False
        user["apk_daily_count"] = user.get("apk_daily_count", 0) + 1
        return True

def uname(user):
    return f"@{user.get('username')}" if user.get("username") else user.get("first_name", str(user.get("user_id", "")))

def session(uid, value=None):
    if users is not None:
        users.update_one({"user_id": uid}, {"$set": {"session": value}} if value is not None else {"$unset": {"session": ""}})
    else:
        user = fallback_user(uid)
        if value is None:
            user.pop("session", None)
        else:
            user["session"] = value

def current_session(uid):
    user = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
    return (user or {}).get("session")

def cancel_extract_timeout(uid):
    timer = extract_timeout_timers.pop(uid, None)
    if timer:
        timer.cancel()

def extraction_timeout(chat, uid, message_id, started_at):
    state = current_session(uid)
    if not isinstance(state, dict) or state.get("action") != "extract":
        return
    if state.get("started_at") != started_at:
        return
    cancel_extract_timeout(uid)
    session(uid)
    welcome(chat, uid, message_id)
    send(
        chat,
        "⚠️ You are not sending any file or link, so I’m going back to home. "
        "Click a button on the welcome screen to use me.",
        disable_notification=True,
    )

def schedule_extract_timeout(chat, uid, message_id, started_at):
    cancel_extract_timeout(uid)
    timer = threading.Timer(EXTRACT_WAIT_SECONDS, extraction_timeout, args=(chat, uid, message_id, started_at))
    timer.daemon = True
    extract_timeout_timers[uid] = timer
    timer.start()

def get_captcha(uid):
    if users is not None:
        user = users.find_one({"user_id": uid}) or {}
        challenge = user.get("captcha")
        if challenge:
            return challenge
    return captcha_challenges.get(uid, {})

def is_verified(uid):
    if users is not None:
        return bool((users.find_one({"user_id": uid}) or {}).get("verified"))
    return uid in verified_users or bool(fallback_user(uid).get("verified"))

def channels_list(): return list(channels.find().sort("created_at", 1)) if channels is not None else list(fallback_channels)

def channel_member_status(channel, uid):
    try:
        member = tg("getChatMember", {"chat_id": channel["chat_id"], "user_id": uid}) or {}
        status = member.get("status")
        joined = status in {"creator", "administrator", "member"}
        joined = joined or (status == "restricted" and member.get("is_member") is True)
        return joined
    except Exception as exc:
        LOG.warning("Membership check failed for %s: %s", channel.get("chat_id"), exc)
        return False

def unjoined_channels(uid, use_cache=False):
    if use_cache:
        with MEMBERSHIP_CACHE_LOCK:
            cached = membership_cache.get(uid)
            if cached and cached[0] > time.monotonic():
                return cached[1]

    channels_to_check = channels_list()
    if not channels_to_check:
        unjoined = []
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(channels_to_check)), thread_name_prefix="membership-check") as pool:
            joined = list(pool.map(lambda channel: channel_member_status(channel, uid), channels_to_check))
        unjoined = [channel for channel, is_joined in zip(channels_to_check, joined) if not is_joined]

    with MEMBERSHIP_CACHE_LOCK:
        if len(membership_cache) >= MAX_MEMBERSHIP_CACHE_ENTRIES:
            expired = [key for key, (expires_at, _) in membership_cache.items() if expires_at <= time.monotonic()]
            for key in expired:
                membership_cache.pop(key, None)
            if len(membership_cache) >= MAX_MEMBERSHIP_CACHE_ENTRIES:
                membership_cache.pop(next(iter(membership_cache)))
        membership_cache[uid] = (time.monotonic() + MEMBERSHIP_CACHE_TTL_SECONDS, unjoined)
    return unjoined

def clear_membership_cache():
    with MEMBERSHIP_CACHE_LOCK:
        membership_cache.clear()

def subscribed(uid):
    return not unjoined_channels(uid)

def sub_prompt(chat, message_id=None, unjoined=None):
    rows = []
    missing_links = []
    uid = chat
    for ch in unjoined if unjoined is not None else unjoined_channels(uid):
        target = ch.get("invite_url") or (f"https://t.me/{ch['username']}" if ch.get("username") else "")
        title = ch.get("title", "channel/group")
        if target:
            rows.append([{"text": f"Join {title}", "url": target}])
        else:
            missing_links.append(title)
    if rows or missing_links:
        rows += [[btn("Verify Membership ✅", "sub:verify")]]
    text = "🔐 <b>Join Required Channels / Groups</b>\n\nJoin the channels shown below, then tap Verify Membership."
    if missing_links:
        text += "\n\n⚠️ <b>Join link unavailable:</b>\n" + "\n".join(f"• {esc(title)}" for title in missing_links)
        text += "\n\nAdmin must configure a public username or invite link for these private channels/groups."
    return edit(chat, message_id, text, kb(rows)) if message_id else send(chat, text, kb(rows))

def require_membership(chat, uid, message_id=None):
    """Return False and show the join screen when a non-admin has left."""
    if is_admin(uid):
        return True
    unjoined = unjoined_channels(uid, use_cache=True)
    if not unjoined:
        return True
    cancel_extract_timeout(uid)
    session(uid)
    sub_prompt(chat, message_id, unjoined)
    return False

# ═══════════════════════════════════════════════════════════════
# USER INTERFACE & MENUS
# ═══════════════════════════════════════════════════════════════
def main_menu(uid):
    rows = [[btn("📤 Extract", "menu:extract", "success"), btn("📊 Viewer 💎0.5", "menu:viewer", "primary")],
            [btn("👤 Profile", "menu:profile", "success"), btn("🎁 Refer & Earn", "menu:refer", "success")],
            [btn("🏆 Leaderboard", "menu:leaderboard", "primary"), btn("💎 Buy Diamonds", "menu:buy", "success")],
        [btn("🧾 History", "menu:history", "primary"), btn("💬 Support", "menu:support", "primary")]]
    if is_admin(uid): rows.append([btn("🛠️ Admin", "admin:panel", "danger")])
    return kb(rows)

def welcome(chat, uid, message_id=None):
    user = (users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)) or {}
    c = cfg()
    text = (f"👋 <b>Welcome {esc(user.get('first_name') or 'Friend')}!</b>\n\n"
            f"{esc(c.get('welcome_text', 'Your extraction workspace is ready.'))}\n\n"
            f"💎 Balance: <b>{user.get('diamonds', 0)} Diamonds</b>")

    # Telegram inline keyboards belong to the message they are sent with.
    # When a welcome image is configured, keep the home screen as one photo
    # message with its caption and keyboard. If a previous screen was text,
    # it cannot be converted to media in-place, so replace that one message.
    if c.get("welcome_image"):
        if message_id:
            try:
                return edit_caption(chat, message_id, text, main_menu(uid))
            except Exception:
                delete_message(chat, message_id)
        try:
            return send_photo(chat, c["welcome_image"], text, main_menu(uid))
        except HTTPError as exc:
            if exc.code == 400:
                if settings is not None:
                    settings.update_one({"_id": "config"}, {"$set": {"welcome_image": ""}}, upsert=True)
                else:
                    fallback_settings["welcome_image"] = ""
                LOG.warning("Invalid welcome image cleared; using text fallback")
            else:
                LOG.warning("Welcome image failed with HTTP %s; using text fallback", exc.code)
            if message_id:
                try:
                    return edit(chat, message_id, text, main_menu(uid))
                except Exception:
                    pass
            return send(chat, text, main_menu(uid))
        except Exception as exc:
            LOG.warning("Welcome image failed: %s; using text fallback", exc)
            if message_id:
                try:
                    return edit(chat, message_id, text, main_menu(uid))
                except Exception:
                    pass
            return send(chat, text, main_menu(uid))
    return edit(chat, message_id, text, main_menu(uid)) if message_id else send(chat, text, main_menu(uid))

def captcha(chat, uid, message_id=None):
    a, b = random.randint(2, 15), random.randint(2, 15); answer_value = a + b
    options = {answer_value}
    while len(options) < 4: options.add(random.randint(1, 30))
    options = list(options); random.shuffle(options)
    challenge = {"answer": answer_value, "expires": time.time() + 300}
    captcha_challenges[uid] = challenge
    if users is not None:
        users.update_one({"user_id": uid}, {"$set": {"captcha": challenge}})
    text = f"🧩 <b>Quick Verification</b>\n\nSolve: <b>{a} + {b} = ?</b>\nChoose the correct answer below."
    rows = [[btn(str(x), f"captcha:{x}") for x in options]]
    return edit(chat, message_id, text, kb(rows)) if message_id else send(chat, text, kb(rows))

def complete_verification(chat, uid, message_id=None):
    existing = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
    if existing.get("verified"):
        welcome(chat, uid, message_id)
        return
    captcha_challenges.pop(uid, None)
    verified_users.add(uid)
    if users is not None:
        user = users.find_one({"user_id": uid}) or {}
        c = cfg()
        if not user.get("welcome_awarded"):
            users.update_one(
                {"user_id": uid, "welcome_awarded": {"$ne": True}},
                {"$inc": {"diamonds": int(c.get("welcome_bonus", 10))}, "$set": {"welcome_awarded": True}},
            )
        ref = user.get("referred_by")
        if ref and ref != uid and not user.get("referral_rewarded"):
            claimed = users.update_one(
                {"user_id": uid, "referral_rewarded": {"$ne": True}},
                {"$set": {"referral_rewarded": True}},
            )
            if claimed.modified_count:
                users.update_one({"user_id": ref}, {"$inc": {"diamonds": int(c.get("refer_bonus", 5)), "referrals": 1}})
                send(ref, f"🎉 Successful referral! You earned 💎 {c.get('refer_bonus', 5)} Diamonds.")
        users.update_one({"user_id": uid}, {"$set": {"verified": True}, "$unset": {"captcha": "", "session": ""}})
    else:
        user = fallback_user(uid)
        c = cfg()
        if not user.get("welcome_awarded"):
            user["diamonds"] += int(c.get("welcome_bonus", 10))
            user["welcome_awarded"] = True
        ref = user.get("referred_by")
        if ref and ref != uid and not user.get("referral_rewarded"):
            ref_user = fallback_user(ref)
            ref_user["diamonds"] += int(c.get("refer_bonus", 5))
            ref_user["referrals"] += 1
            user["referral_rewarded"] = True
        user["verified"] = True
        user.pop("captcha", None)
        user.pop("session", None)
    welcome(chat, uid, message_id)

def start(message, payload=""):
    uid, chat = message["from"]["id"], message["chat"]["id"]
    cancel_extract_timeout(uid)
    session(uid)
    user = ensure_user(message["from"])
    if payload.startswith("ref_") and not user.get("referred_by"):
        try:
            ref = int(payload[4:])
            if ref != uid:
                if users is not None:
                    users.update_one({"user_id": uid, "referred_by": {"$exists": False}}, {"$set": {"referred_by": ref}})
                else:
                    fallback_user(uid)["referred_by"] = ref
                user["referred_by"] = ref
        except ValueError:
            pass
    if not require_membership(chat, uid):
        return
    # A small debounce avoids Telegram/API races when /start follows a
    # callback or a previous welcome screen is still being edited.
    time.sleep(START_REPLY_DELAY_SECONDS)
    if user.get("verified") or uid in verified_users:
        welcome(chat, uid)
        return
    captcha(chat, uid)

# ═══════════════════════════════════════════════════════════════
# EXTRACTION ENGINE & SCANNER LOGIC
# ═══════════════════════════════════════════════════════════════
def sanitize_url_candidate(raw):
    candidate = (raw or "").strip()
    if not candidate:
        return ""
    if any(ch.isspace() for ch in candidate):
        candidate = re.split(r"\s+", candidate, 1)[0]
    candidate = re.sub(r"[\]\)\}\>\'\"`|,;:!?-]+$", "", candidate)
    candidate = candidate.rstrip(".,;:!?)]}>'\"`|")
    return candidate


def normalize_firebase_url(raw):
    candidate = sanitize_url_candidate(raw)
    if not candidate:
        return ""
    if not candidate.startswith(("http://", "https://")):
        candidate = "https://" + candidate
    try:
        p = urlparse(candidate)
        host = (p.hostname or "").lower()
        if p.scheme not in {"http", "https"} or not FIREBASE_REGEX.search(host):
            return ""
        if host in {"firebaseio.com", "firebasedatabase.app", "firebaseapp.com"}:
            return ""
        # Keep only the canonical Firebase database root: host-only URL,
        # regardless of extra path, query, or garbage suffixes embedded in APK strings.
        return f"{p.scheme}://{host}".rstrip("/")
    except Exception:
        return ""


def clean_firebase_url_list(urls):
    cleaned = []
    seen = set()
    for raw in urls or []:
        clean = normalize_firebase_url(raw)
        if clean and clean not in seen:
            seen.add(clean)
            cleaned.append(clean)
    return cleaned


def extract_input_urls(text):
    """Find explicit URLs in text, including bare Firebase domains."""
    raw_urls = re.findall(r"https?://[^\s<>\"'`|]+", text or "", re.I)
    raw_urls += re.findall(
        r"(?<![A-Za-z0-9:/.-])"
        r"(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.)?"
        r"(?:firebaseio\.com|firebasedatabase\.app|firebaseapp\.com)"
        r"(?:/[^\s<>\"'`|]*)?",
        text or "",
        re.I,
    )
    found = []
    for raw in raw_urls:
        clean = normalize_firebase_url(raw)
        if clean and clean not in found:
            found.append(clean)
    return found

URL_LIKE_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'`|]+|"
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?:[/:?#][^\s<>\"'`|]*)?",
    re.I,
)

def extract_web_urls(text):
    found = []
    for match in URL_LIKE_RE.finditer(text or ""):
        raw = match.group(0).rstrip(".,;:!?)]}>")
        if not re.match(r"https?://", raw, re.I):
            raw = "https://" + raw
        if raw not in found:
            found.append(raw)
    return found

def _decode_embedded_value(value):
    value = unquote(str(value or "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9+/=_-]{16,}", value):
        return ""
    normalized = value.replace("-", "+").replace("_", "/")
    try:
        decoded = base64.b64decode(normalized + "=" * (-len(normalized) % 4), validate=False).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    # Do not treat arbitrary Base64-looking words as URLs.
    return decoded if re.search(r"https?://|firebaseio\.com|firebasedatabase\.app|firebaseapp\.com", decoded, re.I) else ""

def endpoint_urls(text):
    candidates = []
    pending = [text or ""]
    seen_payloads = set()
    while pending and len(seen_payloads) < 20:
        payload = pending.pop(0)
        if payload in seen_payloads:
            continue
        seen_payloads.add(payload)
        raw_candidates = re.findall(r"https?://[^\s<>\"'`|]+", payload, re.I)
        for raw in raw_candidates:
            clean = normalize_firebase_url(raw)
            if clean:
                candidates.append(clean)
        for raw in raw_candidates:
            try:
                p = urlparse(raw)
                vals = [v for group in parse_qs(p.query, keep_blank_values=True).values() for v in group]
                if "=" in p.fragment:
                    vals.append(p.fragment.split("=", 1)[1])
                for value in vals:
                    decoded = _decode_embedded_value(value)
                    if decoded:
                        pending.append(decoded)
            except Exception:
                continue
        # Also support a Base64 value pasted by itself or embedded in a line.
        for value in re.findall(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{16,}(?![A-Za-z0-9+/=_-])", payload):
            decoded = _decode_embedded_value(value)
            if decoded:
                pending.append(decoded)
    found = []
    for raw in candidates:
        if raw and raw not in found:
            found.append(raw)
    return clean_firebase_url_list(found)

def binary_urls(data):
    text = data.decode("utf-8", errors="ignore")
    found = set(endpoint_urls(text))

    # APKs often split URLs across binary buffers. Inspect printable strings
    # separately so embedded endpoints are found even when the whole payload
    # is not valid UTF-8.
    segment_count = 0
    for match in re.finditer(rb"[\x20-\x7e]{4,}", data):
        segment_count += 1
        if segment_count > MAX_PRINTABLE_SEGMENTS:
            break
        segment = match.group(0).decode("utf-8", errors="ignore")
        found.update(endpoint_urls(segment))

    pattern = re.compile(
        r"(?<![-/\w])(?:[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?\.)+"
        r"(?:firebaseio\.com|firebasedatabase\.app|firebaseapp\.com)\b",
        re.I,
    )
    for domain in pattern.findall(text):
        found.update(endpoint_urls("https://" + domain))
    return found

def scan_apk(path):
    found, scanned = set(), 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            LOG.warning("APK contains %d entries; scanning first %d only", len(infos), MAX_ARCHIVE_ENTRIES)
        for info in infos[:MAX_ARCHIVE_ENTRIES]:
            if info.is_dir() or info.file_size > MAX_ENTRY or scanned >= MAX_SCAN: continue
            limit = min(info.file_size, MAX_SCAN - scanned)
            with archive.open(info) as entry: data = entry.read(limit + 1)
            if len(data) <= limit: scanned += len(data); found.update(binary_urls(data))
    return sorted(found)

def scan_web(text_input):
    targets = extract_web_urls(text_input)
    if not targets: targets = [text_input]
    found = set()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for url in targets:
        try:
            if not url.startswith("http"): url = "https://" + url
            # Decode Firebase endpoints from wrapper URLs before fetching the
            # wrapper page. This avoids saving placeholder/demo URLs exposed
            # by the wrapper website itself.
            embedded = endpoint_urls(url)
            found.update(embedded)
            if embedded:
                continue
            p = urlparse(url)
            if FIREBASE_REGEX.search(p.hostname or ""): 
                found.update(endpoint_urls(url))
                continue
            with urlopen(Request(url, headers=headers), timeout=15) as response: body = response.read(1_500_001)
            found.update(binary_urls(body))
            page = body.decode(errors="ignore")
            for script in re.findall(r"""(?:src|href)=["']([^"']+\.js[^"']*)["']""", page, re.I)[:8]:
                try:
                    js_url = urljoin(url, script)
                    with urlopen(Request(js_url, headers=headers), timeout=10) as response: 
                        found.update(binary_urls(response.read(500_001)))
                except Exception: pass
        except Exception: pass
    return sorted(found)

def scan_web_sources(source_links):
    if len(source_links) <= 1:
        return [(link, scan_web(link)) for link in source_links]
    with ThreadPoolExecutor(max_workers=min(8, len(source_links)), thread_name_prefix="web-scan") as pool:
        return list(zip(source_links, pool.map(scan_web, source_links)))

def download(file_id):
    path_info = tg("getFile", {"file_id": file_id}).get("file_path")
    if not path_info: raise RuntimeError("Telegram did not return a valid file path")
    suffix = Path(path_info).suffix or ".apk"
    temporary = tempfile.NamedTemporaryFile(prefix="firebase-", suffix=suffix, delete=False)
    path = Path(temporary.name)
    temporary.close()
    try:
        download_url = f"https://api.telegram.org/file/bot{TOKEN}/{path_info}"
        last_error = None
        for attempt in range(3):
            try:
                with urlopen(Request(download_url), timeout=300) as response, path.open("wb") as out:
                    size = 0
                    while chunk := response.read(256 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE:
                            raise RuntimeError("File size exceeds 100MB limit!")
                        out.write(chunk)
                return path
            except (TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < 2:
                    LOG.warning("Telegram file download timed out; retrying (%d/2)", attempt + 1)
                    continue
                raise RuntimeError("Telegram file download timed out after 3 attempts") from exc
            except Exception:
                raise
        raise last_error or RuntimeError("Telegram file download failed")
    except Exception:
        path.unlink(missing_ok=True)
        raise

def save_extract(uid, urls, source):
    urls = clean_firebase_url_list(urls)
    new_urls = []
    if firebase is not None:
        for url in urls:
            result = firebase.update_one({"url": url}, {"$setOnInsert": {"url": url, "first_user_id": uid, "first_seen": now(), "source": source}}, upsert=True)
            if result.upserted_id:
                new_urls.append(url)
            events.insert_one({"type": "extract", "user_id": uid, "url": url, "source": source, "created_at": now()})
        users.update_one({"user_id": uid}, {"$inc": {"extracts": len(urls), f"{source}_extracts": 1}})
    else:
        user = fallback_user(uid)
        for url in urls:
            if url not in fallback_firebase:
                fallback_firebase[url] = {"url": url, "first_user_id": uid, "first_seen": now(), "source": source}
                new_urls.append(url)
            fallback_events.append({"type": "extract", "user_id": uid, "url": url, "source": source, "created_at": now()})
        user["extracts"] += len(urls)
        user[f"{source}_extracts"] += 1
    return new_urls

def update_balance(uid, amount):
    if users is not None:
        users.update_one({"user_id": uid}, {"$inc": {"diamonds": amount}})
    else:
        fallback_user(uid)["diamonds"] += amount

def dump_channel_config():
    config = cfg()
    channel_id = config.get("dump_channel_id", "")
    return str(channel_id).strip(), config.get("dump_channel_title", "Dump Channel")

def configure_dump_channel(channel):
    values = {"dump_channel_id": str(channel["id"]), "dump_channel_title": channel.get("title", "Dump Channel")}
    if settings is not None:
        settings.update_one({"_id": "config"}, {"$set": values}, upsert=True)
    else:
        fallback_settings.update(values)

def send_to_dump_channel(uid, urls, source):
    channel_id, channel_title = dump_channel_config()
    if not channel_id or not urls:
        return 0
    sent_urls = set()
    if settings is not None:
        config = cfg()
        sent_urls = set(config.get("dump_sent_urls", []))
    else:
        sent_urls = set(fallback_dump_sent)
    new_urls = [url for url in urls if url not in sent_urls]
    delivered_urls = new_urls[:30]
    if not delivered_urls:
        return 0
    lines = "\n".join(f"{index}. <code>{esc(url)}</code>" for index, url in enumerate(delivered_urls, 1))
    text = f"🔥 <b>Firebase Received ({source.upper()})</b>\n\n👤 User ID: <code>{uid}</code>\n📂 New URLs: <b>{len(delivered_urls)}</b>\n\n{lines}"
    try:
        tg("sendMessage", {"chat_id": int(channel_id), "text": text[:4090], "parse_mode": "HTML", "disable_web_page_preview": True})
    except Exception:
        LOG.exception("Dump channel delivery failed")
        return 0
    if settings is not None:
        settings.update_one({"_id": "config"}, {"$addToSet": {"dump_sent_urls": {"$each": delivered_urls}}}, upsert=True)
    else:
        fallback_dump_sent.update(delivered_urls)
    return len(delivered_urls)

def format_diamonds(value):
    return f"{value:g}" if isinstance(value, float) else str(value)

def input_lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]

URL_LIKE_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'`|]+|"
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?:[/:?#][^\s<>\"'`|]*)?",
    re.I,
)

def looks_like_url(text):
    return bool(URL_LIKE_RE.search(text or ""))

def looks_like_firebase_url(text):
    return bool(re.search(r"(?:firebaseio\.com|firebasedatabase\.app|firebaseapp\.com)", text or "", re.I))

def firebase_targets_from_text(text):
    """Return unique Firebase endpoints from raw, bare, or Base64-wrapper URLs."""
    targets = []
    # Parse the complete payload first so a Base64-only value can be decoded;
    # then parse normalized explicit URLs as a compatibility fallback.
    source_values = [text or ""] + extract_input_urls(text)
    for source_url in source_values:
        candidates = endpoint_urls(source_url)
        if not candidates and looks_like_firebase_url(source_url):
            candidates = endpoint_urls("https://" + source_url)
        for target in candidates:
            if target not in targets:
                targets.append(target)
    return targets

def send_completion_summary(chat, kind, urls, cost, billed_links=None, result_text=None):
    """Keep the completion summary silent to avoid dumping raw Firebase URLs after extraction."""
    return None

def send_extraction_txt(chat, title, urls):
    result_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="firebase-result-", suffix=".txt", mode="w", encoding="utf-8", delete=False
        ) as handle:
            result_path = Path(handle.name)
            handle.write(f"{title}\n\n")
            if urls:
                handle.write("\n".join(urls))
            else:
                handle.write("No Firebase database URL found.")
        send_document(chat, result_path)
        return True
    finally:
        if result_path:
            result_path.unlink(missing_ok=True)

def user_history_firebase_urls(uid):
    """Return only this user's unique Firebase URLs, newest first."""
    if events is not None:
        rows = events.find({"user_id": uid}, {"url": 1, "created_at": 1}).sort("created_at", -1)
    else:
        rows = sorted(
            (item for item in fallback_events if item.get("user_id") == uid),
            key=lambda item: item.get("created_at", now()),
            reverse=True,
        )
    unique = []
    seen = set()
    for item in rows:
        url = normalize_firebase_url(item.get("url", ""))
        if url and url not in seen:
            seen.add(url)
            unique.append(url)
    return unique

def send_user_history_txt(chat, uid):
    """Send one TXT containing only the requesting user's unique history."""
    urls = user_history_firebase_urls(uid)
    if not urls:
        return False
    return send_extraction_txt(chat, "My Unique Firebase History", urls)


def send_viewer_result_pages(chat, active_results, inactive_results):
    """Send grouped viewer results, keeping each Telegram page compact."""
    expiry = time.time() - 3600
    for old_key, old_state in list(viewer_result_pages.items()):
        if old_state.get("created_at", 0) < expiry:
            viewer_result_pages.pop(old_key, None)
    while len(viewer_result_pages) + sum(bool(items) for items in (active_results, inactive_results)) > MAX_VIEWER_RESULT_SESSIONS:
        oldest_key = min(
            viewer_result_pages,
            key=lambda key: viewer_result_pages[key].get("created_at", 0),
            default=None,
        )
        if oldest_key is None:
            break
        viewer_result_pages.pop(oldest_key, None)
    for category, title, records in (
        ("active", "Active Firebase URLs", active_results),
        ("inactive", "Unavailable / Failed Firebase URLs", inactive_results),
    ):
        if not records:
            continue
        page_key = secrets.token_hex(6)
        viewer_result_pages[page_key] = {
            "title": title,
            "records": records,
            "category": category,
            "created_at": time.time(),
        }
        send_viewer_result_page(chat, page_key, 0)


def send_viewer_result_page(chat, page_key, page, message_id=None):
    state = viewer_result_pages.get(page_key)
    if not state:
        return
    if state.get("created_at", 0) < time.time() - 3600:
        viewer_result_pages.pop(page_key, None)
        return
    records = state["records"]
    page_size = 10
    total_pages = (len(records) + page_size - 1) // page_size
    page = max(0, min(page, total_pages - 1))
    selected = records[page * page_size:(page + 1) * page_size]
    lines = []
    if state["category"] == "active":
        for index, (target, total, online, offline, unknown) in enumerate(selected, page * page_size + 1):
            lines.append(
                f"{index}. <code>{esc(target)}</code>\n"
                f"   📊 Total: <b>{total}</b> | 🟢 <b>{online}</b> | 🔴 <b>{offline}</b> | ⚪ <b>{unknown}</b>"
            )
    else:
        for index, (target, reason) in enumerate(selected, page * page_size + 1):
            lines.append(f"{index}. <code>{esc(target)}</code>\n   ⚠️ {esc(reason)}")
    markup = None
    if total_pages > 1:
        controls = []
        if page > 0:
            controls.append(btn("⬅️ Back", f"viewerpage:{state['category']}:{page_key}:{page - 1}"))
        if page < total_pages - 1:
            controls.append(btn("Next ➡️", f"viewerpage:{state['category']}:{page_key}:{page + 1}"))
        markup = kb([controls])
    text = f"{'✅' if state['category'] == 'active' else '❌'} <b>{state['title']}</b> ({page + 1}/{total_pages})\n\n" + "\n".join(lines)
    return edit(chat, message_id, text, markup) if message_id else send(chat, text, markup)


def admin_firebase_urls():
    urls = []
    seen = set()
    items = list(firebase.find({}, {"url": 1}).sort("first_seen", -1)) if firebase is not None else list(fallback_firebase.values())
    for item in items:
        url = normalize_firebase_url(item.get("url"))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def admin_firebase_active_urls(limit=100):
    urls = admin_firebase_urls()[:limit]
    if not urls:
        return []

    def check(url):
        try:
            read_firebase_status_fast(url)
            return url
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=min(12, len(urls)), thread_name_prefix="firebase-active") as pool:
        checked = pool.map(check, urls)
    return [url for url in checked if url]


def firebase_txt_content(title, urls, numbered):
    header = f"{title}\n\n"
    if not urls:
        return header + "No Firebase URLs found.\n"
    if numbered:
        body = "\n".join(f"{index}. {url}" for index, url in enumerate(urls, 1))
    else:
        body = "\n".join(urls)
    return header + body + "\n"


def send_admin_firebase_txt(chat, title, urls, numbered):
    text = firebase_txt_content(title, urls, numbered)
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="firebase-admin-", suffix=".txt", mode="w", encoding="utf-8", delete=False) as handle:
            handle.write(text)
            path = Path(handle.name)
        send_document(chat, str(path))
    finally:
        if path:
            path.unlink(missing_ok=True)


def admin_firebase_menu(chat, mid=None):
    rows = [
        [btn("📋 All • Numbered", "admin:firebase_all_num")],
        [btn("📄 All • Plain TXT", "admin:firebase_all_plain")],
        [btn("🟢 Active • Numbered", "admin:firebase_active_num")],
        [btn("🟢 Active • Plain TXT", "admin:firebase_active_plain")],
        [btn("🔙 Back", "admin:panel")],
    ]
    text = "🔥 <b>Firebase Export</b>\n\nChoose <b>All</b> or <b>Active</b> Firebase URLs, then pick a numbered or line-by-line TXT format."
    edit(chat, mid, text, kb(rows)) if mid else send(chat, text, kb(rows))


def another_task_prompt(chat):
    send(chat, "✅ Done.\n\n/start to do another thing")

def firebase_status_summary(data):
    if not isinstance(data, dict):
        return 0, 0, 0, 0
    total = online = unknown = 0
    for device in data.values():
        if not isinstance(device, dict):
            continue
        total += 1
        state = next((device[key] for key in ONLINE_KEYS if key in device), None)
        if state is None:
            unknown += 1
            continue
        if isinstance(state, bool):
            online += int(state)
        elif isinstance(state, (int, float)):
            online += int(bool(state))
        elif isinstance(state, str):
            normalized = state.strip().lower()
            if normalized in {"online", "active", "connected", "on", "true", "1", "yes"}:
                online += 1
            elif normalized not in {"offline", "inactive", "disconnected", "off", "false", "0", "no"}:
                unknown += 1
        else:
            unknown += 1
    return total, online, total - online - unknown, unknown

def firebase_status_summary_stream(response):
    """Summarize a Firebase object one top-level record at a time."""
    decoder = json.JSONDecoder()
    utf8_decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    total_bytes = 0
    eof = False

    def read_more():
        nonlocal buffer, total_bytes, eof
        if eof:
            return False
        chunk = response.read(64 * 1024)
        if not chunk:
            buffer += utf8_decoder.decode(b"", final=True)
            eof = True
            return False
        total_bytes += len(chunk)
        if total_bytes > MAX_VIEWER_RESPONSE_BYTES:
            raise ValueError(
                f"Firebase response exceeds the {MAX_VIEWER_RESPONSE_BYTES // (1024 * 1024)} MB Viewer limit."
            )
        buffer += utf8_decoder.decode(chunk)
        return True

    def ensure(position):
        while position >= len(buffer) and read_more():
            pass
        return position < len(buffer)

    def skip_whitespace(position):
        while ensure(position) and buffer[position].isspace():
            position += 1
        return position

    def scan_string(position):
        if not ensure(position) or buffer[position] != '"':
            raise ValueError("Firebase returned invalid JSON.")
        cursor = position + 1
        escaped = False
        while True:
            while cursor < len(buffer):
                char = buffer[cursor]
                cursor += 1
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    return cursor
                if cursor - position > MAX_VIEWER_ENTRY_BYTES:
                    raise ValueError("A Firebase record exceeds the 2 MB Viewer limit.")
            if not read_more():
                raise ValueError("Firebase returned incomplete JSON.")

    def scan_value(position):
        if not ensure(position):
            raise ValueError("Firebase returned incomplete JSON.")
        first = buffer[position]
        if first == '"':
            return scan_string(position)
        if first in "[{":
            cursor = position
            depth = 0
            in_string = False
            escaped = False
            while True:
                while cursor < len(buffer):
                    char = buffer[cursor]
                    cursor += 1
                    if in_string:
                        if escaped:
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == '"':
                            in_string = False
                    elif char == '"':
                        in_string = True
                    elif char in "[{":
                        depth += 1
                    elif char in "]}":
                        depth -= 1
                        if depth == 0:
                            return cursor
                    if cursor - position > MAX_VIEWER_ENTRY_BYTES:
                        raise ValueError("A Firebase record exceeds the 2 MB Viewer limit.")
                if not read_more():
                    raise ValueError("Firebase returned incomplete JSON.")

        cursor = position
        while ensure(cursor) and buffer[cursor] not in ",}":
            cursor += 1
        if cursor == len(buffer) and not eof:
            read_more()
            return scan_value(position)
        return cursor

    position = skip_whitespace(0)
    if not ensure(position):
        raise ValueError("Firebase returned an empty response.")
    if buffer[position] == "n":
        while len(buffer) < position + 4 and read_more():
            pass
        if buffer[position:position + 4] == "null":
            raise ValueError("Ye Firebase deactivate hai. Please send another Firebase URL.")
    if buffer[position] != "{":
        raise ValueError("Firebase response is not a JSON object.")
    position += 1
    total = online = offline = unknown = 0

    while True:
        position = skip_whitespace(position)
        if not ensure(position):
            raise ValueError("Firebase returned incomplete JSON.")
        if buffer[position] == "}":
            return total, online, offline, unknown
        if buffer[position] == ",":
            position = skip_whitespace(position + 1)
        if not ensure(position) or buffer[position] != '"':
            raise ValueError("Firebase returned invalid JSON.")

        key_end = scan_string(position)
        key = json.loads(buffer[position:key_end])
        position = skip_whitespace(key_end)
        if not ensure(position) or buffer[position] != ":":
            raise ValueError("Firebase returned invalid JSON.")

        value_start = skip_whitespace(position + 1)
        value_end = scan_value(value_start)
        value_text = buffer[value_start:value_end]
        if len(value_text.encode("utf-8")) > MAX_VIEWER_ENTRY_BYTES:
            raise ValueError("A Firebase record exceeds the 2 MB Viewer limit.")
        value = decoder.raw_decode(value_text)[0]
        if isinstance(value, dict):
            counts = firebase_status_summary({key: value})
            total += counts[0]
            online += counts[1]
            offline += counts[2]
            unknown += counts[3]

        buffer = buffer[value_end:]
        position = 0

def firebase_base_url(value):
    value = value.strip().strip("/")
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if "." not in (parsed.netloc or ""):
        value = "https://" + parsed.netloc + ".firebaseio.com" + parsed.path
        parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not FIREBASE_REGEX.search(parsed.hostname or ""):
        raise ValueError("Ye wrong Firebase URL hai. Please send another Firebase URL.")
    path = parsed.path.rstrip("/")
    if path.endswith(".json"):
        path = path[:-5].rstrip("/")
    return parsed._replace(path=path, params="", query=parsed.query, fragment="").geturl().rstrip("/")

def read_firebase_status(url):
    base_url = firebase_base_url(url)
    try:
        headers = {"Cache-Control": "no-cache", "User-Agent": "firebase-viewer/1.0"}
        root_url = urlparse(base_url)._replace(path="", params="", query="", fragment="").geturl().rstrip("/")
        endpoint = root_url + "/clients.json"
        with urlopen(Request(endpoint, headers=headers), timeout=30) as response:
            return firebase_status_summary_stream(response)
    except HTTPError as exc:
        if exc.code in {400, 404}:
            raise ValueError("Ye wrong ya deactivated Firebase hai. Please send another Firebase URL.") from exc
        if exc.code in {401, 403}:
            raise ValueError("Firebase active hai, lekin access denied hai. Rules/auth check karein ya another URL send karein.") from exc
        raise ValueError(f"Firebase read failed (HTTP {exc.code}). Please send another URL.") from exc
    except ValueError:
        raise
    except Exception as exc:
        error_text = str(exc)
        if "getaddrinfo failed" in error_text or "11001" in error_text:
            raise ValueError("Ye wrong ya deactivated Firebase hai. Please send another Firebase URL.") from exc
        if "JSON" in error_text or "Expecting value" in error_text:
            raise ValueError("Ye wrong Firebase response hai. Please send another Firebase URL.") from exc
        raise ValueError(f"Firebase check failed. Please send another URL: {error_text[:140]}") from exc

def read_firebase_status_fast(url):
    return read_firebase_status(url)

def check_viewer_target(target):
    try:
        return "active", (target, *read_firebase_status(target))
    except Exception as exc:
        return "inactive", (target, str(exc)[:250])

def check_viewer_targets(targets):
    """Check targets in bounded batches so a large request queues few futures."""
    results = []
    batch_size = 100
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="viewer-check") as pool:
        for offset in range(0, len(targets), batch_size):
            batch = targets[offset:offset + batch_size]
            for target, result in zip(batch, pool.map(check_viewer_target, batch)):
                results.append(result)
    return results

def process_firebase_view(message):
    uid, chat = message["from"]["id"], message["chat"]["id"]
    LOG.info("Firebase Viewer request from user %s", uid)
    targets = firebase_targets_from_text(message.get("text", ""))
    if not targets:
        send(chat, "⚠️ Please send one or more valid Firebase URLs, raw or Base64-wrapped.")
        return
    if len(targets) > MAX_VIEWER_TARGETS:
        send(
            chat,
            f"⚠️ Too many Firebase URLs in one request. Maximum is <b>{MAX_VIEWER_TARGETS}</b>. "
            "Please split them into smaller batches.",
        )
        session(uid)
        return

    viewer_claimed = False
    viewer_slot_acquired = False
    viewer_already_active = False
    with LIMIT_LOCK:
        if uid in active_viewer_users:
            viewer_already_active = True
        else:
            active_viewer_users.add(uid)
            viewer_claimed = True
    if viewer_already_active:
        send(chat, "⏳ Your previous Firebase Viewer request is still running. Please wait.")
        return
    if not VIEWER_SLOTS.acquire(blocking=False):
        with LIMIT_LOCK:
            active_viewer_users.discard(uid)
        send(chat, "⏳ Too many Firebase Viewer checks are running. Please try again in a moment.")
        return
    viewer_slot_acquired = True

    cost = 0.5 * len(targets)
    charged = False
    progress_message = None
    successful = 0
    active_results = []
    inactive_results = []
    try:
        user = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
        if user.get("diamonds", 0) < cost:
            send(chat, f"❌ <b>Insufficient Balance!</b> You need 💎 {format_diamonds(cost)} Diamond to use Firebase Viewer.")
            return
        update_balance(uid, -cost)
        charged = True

        try:
            sticker = cfg().get("viewer_sticker", "")
            if sticker:
                progress_message = send_sticker(chat, sticker)
            else:
                progress_message = send(chat, f"🔎 <b>Checking {len(targets)} Firebase URL(s)...</b>")
        except Exception:
            LOG.warning("Viewer progress message failed", exc_info=True)

        checked_results = check_viewer_targets(targets)

        for category, record in checked_results:
            if category == "active":
                successful += 1
                active_results.append(record)
            else:
                inactive_results.append(record)

        # Small requests keep the detailed legacy messages. For 10+ URLs,
        # grouped pages avoid flooding Telegram and are followed by one TXT
        # containing active URLs only.
        if len(targets) < 10:
            for index, (category, record) in enumerate(checked_results, 1):
                try:
                    if category == "active":
                        target, total, online, offline, unknown = record
                        send(chat, f"✅ <b>Firebase Active ({index}/{len(targets)})</b>\n\n<code>{esc(target)}</code>\n\n📊 Total Numbers: <b>{total}</b>\n🟢 Online: <b>{online}</b>\n🔴 Offline: <b>{offline}</b>\n⚪ Unknown: <b>{unknown}</b>\n📌 Status: <b>Working</b>")
                    else:
                        target, reason = record
                        send(chat, f"❌ <b>Firebase {index}/{len(targets)} Failed</b>\n\n<code>{esc(target)}</code>\n\n{esc(reason)}")
                except Exception:
                    LOG.warning("Viewer result message failed for item %d", index, exc_info=True)

        if len(targets) >= 10:
            try:
                send_viewer_result_pages(chat, active_results, inactive_results)
            except Exception:
                LOG.exception("Grouped viewer result delivery failed")
            if active_results:
                active_urls = list(dict.fromkeys(record[0] for record in active_results))
                try:
                    send_extraction_txt(chat, "Active Firebase Viewer Results", active_urls)
                except Exception:
                    LOG.exception("Active viewer TXT delivery failed")
        send_completion_summary(chat, "Viewer", targets, cost, len(targets), f"{successful}/{len(targets)} checked")
    except Exception as exc:
        if charged:
            try:
                update_balance(uid, cost)
            except Exception:
                LOG.exception("Viewer refund failed for user %s", uid)
        try:
            refund_text = f"\n🔄 💎 <b>{format_diamonds(cost)} Diamond Refunded</b>." if charged else ""
            send(chat, f"❌ <b>Viewer Error:</b> <code>{esc(str(exc)[:250])}</code>{refund_text}")
        except Exception:
            LOG.exception("Viewer error notification failed for user %s", uid)
    finally:
        if viewer_slot_acquired:
            VIEWER_SLOTS.release()
        if viewer_claimed:
            with LIMIT_LOCK:
                active_viewer_users.discard(uid)
        if progress_message and progress_message.get("message_id"):
            delete_message(chat, progress_message["message_id"])
        session(uid)
    another_task_prompt(chat)

# ═══════════════════════════════════════════════════════════════
# EXTRACTION WORKFLOW WITH REFUND & DUMP CHANNELS
# ═══════════════════════════════════════════════════════════════
def process_extraction(message):
    uid, chat = message["from"]["id"], message["chat"]["id"]; path = None
    result_delivered = False
    has_file = bool(message.get("document"))
    text_input = message.get("text", "")
    if not has_file and not looks_like_url(text_input):
        send(chat, "⚠️ Please send an APK file, .txt file, or a valid public web/Firebase URL.")
        return
    cancel_extract_timeout(uid)
    session(uid)
    if is_banned(uid):
        send(chat, "🚫 <b>You are blocked from using this bot.</b>")
        return
    u = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
    cost_per_link = max(1, int(cfg().get("cost_per_extract", 1)))
    charged_cost = 0
    status_msg = None

    # Reject unsupported files before touching the user's balance. A random
    # photo/audio/video must never become an extraction request or a charge.
    is_apk = False
    is_text_file = False
    slot_claimed = False
    extraction_acquired = False
    user_extraction_claimed = False
    if has_file:
        doc = message["document"]
        file_name = doc.get("file_name", "").lower()
        is_apk = file_name.endswith(".apk") or doc.get("mime_type") == "application/vnd.android.package-archive"
        is_text_file = file_name.endswith((".txt", ".text", ".log")) or doc.get("mime_type", "").startswith("text/")
        if not (is_apk or is_text_file):
            send(chat, "⚠️ Please send a valid <b>.apk</b> or <b>.txt</b> file document.")
            schedule_extract_timeout(
                chat,
                uid,
                (current_session(uid) or {}).get("message_id"),
                (current_session(uid) or {}).get("started_at", time.time()),
            )
            return
        if is_apk:
            extraction_already_active = False
            with LIMIT_LOCK:
                if uid in active_extraction_users:
                    extraction_already_active = True
                else:
                    active_extraction_users.add(uid)
            if extraction_already_active:
                send(chat, "⏳ Your previous APK extraction is still running. Please wait for it to finish.")
                return
            user_extraction_claimed = True
            if not EXTRACTION_SLOTS.acquire(blocking=False):
                with LIMIT_LOCK:
                    active_extraction_users.discard(uid)
                user_extraction_claimed = False
                send(chat, "⏳ Too many APK extractions are running. Please try again in a moment.")
                return
            extraction_acquired = True
            if not claim_apk_slot(uid):
                EXTRACTION_SLOTS.release()
                extraction_acquired = False
                with LIMIT_LOCK:
                    active_extraction_users.discard(uid)
                user_extraction_claimed = False
                send(chat, f"⚠️ Daily APK limit reached ({MAX_DAILY_APKS}). Try again tomorrow.")
                return
            slot_claimed = True

    try:
        status_msg = send(chat, "⚙️ <b>Running Deep Extraction Engine...</b>\nDecoding and scanning each link...")
        inputs = []
        if is_apk:
            doc = message["document"]
            path = download(doc["file_id"])
            urls, source = scan_apk(path), "apk"
            inputs = [("APK file", urls)]
            billable_inputs = 1 if urls else 0
        elif is_text_file:
            doc = message["document"]
            path = download(doc["file_id"])
            text_content = path.read_text(encoding="utf-8", errors="ignore")[:MAX_SCAN]
            source = "web"
            source_links = extract_web_urls(text_content)
            if source_links:
                inputs = scan_web_sources(source_links)
            else:
                inputs = [("TXT file", scan_web(text_content))]
            urls = sorted({url for _, found in inputs for url in found})
            billable_inputs = sum(1 for _, found in inputs if found)
        else:
            source = "web"
            source_links = extract_web_urls(text_input)
            inputs = scan_web_sources(source_links) if source_links else [("Message", scan_web(text_input))]
            urls = sorted({url for _, found in inputs for url in found})
            billable_inputs = sum(1 for _, found in inputs if found)

        if not urls:
            if is_apk:
                result_delivered = send_extraction_txt(chat, "Firebase Extraction Result", [])
            send(chat, "⚠️ <b>Scan Complete:</b> No Firebase database URL found. No diamond was used.")
            return

        cost = cost_per_link * billable_inputs
        u = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
        if u.get("diamonds", 0) < cost:
            send(chat, f"❌ <b>Insufficient Balance!</b> You need 💎 {format_diamonds(cost)} Diamonds for {billable_inputs or len(urls)} link(s).", main_menu(uid))
            return

        # Scan first, then charge only for source links that produced a real
        # Firebase endpoint. A failed/empty scan is never billed.
        update_balance(uid, -cost)
        charged_cost = cost

        urls = clean_firebase_url_list(urls)
        new_urls = save_extract(uid, urls, source)
        if is_apk:
            result_delivered = send_extraction_txt(chat, "Firebase Extraction Result", urls)
        for index, (label, found_urls) in enumerate(inputs, 1):
            clean_found = clean_firebase_url_list(found_urls)
            if clean_found:
                lines = "\n".join(f"{i}. <code>{esc(u_url)}</code>" for i, u_url in enumerate(clean_found[:30], 1))
                send(
                    chat,
                    f"🔥 <b>Extraction {index}/{len(inputs)} Successful!</b>\n\n"
                    f"<b>Input:</b> <code>{esc(label[:180])}</code>\n"
                    f"<b>Found Databases ({len(clean_found)}):</b>\n{lines}\n\n"
                    f"💎 <b>Credit used:</b> <code>{format_diamonds(cost_per_link)}</code> Diamond"
                )
            else:
                send(chat, f"⚠️ <b>Extraction {index}/{len(inputs)}:</b> No Firebase URL found.\n<b>Input:</b> <code>{esc(label[:180])}</code>\n\n💎 <b>Credit used:</b> <code>0</code> Diamond")

        send_completion_summary(chat, "Extraction", urls, cost, billable_inputs or len(urls))

        send_to_dump_channel(uid, new_urls, source)
        lines = "\n".join(f"{i}. <code>{esc(u_url)}</code>" for i, u_url in enumerate(urls[:30], 1))

        # Keep the legacy destination only until a custom dump channel is configured.
        if not dump_channel_config()[0] and USER_DUMP_CHANNEL:
            try:
                dump_msg = f"🔥 <b>New Firebase Extracted ({source.upper()})</b>\n\n<b>Databases ({len(urls)}):</b>\n{lines}"
                tg("sendMessage", {"chat_id": USER_DUMP_CHANNEL, "text": dump_msg[:4090], "parse_mode": "HTML"})
            except Exception: pass

        # Dump to Admin Private Channel
        try:
            new_lines = "\n".join(f"{i}. <code>{esc(u_url)}</code>" for i, u_url in enumerate(new_urls[:30], 1))
            admin_dump = (f"🚨 <b>[EXTRACTION LOG]</b>\n\n"
                          f"👤 User: {esc(uname(u))}\n"
                          f"🆔 ID: <code>{uid}</code>\n"
                          f"📂 Source: <b>{source.upper()}</b>\n"
                          f"🎯 New Firebase ({len(new_urls)}):\n{new_lines}")
            if ADMIN_DUMP_CHANNEL and new_urls:
                tg("sendMessage", {"chat_id": ADMIN_DUMP_CHANNEL, "text": admin_dump[:4090], "parse_mode": "HTML"})
        except Exception: pass

    except Exception as exc:
        LOG.exception("Extract error")
        # Automatic Refund Logic on error
        if charged_cost:
            update_balance(uid, charged_cost)
        refund_text = f"\n🔄 💎 <b>{format_diamonds(charged_cost)} Diamond Refunded</b>." if charged_cost else ""
        send(chat, f"❌ <b>Extraction Error:</b> <code>{esc(str(exc)[:200])}</code>{refund_text}", main_menu(uid))
    finally:
        if status_msg and status_msg.get("message_id"):
            delete_message(chat, status_msg["message_id"])
        # Uploaded files are temporary inputs. Remove APKs even when scanning
        # fails or the APK has an invalid ZIP structure.
        if path:
            path.unlink(missing_ok=True)
        if extraction_acquired:
            EXTRACTION_SLOTS.release()
        if user_extraction_claimed:
            with LIMIT_LOCK:
                active_extraction_users.discard(uid)
        session(uid)
    another_task_prompt(chat)

# ═══════════════════════════════════════════════════════════════
# ORDERS & PAYMENT SYSTEM
# ═══════════════════════════════════════════════════════════════
def order_text(order):
    u = users.find_one({"user_id": order["user_id"]}) or {} if users is not None else fallback_user(order["user_id"])
    return f"🛎️ <b>Wallet Deposit Request</b>\n\n👤 User: {esc(uname(u))}\n🆔 ID: <code>{order['user_id']}</code>\n💰 Amount: ₹{order['amount']}\n🧾 UTR: <code>{order.get('utr', 'Not submitted')}</code>\n\nStatus: <b>{order['status'].upper()}</b>"

def create_order(chat, uid, amount):
    oid = secrets.token_hex(5).upper()
    order = {"order_id": oid, "user_id": uid, "amount": amount, "status": "created", "created_at": now()}
    if orders is not None:
        orders.insert_one(order)
    else:
        fallback_orders[oid] = order
    text = f"🛎️ <b>Wallet Deposit</b>\n\nPay <b>₹{amount}</b> for <b>{amount} Diamonds</b>.\nOrder ID: <code>{oid}</code>\n\nAfter payment, tap <b>I Have Paid</b>."
    buttons = kb([[btn("I Have Paid ✅", f"order:paid:{oid}"), btn("Cancel Order ❌", f"order:cancel:{oid}")]])
    if cfg().get("qr_file_id"):
        try: return send_photo(chat, cfg()["qr_file_id"], text, buttons)
        except Exception: LOG.exception("QR send failed")
    send(chat, text + "\n\n⚠️ QR Code is not configured by admin yet.", buttons)

# ═══════════════════════════════════════════════════════════════
# ADMIN PANEL HANDLERS
# ═══════════════════════════════════════════════════════════════
def admin_panel(chat, mid=None):
    rows = [[btn("📡 Channels", "admin:channels"), btn("📥 Dump", "admin:dump")],
            [btn("👮 Admins", "admin:admins")],
            [btn("🛎️ Orders", "admin:orders"), btn("🌐 History", "admin:history")],
            [btn("💎 Diamonds", "admin:diamond"), btn("🎁 Bonuses", "admin:bonuses")],
            [btn("🚫 Ban User", "admin:ban"), btn("✅ Unban User", "admin:unban")],
            [btn("🔥 Firebase Data", "admin:firebase"), btn("🔍 Check User", "admin:check")],
            [btn("📢 Broadcast", "admin:broadcast"), btn("🖼️ Images", "admin:images")],
            [btn("📊 Status", "admin:status")], [btn("🔙 Main Menu", "nav:home")]]
    text = "🛠️ <b>Admin Control Panel</b>\nManage channels, orders, users, balances, and broadcasts."
    edit(chat, mid, text, kb(rows)) if mid else send(chat, text, kb(rows))

def admin_channels(chat, mid=None):
    items = channels_list()
    lines = []
    for i, item in enumerate(items, 1):
        configured = bool(item.get("invite_url") or item.get("username"))
        status = "join link ready" if configured else "⚠️ invite link missing"
        lines.append(f"{i}. {esc(item.get('title', item['chat_id']))} <code>{item['chat_id']}</code> — {status}")
    text = "📡 <b>Channels & Groups</b>\n\n" + ("\n".join(lines) or "None")
    rows = [[btn("➕ Add Channel", "admin:add_channel")]]
    for item in items:
        title = str(item.get("title", item["chat_id"]))[:24]
        rows.append([btn(f"🔗 Set Link: {title}", f"channel:invite:{item['chat_id']}")])
        rows.append([btn(f"🗑️ Delete: {title}", f"channel:delete:{item['chat_id']}")])
    rows.append([btn("🔙 Back", "admin:panel")])
    edit(chat, mid, text, kb(rows)) if mid else send(chat, text, kb(rows))

def admin_dump_channel(chat, mid=None):
    channel_id, title = dump_channel_config()
    text = f"📥 <b>Dump Channel</b>\n\nCurrent: <b>{esc(title) if channel_id else 'Not configured'}</b>"
    if channel_id:
        text += f"\nID: <code>{esc(channel_id)}</code>"
    rows = [[btn("➕ Set Dump Channel", "admin:set_dump")], [btn("🔙 Back", "admin:panel")]]
    edit(chat, mid, text, kb(rows)) if mid else send(chat, text, kb(rows))

def handle_admin_input(message, state, text):
    uid, chat = message["from"]["id"], message["chat"]["id"]; action = state.get("action") if isinstance(state, dict) else state
    if action == "viewer_sticker":
        sticker = message.get("sticker")
        if not sticker:
            send(chat, "⚠️ Please send a Telegram sticker.")
            return True
        sticker_id = sticker.get("file_id")
        if settings is not None:
            settings.update_one({"_id": "config"}, {"$set": {"viewer_sticker": sticker_id}}, upsert=True)
        else:
            fallback_settings["viewer_sticker"] = sticker_id
        session(uid)
        send(chat, "✅ Viewer sticker saved.", kb([[btn("🔙 Back", "admin:panel")]]))
        return True
    if action == "dump_channel":
        origin = message.get("forward_from_chat"); fo = message.get("forward_origin", {})
        if not origin and fo.get("type") in {"chat", "channel"}:
            origin = fo.get("chat")
        if not origin and re.fullmatch(r"-?\d+", text):
            try:
                origin = tg("getChat", {"chat_id": int(text)})
            except Exception as exc:
                send(chat, f"⚠️ Channel ID verify nahi hua: <code>{esc(str(exc)[:180])}</code>")
                return True
        if not origin or not origin.get("id"):
            send(chat, "⚠️ Channel ka message forward karein ya numeric channel ID bhejein (example: <code>-1001234567890</code>).")
            return True
        configure_dump_channel(origin)
        session(uid)
        send(chat, f"✅ Dump channel set: <b>{esc(origin.get('title', 'Dump Channel'))}</b>\nBot ko us channel me admin rights zaroor dein.", kb([[btn("🔙 Back", "admin:panel")]]))
        return True
    if action == "channel_invite":
        chat_id = state.get("chat_id")
        if text.lower() == "skip":
            send(chat, "⚠️ Private channel/group ke liye invite link required hai. Link bheje bina user join nahi kar sakta.")
        elif re.fullmatch(r"https?://\S+", text):
            if channels is not None:
                channels.update_one({"chat_id": chat_id}, {"$set": {"invite_url": text}})
            else:
                for channel in fallback_channels:
                    if channel.get("chat_id") == chat_id:
                        channel["invite_url"] = text
                        break
            clear_membership_cache()
            session(uid); send(chat, "✅ Channel and invite link saved.", kb([[btn("🔙 Back", "admin:channels")]]))
        else:
            send(chat, "Send a valid invite link or type <code>skip</code>.")
        return True
    if action == "add_channel":
        origin = message.get("forward_from_chat"); fo = message.get("forward_origin", {})
        if not origin and fo.get("type") in {"chat", "channel"}: origin = fo.get("chat")
        if not origin: send(chat, "⚠️ Forward a message from the private/public channel or group here."); return True
        try:
            chat_info = tg("getChat", {"chat_id": origin["id"]}) or {}
            origin = {**origin, **chat_info}
        except Exception:
            LOG.info("Could not enrich forwarded chat %s", origin.get("id"))
        invite_url = origin.get("invite_link", "")
        if not invite_url and origin.get("username"):
            invite_url = f"https://t.me/{origin['username']}"
        channel = {"chat_id": origin["id"], "title": origin.get("title", "Channel / Group"), "username": origin.get("username", ""), "invite_url": invite_url, "created_at": now()}
        if channels is not None:
            channels.update_one({"chat_id": origin["id"]}, {"$set": channel}, upsert=True)
        else:
            fallback_channels[:] = [x for x in fallback_channels if x.get("chat_id") != origin["id"]]
            fallback_channels.append(channel)
        clear_membership_cache()
        if channel["invite_url"]:
            session(uid); send(chat, "✅ Channel/group added successfully.", kb([[btn("🔙 Back", "admin:channels")]]))
        else:
            session(uid, {"action": "channel_invite", "chat_id": origin["id"]})
            send(chat, "✅ Forward received. Send the channel/group invite link now. Private channels/groups require a valid invite link.")
        return True
    if action in {"add_admin", "remove_admin"}:
        if text.isdigit():
            admin_id = int(text)
            if admins is not None:
                admins.delete_one({"user_id": admin_id}) if action == "remove_admin" else admins.update_one({"user_id": admin_id}, {"$set": {"user_id": admin_id}}, upsert=True)
            elif action == "remove_admin":
                fallback_admins.discard(admin_id)
            else:
                fallback_admins.add(admin_id)
        session(uid); send(chat, "✅ Admin list updated.", kb([[btn("🔙 Back", "admin:admins")]])); return True
    if action == "diamond":
        p = text.split()
        if len(p) == 2 and p[0].isdigit() and re.fullmatch(r"-?\d+", p[1]):
            update_balance(int(p[0]), int(p[1])); session(uid); send(chat, "✅ User balance updated.", kb([[btn("🔙 Back", "admin:panel")]]))
        else: send(chat, "Format: <code>USER_ID AMOUNT</code> (Negative amount to deduct).")
        return True
    if action == "bonuses":
        p = text.split()
        if len(p) == 2 and all(x.isdigit() for x in p):
            if settings is not None:
                settings.update_one({"_id": "config"}, {"$set": {"welcome_bonus": int(p[0]), "refer_bonus": int(p[1])}}, upsert=True)
            else:
                fallback_settings.update({"welcome_bonus": int(p[0]), "refer_bonus": int(p[1])})
            session(uid); send(chat, "✅ Bonus values updated.", kb([[btn("🔙 Back", "admin:panel")]]))
        else: send(chat, "Format: <code>WELCOME_BONUS REFER_BONUS</code>")
        return True
    if action == "check":
        if text.isdigit():
            u = users.find_one({"user_id": int(text)}) or {} if users is not None else fallback_user(int(text))
            send(chat, f"👤 <b>User Details</b>\n\nID: <code>{text}</code>\nName: {esc(uname(u))}\nDiamonds: 💎 {u.get('diamonds',0)}\nReferrals: {u.get('referrals',0)}\nFirebase extracts: {u.get('extracts',0)}\nAPK Extracts: {u.get('apk_extracts',0)}\nWeb Extracts: {u.get('web_extracts',0)}", kb([[btn("🔙 Back", "admin:panel")]])); session(uid)
        return True
    if action in {"ban", "unban"}:
        if not text.isdigit():
            send(chat, "Send a numeric Telegram User ID.")
            return True
        target_uid = int(text)
        if target_uid in ADMIN_IDS:
            send(chat, "⚠️ Main admin cannot be banned.")
            return True
        banned = action == "ban"
        if users is not None:
            users.update_one({"user_id": target_uid}, {"$set": {"banned": banned}}, upsert=True)
        else:
            fallback_user(target_uid)["banned"] = banned
        session(uid)
        send(chat, f"{'🚫 User banned' if banned else '✅ User unbanned'}: <code>{target_uid}</code>", kb([[btn("🔙 Back", "admin:panel")]]))
        return True
    if action == "broadcast":
        recipients = [x["user_id"] for x in users.find({}, {"user_id": 1})] if users is not None else list(fallback_users)
        count = 0
        for target in recipients:
            try: 
                tg("copyMessage", {"chat_id": target, "from_chat_id": chat, "message_id": message["message_id"]})
                count += 1
            except Exception: pass
        session(uid); send(chat, f"✅ Broadcast sent to {count} users.", kb([[btn("🔙 Back", "admin:panel")]])); return True
    return False

def handle_image(message):
    if not is_private_chat(message):
        return
    uid, chat = message["from"]["id"], message["chat"]["id"]
    state = (users.find_one({"user_id": uid}) or {} if users is not None else fallback_user(uid)).get("session", {})
    if not is_admin(uid) or not isinstance(state, dict) or not message.get("photo"): return
    if state.get("action") == "broadcast":
        return handle_admin_input(message, state, "")
    if state.get("action") != "image":
        return
    if settings is not None:
        settings.update_one({"_id": "config"}, {"$set": {state["key"]: message["photo"][-1]["file_id"]}}, upsert=True)
    else:
        fallback_settings[state["key"]] = message["photo"][-1]["file_id"]
    session(uid)
    send(chat, "✅ Image saved successfully.", kb([[btn("🔙 Back", "admin:panel")]]))


def queue_apk_extraction(message):
    uid = message["from"]["id"]
    queue_full = False
    should_start = False
    with LIMIT_LOCK:
        queue = apk_queues.setdefault(uid, [])
        if len(queue) >= MAX_PENDING_APKS_PER_USER:
            queue_full = True
        else:
            queue.append(message)
            if uid not in apk_queue_workers:
                apk_queue_workers.add(uid)
                should_start = True
    if queue_full:
        send(message["chat"]["id"], "⏳ Your APK queue is full. Please wait for the current files to finish.")
        return
    if should_start and not submit_work(drain_apk_queue, uid):
        with LIMIT_LOCK:
            apk_queue_workers.discard(uid)
            apk_queues.pop(uid, None)
        send(message["chat"]["id"], "⚠️ The extraction queue is busy. Please send the APK again in a moment.")


def drain_apk_queue(uid):
    try:
        while True:
            with LIMIT_LOCK:
                queue = apk_queues.get(uid, [])
                message = queue.pop(0) if queue else None
                if not queue:
                    apk_queues.pop(uid, None)
            if message is None:
                return
            state = current_session(uid)
            if state is not None:
                session(uid, state)
            process_extraction(message)
    finally:
        restart_queue = False
        with LIMIT_LOCK:
            apk_queue_workers.discard(uid)
            if apk_queues.get(uid):
                apk_queue_workers.add(uid)
                restart_queue = True
        if restart_queue and not submit_work(drain_apk_queue, uid):
            with LIMIT_LOCK:
                apk_queue_workers.discard(uid)

# ═══════════════════════════════════════════════════════════════
# CALLBACK QUERY ROUTING
# ═══════════════════════════════════════════════════════════════
def callback(q, callback_answered=False):
    msg = q.get("message", {})
    if not is_private_chat(msg):
        if not callback_answered:
            answer(q, "Please use the bot in private chat.")
        return
    if not callback_answered:
        answer(q)
    time.sleep(CALLBACK_REPLY_DELAY_SECONDS)
    data = q.get("data", "").split(":"); action, value = data[0], data[1] if len(data) > 1 else ""
    uid = q["from"]["id"]; chat = msg.get("chat", {}).get("id", uid); mid = msg.get("message_id")
    try:
        if action == "sub" and value == "verify":
            unjoined = unjoined_channels(uid)
            if not unjoined:
                captcha(chat, uid, mid)
            else:
                sub_prompt(chat, mid, unjoined)
            return
        if action == "captcha":
            if not require_membership(chat, uid, mid):
                return
            cap = get_captcha(uid)
            valid = time.time() < cap.get("expires", 0) and int(value) == int(cap.get("answer", -1))
            complete_verification(chat, uid, mid) if valid else captcha(chat, uid, mid)
            return
        if not require_membership(chat, uid, mid):
            return
        if not is_admin(uid) and not is_verified(uid):
            captcha(chat, uid, mid)
            return
        if action == "viewerpage" and len(data) >= 4:
            page_key = data[2]
            try:
                page = int(data[3])
            except ValueError:
                return
            send_viewer_result_page(chat, page_key, page, mid)
        elif action == "history" and value == "download":
            urls = user_history_firebase_urls(uid)
            if not urls:
                edit(chat, mid, "🧾 <b>Your Extraction History</b>\n\nNo Firebase history found.", kb([[btn("🔙 Back", "nav:home")]]))
                return
            try:
                send_user_history_txt(chat, uid)
                answer(q, f"{len(urls)} unique Firebase URL(s) sent.")
            except Exception:
                LOG.exception("User history TXT delivery failed")
                send(chat, "❌ History TXT could not be sent right now. Please try again.")
        elif action == "nav":
            cancel_extract_timeout(uid)
            session(uid)
            welcome(chat, uid, mid)
        elif action == "menu":
            if value == "extract":
                prompt = edit(
                    chat,
                    mid,
                    "📤 <b>Send Target Data</b>\n\n"
                    "Send an <b>.APK file</b> document or a <b>Public Web/Firebase URL</b> now.\n"
                    "Only an APK or link will start extraction.",
                    kb([[btn("❌ Cancel", "nav:home")]]),
                )
                prompt_id = (prompt or {}).get("message_id") or mid
                started_at = time.time()
                session(uid, {"action": "extract", "message_id": prompt_id, "started_at": started_at})
                schedule_extract_timeout(chat, uid, prompt_id, started_at)
            elif value == "viewer":
                session(uid, {"action": "firebase_view"})
                edit(chat, mid, "📊 <b>Firebase Viewer</b>\n\nSend one or more Firebase Realtime Database URLs, a Base64-wrapped URL, or a .txt file.\n\n💎 Cost: <b>0.5 Diamond</b>", kb([[btn("❌ Cancel", "nav:home")]]))
            elif value == "profile":
                u = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
                u = u or {}
                edit(chat, mid, f"👤 <b>User Profile</b>\n\nName: {esc(uname(u))}\nID: <code>{uid}</code>\nBalance: 💎 {u.get('diamonds',0)} Diamonds\nReferrals: {u.get('referrals',0)}\nAPK Extracts: {u.get('apk_extracts',0)}\nWeb Extracts: {u.get('web_extracts',0)}", kb([[btn("🔙 Back", "nav:home")]]))
            elif value == "refer":
                link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"; edit(chat, mid, f"🎁 <b>Refer & Earn</b>\n\nEarn 💎 {cfg().get('refer_bonus',5)} Diamonds for every verified friend who joins!\n\nYour Link:\n<code>{link}</code>", kb([[{"text": "Share Link 🚀", "url": f"https://t.me/share/url?url={quote(link)}"}], [btn("🔙 Back", "nav:home")]]))
            elif value == "leaderboard":
                top = list(users.find().sort("diamonds", -1).limit(10)) if users is not None else sorted(fallback_users.values(), key=lambda x: x.get("diamonds", 0), reverse=True)[:10]
                edit(chat, mid, "🏆 <b>Leaderboard (Top Holders)</b>\n\n" + ("\n".join(f"{i+1}. {esc(uname(x))} — 💎 {x.get('diamonds',0)}" for i,x in enumerate(top)) or "No data yet."), kb([[btn("🔙 Back", "nav:home")]]))
            elif value == "buy": session(uid, "buy"); edit(chat, mid, "💎 <b>Buy Diamonds</b>\n\nEnter the amount you wish to buy.\nRate: ₹1 = 1 Diamond.", kb([[btn("❌ Cancel", "nav:home")]]))
            elif value == "history":
                rows = list(events.find({"user_id": uid}).sort("created_at", -1).limit(10)) if events is not None else [x for x in reversed(fallback_events) if x.get("user_id") == uid][:10]
                unique_urls = user_history_firebase_urls(uid)
                history_rows = [[btn("⬇️ Download My Unique Firebase TXT", "history:download")]] if unique_urls else []
                history_rows.append([btn("🔙 Back", "nav:home")])
                history_text = (
                    "🧾 <b>Your Extraction History</b>\n\n"
                    + ("\n".join(f"• [{(x.get('source') or 'unknown').upper()}] — <code>{esc(x.get('url',''))}</code>" for x in rows) or "No history found.")
                    + (f"\n\n🔗 Unique Firebase URLs: <b>{len(unique_urls)}</b>" if unique_urls else "")
                )
                edit(chat, mid, history_text, kb(history_rows))
            elif value == "support":
                if SUPPORT_URL:
                    edit(chat, mid, "💬 Contact our official support group below.", kb([[{"text": "Open Support Group 💬", "url": SUPPORT_URL}], [btn("🔙 Back", "nav:home")]]))
                else:
                    edit(chat, mid, "💬 Support group link is not configured yet.", kb([[btn("🔙 Back", "nav:home")]]))
        elif action == "order":
            oid = data[2]
            if value == "paid": 
                if orders is not None:
                    orders.update_one({"order_id": oid, "user_id": uid, "status": "created"}, {"$set": {"status": "awaiting_utr"}})
                elif oid in fallback_orders and fallback_orders[oid].get("user_id") == uid and fallback_orders[oid].get("status") == "created":
                    fallback_orders[oid]["status"] = "awaiting_utr"
                session(uid, "utr"); edit(chat, mid, "🧾 Send your <b>12-digit UTR/Transaction ID</b> now.", kb([[btn("❌ Cancel","nav:home")]]))
            elif value == "cancel": 
                if orders is not None:
                    orders.update_one({"order_id": oid, "user_id": uid, "status": "created"}, {"$set":{"status":"cancelled"}})
                elif oid in fallback_orders and fallback_orders[oid].get("user_id") == uid and fallback_orders[oid].get("status") == "created":
                    fallback_orders[oid]["status"] = "cancelled"
                delete_message(chat, mid)
                send(
                    chat,
                    "❌ <b>Order Cancelled</b>\n\nYour diamond order has been cancelled.",
                    kb([
                        [btn("🔙 Back to Menu", "nav:home")],
                        [btn("💎 Buy Diamonds", "menu:buy")],
                    ]),
                )
            elif value in {"approve","reject"} and is_admin(uid):
                if orders is not None:
                    o = orders.find_one_and_update({"order_id":oid,"status":"pending"},{"$set":{"status":"approved" if value=="approve" else "declined","reviewed_by":uid,"reviewed_at":now()}},return_document=ReturnDocument.AFTER)
                else:
                    o = fallback_orders.get(oid)
                    if o and o.get("status") == "pending":
                        o.update({"status": "approved" if value == "approve" else "declined", "reviewed_by": uid, "reviewed_at": now()})
                    else:
                        o = None
                if o and value == "approve": update_balance(o["user_id"], o["amount"])
                if o: send(o["user_id"], f"{'✅ APPROVED' if value=='approve' else '❌ DECLINED'}\nOrder <code>{oid}</code> has been {o['status']}."); edit(chat,mid,order_text(o),kb([[btn("🔙 Back","admin:orders")]]))
            elif value == "view" and is_admin(uid):
                o = orders.find_one({"order_id":oid}) if orders is not None else fallback_orders.get(oid)
                if o:
                    edit(chat,mid,order_text(o),kb([[btn("Approve ✅",f"order:approve:{oid}"),btn("Reject ❌",f"order:reject:{oid}")],[btn("🔙 Back","admin:orders")]]))
        elif action == "admin" and is_admin(uid):
            if value == "panel": admin_panel(chat,mid)
            elif value == "channels": admin_channels(chat,mid)
            elif value == "dump": admin_dump_channel(chat,mid)
            elif value == "set_dump": session(uid, {"action": "dump_channel"}); edit(chat, mid, "📥 <b>Set Dump Channel</b>\n\nForward any message from the target channel here, or send its numeric ID.\n\nBot ko channel me message bhejne ki permission honi chahiye.", kb([[btn("🔙 Back", "admin:dump")]]))
            elif value == "add_channel": session(uid,{"action":"add_channel"}); edit(chat,mid,"📩 Forward any message from the private/public channel or group here.",kb([[btn("🔙 Back","admin:channels")]]))
            elif value == "admins":
                a = list(admins.find().sort("user_id",1)) if admins is not None else [{"user_id": x} for x in sorted(ADMIN_IDS | fallback_admins)]
                edit(chat,mid,"👮 <b>Admin List</b>\n\n"+("\n".join(f"• <code>{x['user_id']}</code>" for x in a) or "None"),kb([[btn("➕ Add Admin","admin:add_admin"),btn("➖ Remove Admin","admin:remove_admin")],[btn("🔙 Back","admin:panel")]]))
            elif value in {"add_admin","remove_admin"}: session(uid,{"action":value}); edit(chat,mid,"Send Telegram User ID.",kb([[btn("🔙 Back","admin:admins")]]))
            elif value == "orders":
                o = list(orders.find({"status":"pending"}).sort("created_at",-1).limit(20)) if orders is not None else [x for x in fallback_orders.values() if x.get("status") == "pending"][:20]
                edit(chat,mid,"🛎️ <b>Pending Deposit Orders</b>\n\n"+("\n".join(f"• {x['order_id']} — ₹{x['amount']}" for x in o) or "None"),kb([[btn(f"Open {x['order_id']}",f"order:view:{x['order_id']}")] for x in o]+[[btn("🔙 Back","admin:panel")]]))
            elif value == "history":
                e = list(events.find().sort("created_at",-1).limit(20)) if events is not None else list(reversed(fallback_events))[:20]
                edit(chat,mid,"🌐 <b>Global Extractions</b>\n\n"+("\n".join(f"• ID: {x.get('user_id')} [{x.get('source').upper()}] {esc(x.get('url',''))}" for x in e) or "None"),kb([[btn("🔙 Back","admin:panel")]]))
            elif value == "diamond": session(uid,{"action":"diamond"}); edit(chat,mid,"Send <code>USER_ID AMOUNT</code> (Negative value removes diamonds).",kb([[btn("🔙 Back","admin:panel")]]))
            elif value == "bonuses": session(uid,{"action":"bonuses"}); edit(chat,mid,f"Current Bonuses:\nWelcome: {cfg().get('welcome_bonus',10)} | Refer: {cfg().get('refer_bonus',5)}\n\nSend <code>WELCOME_BONUS REFER_BONUS</code>.",kb([[btn("🔙 Back","admin:panel")]]))
            elif value == "firebase":
                admin_firebase_menu(chat, mid)
            elif value in {"firebase_all_num", "firebase_all_plain", "firebase_active_num", "firebase_active_plain"}:
                try:
                    numbered = value.endswith("num")
                    active_only = "active" in value
                    title = "Active Firebase URLs" if active_only else "All Firebase URLs"
                    urls = admin_firebase_active_urls() if active_only else admin_firebase_urls()
                    if not urls:
                        edit(chat, mid, f"🔥 <b>{title}</b>\n\nNo Firebase URL found.", kb([[btn("🔙 Back", "admin:firebase")]]))
                        return
                    send_admin_firebase_txt(chat, title, urls, numbered)
                    edit(chat, mid, f"🔥 <b>{title}</b>\n\n{len(urls)} URL(s) exported.\n\nFormat: {'Numbered' if numbered else 'Line by line'}.", kb([[btn("🔙 Back", "admin:firebase")]]))
                except Exception as exc:
                    LOG.exception("Firebase TXT export failed")
                    send(chat, f"❌ Firebase export failed: <code>{esc(str(exc)[:220])}</code>", kb([[btn("🔙 Back", "admin:firebase")]]))
            elif value == "check": session(uid,{"action":"check"}); edit(chat,mid,"Send User ID.",kb([[btn("🔙 Back","admin:panel")]]))
            elif value == "ban": session(uid,{"action":"ban"}); edit(chat,mid,"🚫 Send the Telegram User ID to ban.",kb([[btn("🔙 Back","admin:panel")]]))
            elif value == "unban": session(uid,{"action":"unban"}); edit(chat,mid,"✅ Send the Telegram User ID to unban.",kb([[btn("🔙 Back","admin:panel")]]))
            elif value == "broadcast": session(uid,{"action":"broadcast"}); edit(chat,mid,"Send text, photo, document or video to broadcast to all users.",kb([[btn("🔙 Back","admin:panel")]]))
            elif value == "images": edit(chat,mid,"🖼️ Select Image Setting:",kb([[btn("QR Payment","image:qr_file_id")],[btn("Welcome Image","image:welcome_image")],[btn("Panel Image","image:panel_image")],[btn("Viewer Sticker","admin:viewer_sticker")],[btn("🔙 Back","admin:panel")]]))
            elif value == "viewer_sticker": session(uid, {"action": "viewer_sticker"}); edit(chat,mid,"🎭 Send the sticker to use while Firebase Viewer is checking.",kb([[btn("🔙 Back","admin:panel")]]))
            elif value == "status":
                total_apk = sum(x.get("apk_extracts",0) for x in users.find({},{"apk_extracts":1})) if users is not None else 0
                total_web = sum(x.get("web_extracts",0) for x in users.find({},{"web_extracts":1})) if users is not None else 0
                edit(chat,mid,f"📊 <b>System Statistics</b>\n\nUsers: {users.count_documents({}) if users is not None else len(fallback_users)}\nAPK Extractions: {total_apk if users is not None else sum(x.get('apk_extracts', 0) for x in fallback_users.values())}\nWeb Extractions: {total_web if users is not None else sum(x.get('web_extracts', 0) for x in fallback_users.values())}\nUnique Firebase: {firebase.count_documents({}) if firebase is not None else len(fallback_firebase)}\nChannels Monitored: {channels.count_documents({}) if channels is not None else len(fallback_channels)}",kb([[btn("🔙 Back","admin:panel")]]))
        elif action == "channel" and value == "invite" and is_admin(uid):
            chat_id = int(data[2])
            session(uid, {"action": "channel_invite", "chat_id": chat_id})
            edit(chat, mid, "🔗 <b>Set Channel/Group Invite Link</b>\n\nSend a public <code>https://t.me/...</code> link or private invite link now.", kb([[btn("🔙 Back", "admin:channels")]]))
        elif action == "channel" and value == "delete" and is_admin(uid):
            if channels is not None:
                channels.delete_one({"chat_id":int(data[2])})
            else:
                fallback_channels[:] = [x for x in fallback_channels if x.get("chat_id") != int(data[2])]
            clear_membership_cache()
            admin_channels(chat,mid)
        elif action == "image" and is_admin(uid): session(uid,{"action":"image","key":value}); edit(chat,mid,"📷 Send the image photo now.",kb([[btn("🔙 Back","admin:images")]]))
    except Exception: LOG.exception("callback failed: %s", q.get("data"))

# ═══════════════════════════════════════════════════════════════
# MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════
def handle_message(msg_obj):
    if not is_private_chat(msg_obj):
        return
    user = msg_obj.get("from")
    if not user: return
    uid, chat = user["id"], msg_obj["chat"]["id"]; u = ensure_user(user); text = msg_obj.get("text","").strip()
    if is_banned(uid) and not is_admin(uid):
        send(chat, "🚫 <b>You are blocked from using this bot.</b>")
        return
    
    if text.startswith("/start"): 
        return start(msg_obj, text.split(maxsplit=1)[1] if len(text.split(maxsplit=1))>1 else "")
    if text.startswith("/admin"):
        return admin_panel(chat) if is_admin(uid) else send(chat, "❌ Unauthorized.")

    if not require_membership(chat, uid):
        return
    if not is_admin(uid) and not is_verified(uid):
        return captcha(chat, uid)

    state = u.get("session")
    state_action = state.get("action") if isinstance(state, dict) else state
    if msg_obj.get("photo") and is_admin(uid) and isinstance(state, dict) and state.get("action") == "broadcast":
        return handle_admin_input(msg_obj, state, "")
    if (msg_obj.get("document") or msg_obj.get("audio")) and not (
        is_admin(uid) and isinstance(state, dict) and state.get("action") == "broadcast"
    ):
        if state_action == "extract":
            doc = msg_obj.get("document")
            file_name = doc.get("file_name", "").lower() if doc else ""
            is_apk = bool(doc and (file_name.endswith(".apk") or doc.get("mime_type") == "application/vnd.android.package-archive"))
            if is_apk:
                return queue_apk_extraction(msg_obj)
            return process_extraction(msg_obj)
        if state_action == "firebase_view" and msg_obj.get("document"):
            doc = msg_obj["document"]
            file_name = doc.get("file_name", "").lower()
            is_text_file = file_name.endswith((".txt", ".text", ".log")) or doc.get("mime_type", "").startswith("text/")
            if not is_text_file:
                send(chat, "⚠️ Please send a valid <b>.txt</b> file or paste Firebase links as text.")
                return
            path = None
            try:
                path = download(doc["file_id"])
                text_content = path.read_text(encoding="utf-8", errors="ignore")[:MAX_VIEWER_SCAN]
                return process_firebase_view({**msg_obj, "text": text_content})
            except Exception as exc:
                send(chat, f"❌ Could not read this text file: <code>{esc(str(exc)[:200])}</code>")
            finally:
                if path:
                    path.unlink(missing_ok=True)
            return
        return
    if is_admin(uid) and state and handle_admin_input(msg_obj, state, text): return

    if state_action == "extract":
        if not looks_like_url(text):
            send(chat, "⚠️ Please send an APK file or a valid public web/Firebase URL.")
            return
        return process_extraction(msg_obj)

    if state_action == "firebase_view":
        # Do not gate this on a generic URL regex: Base64-wrapped Firebase
        # links and messages containing several endpoints are valid viewer
        # inputs too. The viewer parser is the source of truth.
        if not firebase_targets_from_text(text):
            send(chat, "⚠️ Please send a valid Firebase Realtime Database URL.")
            return
        return process_firebase_view(msg_obj)

    if state == "buy":
        if not text.isdigit() or not 1 <= int(text) <= 100000: return send(chat,"Send a valid number from 1 to 100000.")
        session(uid); return create_order(chat, uid, int(text))

    if state == "utr":
        if not re.fullmatch(r"\d{12}", text): return send(chat,"⚠️ UTR must contain exactly 12 digits.")
        if orders is not None:
            o = orders.find_one_and_update({"user_id":uid,"status":"awaiting_utr"},{"$set":{"utr":text,"status":"pending","utr_at":now()}},sort=[("created_at",-1)],return_document=ReturnDocument.AFTER)
        else:
            pending = [x for x in fallback_orders.values() if x.get("user_id") == uid and x.get("status") == "awaiting_utr"]
            o = max(pending, key=lambda x: x.get("created_at", now()), default=None)
            if o:
                o.update({"utr": text, "status": "pending", "utr_at": now()})
        session(uid)
        send(chat,"✅ UTR submitted! Admin will verify and credit your balance within 24 hours.",kb([[btn("💬 Support Group","menu:support")]]))
        if o:
            recipients = set(ADMIN_IDS)|{x["user_id"] for x in admins.find({},{"user_id":1})} if admins is not None else set(ADMIN_IDS)
            for admin in recipients:
                try: send(admin, order_text(o), kb([[btn("Approve ✅",f"order:approve:{o['order_id']}"),btn("Reject ❌",f"order:reject:{o['order_id']}")]]))
                except Exception: pass
        return

    # Do not treat arbitrary private-chat messages as extraction requests.
    # Users must explicitly choose Extract Firebase or Firebase Viewer first.
    return

# ═══════════════════════════════════════════════════════════════
# HEALTH CHECK & MAIN POLLING LOOP
# ═══════════════════════════════════════════════════════════════
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok","service":"firebase-bot"}' if self.path in {"/healthz","/api/healthz"} else b""
        self.send_response(200 if body else 404); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass

def flush_media_group(group_id):
    group_messages = pending_media_groups.pop(group_id, [])
    timer = media_group_timers.pop(group_id, None)
    if timer:
        timer.cancel()
    if not group_messages:
        return
    if all(msg.get("document") for msg in group_messages):
        submit_user_work(process_media_group, group_messages)
        return
    for msg in group_messages:
        submit_user_work(handle_image if msg.get("photo") else handle_message, msg)


def process_media_group(messages):
    """Process album documents serially so one file cannot clear the next file's session."""
    if not messages:
        return
    first = messages[0]
    uid = (first.get("from") or {}).get("id")
    if not uid:
        return
    state = current_session(uid)
    for message in messages:
        if state is not None:
            session(uid, state)
        handle_message(message)


def queue_media_group_message(msg):
    group_id = msg.get("media_group_id")
    if not group_id or not msg.get("document"):
        return False
    group_messages = pending_media_groups.setdefault(group_id, [])
    group_messages.append(msg)
    if group_id in media_group_timers:
        return True
    timer = threading.Timer(0.8, flush_media_group, args=(group_id,))
    timer.daemon = True
    media_group_timers[group_id] = timer
    timer.start()
    return True


def restart_process(reason):
    LOG.critical("%s; restarting bot process", reason)
    try:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except OSError:
        LOG.exception("Could not replace the bot process; exiting for the host to restart it")
        os._exit(1)


def restart_on_main_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    LOG.critical(
        "Uncaught main-thread exception",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    restart_process("Uncaught main-thread exception")


def restart_on_thread_exception(args):
    if issubclass(args.exc_type, SystemExit):
        return
    LOG.critical(
        "Uncaught exception in thread %s",
        args.thread.name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
    restart_process(f"Uncaught exception in thread {args.thread.name}")


def install_crash_restart_hooks():
    sys.excepthook = restart_on_main_exception
    threading.excepthook = restart_on_thread_exception


def bot_watchdog():
    while True:
        time.sleep(30)
        if time.monotonic() - last_poll_at > 180:
            restart_process("Polling appears frozen")


def main():
    global BOT_USERNAME
    install_crash_restart_hooks()
    if not TOKEN: LOG.error("Configure TELEGRAM_BOT_TOKEN in environment variables."); return
    setup_db(); BOT_USERNAME = (tg("getMe") or {}).get("username","your_bot")
    threading.Thread(target=backup_loop, daemon=True, name="mongodb-backup").start()
    
    port = int(os.getenv("PORT", "8099"))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        LOG.info("Health server listening on port %s", port)
    except OSError as exc:
        LOG.warning("Health server could not bind port %s: %s. Continuing with Telegram bot.", port, exc)
    tg("deleteWebhook",{"drop_pending_updates":False})
    LOG.info("⚡ Bot live as @%s", BOT_USERNAME)
    threading.Thread(target=bot_watchdog, daemon=True, name="bot-watchdog").start()
    
    offset = 0
    while True:
        try:
            updates = tg("getUpdates",{"offset":offset+1,"timeout":25,"allowed_updates":["message","callback_query"]},timeout=35) or []
            global last_poll_at
            last_poll_at = time.monotonic()
            for update in updates:
                offset = update["update_id"]
                if update.get("callback_query"): 
                    submit_callback(update["callback_query"])
                elif update.get("message"):
                    message = update["message"]
                    if queue_media_group_message(message):
                        continue
                    if message.get("photo"):
                        submit_user_work(handle_image, message)
                    else:
                        submit_user_work(handle_message, message)
        except HTTPError as exc:
            if exc.code == 409:
                LOG.warning(
                    "Telegram polling conflict (409): another instance is using this bot token. "
                    "Stop the other instance; retrying in 15 seconds."
                )
                time.sleep(15)
            else:
                LOG.warning("Polling HTTP retry: %s", exc)
                time.sleep(3)
        except Exception as exc:
            LOG.warning("Polling retry: %s", exc)
            time.sleep(3)

if __name__ == "__main__": 
    main()
