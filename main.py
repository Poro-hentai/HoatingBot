#!/usr/bin/env python3
"""
=============================================================
   🔥 ULTIMATE TELEGRAM FIREBASE & APK EXTRACTOR BOT 🔥
=============================================================
"""

import base64
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
import gc
import hashlib
import html
import io
import json
import logging
import os
import random
import re
import secrets
import socket
socket.setdefaulttimeout(2.5)
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

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8216354175:AAEMEq1EwDKj6lgefbkPIR5MhEU3hef9VUo").strip()
if TOKEN == "8216354175:AAH_pilA68YbcWAE1vgXcwmlZK92loHaJvU" or not TOKEN:
    TOKEN = "8216354175:AAEMEq1EwDKj6lgefbkPIR5MhEU3hef9VUo"
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

MAX_FILE = 50 * 1024 * 1024  # Extended to 100MB File Limit
MAX_ENTRY = 20 * 1024 * 1024
MAX_SCAN = 40 * 1024 * 1024
MAX_VIEWER_SCAN = 50 * 1024 * 1024
MAX_VIEWER_TARGETS = max(1, env_int("MAX_VIEWER_TARGETS", 100000))
MAX_PENDING_APKS_PER_USER = max(1, env_int("MAX_PENDING_APKS_PER_USER", 3))
MAX_QUEUED_WORK = max(8, env_int("MAX_QUEUED_WORK", 128))
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
WORKERS = ThreadPoolExecutor(max_workers=20, thread_name_prefix="bot-worker")
WORK_DISPATCH_SLOTS = threading.BoundedSemaphore(MAX_QUEUED_WORK)
EXTRACTION_SLOTS = threading.BoundedSemaphore(4)
VIEWER_SLOTS = threading.BoundedSemaphore(12)
LIMIT_LOCK = threading.Lock()
active_extraction_users = set()
active_viewer_users = set()
active_dedup_users = set()
active_sister_users = set()
sister_tracker_cache = {}
sister_cache_lock = threading.Lock()
SISTER_WORKERS = ThreadPoolExecutor(max_workers=3, thread_name_prefix="sister-worker")

fallback_watchlist = {}
fallback_daily_alert = {}
fallback_last_alert = {}
active_scan_cancellations = {}
scan_cancel_lock = threading.Lock()
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

def send_document(chat_id, file_path, caption=None):
    boundary = "----FirebaseBackupBoundary"
    content = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode()]
    if caption:
        content.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode())
        content.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n".encode())
    mime_type = "text/plain" if Path(file_path).suffix.lower() in {".txt", ".text", ".log"} else "application/json"
    content.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{Path(file_path).name}\"\r\nContent-Type: {mime_type}\r\n\r\n".encode() + Path(file_path).read_bytes() + b"\r\n")
    content.append(f"--{boundary}--\r\n".encode())
    response = urlopen(Request(f"{API}/sendDocument", data=b"".join(content), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}), timeout=45)
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
        with tempfile.NamedTemporaryFile(prefix="firebase-backup-", suffix=".json", mode="w", encoding="utf-8", delete=False) as handle:
            backup_path = Path(handle.name)
            handle.write('{\n  "created_at": "' + now().isoformat() + '",\n  "database": "' + str(db.name) + '",\n  "collections": {\n')
            colls = db.list_collection_names()
            for ci, collection_name in enumerate(colls):
                handle.write(f'    "{collection_name}": [\n')
                cursor = db[collection_name].find({})
                first = True
                for doc in cursor:
                    if not first:
                        handle.write(',\n')
                    first = False
                    handle.write('      ' + bson_dumps(doc))
                handle.write('\n    ]' + (',' if ci < len(colls) - 1 else '') + '\n')
            handle.write('  }\n}\n')
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
        gc.collect()

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

def answer(query, text="", alert=False):
    if query.get("_answered"):
        return
    query["_answered"] = True
    payload = {"callback_query_id": query["id"]}
    if text:
        payload["text"] = text[:190]
        if alert:
            payload["show_alert"] = True
    try:
        tg("answerCallbackQuery", payload, timeout=3)
    except Exception:
        pass

def is_process_running(uid):
    with LIMIT_LOCK:
        return (uid in active_viewer_users) or (uid in active_extraction_users) or (uid in active_dedup_users) or (uid in active_sister_users)

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

def unjoined_channels(uid):
    channels_to_check = channels_list()
    if not channels_to_check:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(channels_to_check)), thread_name_prefix="membership-check") as pool:
        joined = list(pool.map(lambda channel: channel_member_status(channel, uid), channels_to_check))
    return [channel for channel, is_joined in zip(channels_to_check, joined) if not is_joined]

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
    unjoined = unjoined_channels(uid)
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
    rows = [
        [btn("📤 Extract", "menu:extract", "success"), btn("📊 Viewer", "menu:viewer", "primary")],
        [btn("🧹 Remove Duplicates (.txt) 💎1", "menu:dedup", "success"), btn("⏳ Watchlist & Status", "menu:watchlist", "primary")],
        [btn("👤 Profile", "menu:profile", "success"), btn("🎁 Refer & Earn", "menu:refer", "success")],
        [btn("🏆 Leaderboard", "menu:leaderboard", "primary"), btn("💎 Buy Diamonds", "menu:buy", "success")],
        [btn("🧾 History", "menu:history", "primary"), btn("💬 Support", "menu:support", "primary")]
    ]
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
    with LIMIT_LOCK:
        active_viewer_users.discard(uid)
        active_extraction_users.discard(uid)
        active_dedup_users.discard(uid)
        active_sister_users.discard(uid)
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
        if host in {"firebaseio.com", "firebasedatabase.app", "firebaseapp.com", "your-project.firebaseio.com", "your-project-default-rtdb.firebaseio.com"}:
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

            # Auto-decode web panel share tokens (e.g. ZXKAI / ?s= parameter)
            if "s=" in url:
                parsed_u = urlparse(url)
                qs = parse_qs(parsed_u.query)
                if "s" in qs and qs["s"]:
                    s_token = qs["s"][0]
                    api_endpoint = urljoin(url, "/api/decode-share")
                    try:
                        req_data = json.dumps({"s": s_token}).encode("utf-8")
                        req_share = Request(api_endpoint, data=req_data, headers={"Content-Type": "application/json", "User-Agent": headers["User-Agent"]})
                        with urlopen(req_share, timeout=5) as r_share:
                            res_json = json.loads(r_share.read().decode("utf-8", errors="ignore"))
                            if res_json.get("success") and res_json.get("url"):
                                normalized = normalize_firebase_url(res_json["url"])
                                if normalized:
                                    found.add(normalized)
                    except Exception:
                        pass
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
                            raise RuntimeError("File size exceeds 50MB limit!")
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

def get_dump_sent_urls(candidate_urls):
    if not candidate_urls:
        return set()
    found = set()
    for u in candidate_urls:
        if u in fallback_dump_sent:
            found.add(u)
    if db is not None:
        try:
            docs = db.dump_sent.find({"url": {"$in": candidate_urls}}, {"url": 1})
            for d in docs:
                u = d.get("url")
                if u:
                    found.add(u)
                    fallback_dump_sent.add(u)
        except Exception:
            pass
    if settings is not None:
        try:
            cfg_urls = set(cfg().get("dump_sent_urls", []))
            for u in candidate_urls:
                if u in cfg_urls:
                    found.add(u)
                    fallback_dump_sent.add(u)
        except Exception:
            pass
    return found

def mark_dump_sent_urls(sent_urls):
    if not sent_urls:
        return
    fallback_dump_sent.update(sent_urls)
    if db is not None:
        try:
            ops = [pymongo.UpdateOne({"url": u}, {"$setOnInsert": {"url": u, "sent_at": now()}}, upsert=True) for u in sent_urls]
            if ops:
                db.dump_sent.bulk_write(ops, ordered=False)
        except Exception:
            pass
    if settings is not None:
        try:
            settings.update_one({"_id": "config"}, {"$addToSet": {"dump_sent_urls": {"$each": sent_urls[:500]}}}, upsert=True)
        except Exception:
            pass

def send_to_dump_channel(uid, urls, source):
    channel_id, channel_title = dump_channel_config()
    target_channels = []
    if channel_id:
        try:
            target_channels.append(int(channel_id))
        except Exception:
            if str(channel_id).startswith("@"):
                target_channels.append(str(channel_id))
    if ADMIN_DUMP_CHANNEL and ADMIN_DUMP_CHANNEL not in target_channels:
        target_channels.append(ADMIN_DUMP_CHANNEL)
    if USER_DUMP_CHANNEL and USER_DUMP_CHANNEL not in target_channels:
        target_channels.append(USER_DUMP_CHANNEL)

    if not target_channels or not urls:
        return 0

    clean_urls = clean_firebase_url_list(urls)
    if not clean_urls:
        return 0

    already_sent = get_dump_sent_urls(clean_urls)
    new_urls = [u for u in clean_urls if u not in already_sent]
    if not new_urls:
        return 0

    u = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
    user_name = esc(uname(u or {"user_id": uid}))
    total_new = len(new_urls)

    if total_new <= 10:
        lines = "\n".join(f"{index}. <code>{esc(url)}</code>" for index, url in enumerate(new_urls, 1))
        text = (
            f"🔥 <b>New Firebase Discovered!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> {user_name} (<code>{uid}</code>)\n"
            f"📂 <b>Source:</b> <code>{source.upper()}</code>\n"
            f"🎯 <b>Fresh Databases ({total_new}):</b>\n\n"
            f"{lines}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"✨ <i>100% Fresh & Unposted Firebase</i>"
        )
        for ch in target_channels:
            try:
                tg("sendMessage", {
                    "chat_id": ch,
                    "text": text[:4090],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
            except Exception:
                LOG.exception("Dump channel text delivery failed for %s", ch)
    else:
        temp_dir = tempfile.gettempdir()
        temp_file = Path(temp_dir) / f"New_Firebase_{source.upper()}_{int(time.time())}.txt"
        temp_file.write_text("\n".join(new_urls), encoding="utf-8")
        caption = (
            f"📁 <b>New Firebase Bulk Dump ({total_new:,} URLs)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> {user_name} (<code>{uid}</code>)\n"
            f"📂 <b>Source:</b> <code>{source.upper()}</code>\n"
            f"🎯 <b>Total Fresh Databases:</b> <code>{total_new:,}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"✨ <i>All fresh unique Firebase URLs attached in file.</i>"
        )
        for ch in target_channels:
            try:
                send_document(ch, temp_file, caption=caption)
            except Exception:
                LOG.exception("Dump channel document delivery failed for %s", ch)
        try:
            temp_file.unlink(missing_ok=True)
        except Exception:
            pass

    mark_dump_sent_urls(new_urls)
    return total_new

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
    seen = set()
    raw_text = text or ""
    # For large text / files (e.g. 10,000+ links), use fast bulk regex scanning
    if len(raw_text) > 20000:
        raw_urls = re.findall(r"https?://[^\s<>\"'`|]+|(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.)?(?:firebaseio\.com|firebasedatabase\.app|firebaseapp\.com)(?:/[^\s<>\"'`|]*)?", raw_text, re.I)
        for raw in raw_urls:
            raw = raw.strip()
            if not raw:
                continue
            clean = normalize_firebase_url(raw if raw.startswith(("http://", "https://")) else "https://" + raw)
            if clean and clean not in seen:
                seen.add(clean)
                targets.append(clean)
        if targets:
            return targets

    source_values = [raw_text] + extract_input_urls(raw_text)
    for source_url in source_values:
        candidates = endpoint_urls(source_url)
        if not candidates and looks_like_firebase_url(source_url):
            candidates = endpoint_urls("https://" + source_url)
        for target in candidates:
            if target not in seen:
                seen.add(target)
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

def send_active_firebase_txt(chat, urls):
    temp_dir = tempfile.gettempdir()
    filepath = Path(temp_dir) / f"Active_Firebase_{int(time.time())}.txt"
    try:
        content = "\n".join(urls)
        filepath.write_text(content, encoding="utf-8")
        caption = (
            f"🟢 <b>Active Firebase Found ({len(urls):,})</b>\n\n"
            f"Ye lo aapki saari working/active Firebase URLs ki list.\n"
            f"Total Active: <b>{len(urls):,}</b>"
        )
        send_document(chat, filepath, caption=caption)
        return True
    except Exception as exc:
        LOG.exception("Failed to send active firebase txt")
        send(chat, f"❌ Active file send karne me error: <code>{esc(str(exc)[:200])}</code>")
        return False
    finally:
        if filepath.exists():
            try:
                filepath.unlink()
            except Exception:
                pass

def send_protected_firebase_txt(chat, urls):
    temp_dir = tempfile.gettempdir()
    filepath = Path(temp_dir) / f"Protected_Firebase_{int(time.time())}.txt"
    try:
        content = "\n".join(urls)
        filepath.write_text(content, encoding="utf-8")
        caption = (
            f"⚠️ <b>Protected By Auth Firebase ({len(urls):,})</b>\n\n"
            f"Ye URLs active hain par Auth/Rules se protected hain.\n"
            f"Total Protected: <b>{len(urls):,}</b>"
        )
        send_document(chat, filepath, caption=caption)
        return True
    except Exception:
        return False
    finally:
        if filepath.exists():
            try:
                filepath.unlink()
            except Exception:
                pass

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


def send_viewer_result_pages(chat, active_results, protected_results, inactive_results):
    """Send grouped viewer results, keeping each Telegram page compact."""
    expiry = time.time() - 3600
    for old_key, old_state in list(viewer_result_pages.items()):
        if old_state.get("created_at", 0) < expiry:
            viewer_result_pages.pop(old_key, None)
    for category, title, records in (
        ("active", "🟢 Active Firebase URLs", active_results),
        ("protected", "⚠️ Protected By Auth Firebase URLs", protected_results),
        ("inactive", "🔴 Deactivated / Unavailable Firebase URLs", inactive_results),
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
                f"   📊 Total: <b>{total}</b> | 🟢 <b>{online}</b> | 🔴 <b>{offline}</b>"
            )
    elif state["category"] == "protected":
        for index, record in enumerate(selected, page * page_size + 1):
            target = record[0]
            lines.append(
                f"{index}. <code>{esc(target)}</code>\n"
                f"   ⚠️ <b>Protected By Auth</b> (Firebase is active and you can use it only using correct auth/password)"
            )
    else:
        for index, (target, reason) in enumerate(selected, page * page_size + 1):
            lines.append(f"{index}. <code>{esc(target)}</code>\n   🔴 {esc(reason)}")
    markup = None
    if total_pages > 1:
        controls = []
        if page > 0:
            controls.append(btn("⬅️ Back", f"viewerpage:{state['category']}:{page_key}:{page - 1}"))
        if page < total_pages - 1:
            controls.append(btn("Next ➡️", f"viewerpage:{state['category']}:{page_key}:{page + 1}"))
        markup = kb([controls])
    prefix_icon = '🟢' if state['category'] == 'active' else ('⚠️' if state['category'] == 'protected' else '🔴')
    text = f"{prefix_icon} <b>{state['title']}</b> ({page + 1}/{total_pages})\n\n" + "\n".join(lines)
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

DEVICE_NODE_CANDIDATES = (
    "clients", "devices", "All_Users", "All_User", "users",
    "registeredDevices", "bots", "agents", "victims", "client",
    "Device", "Clients", "device_list", "Devices", "user_list"
)

def firebase_status_summary(data):
    if not isinstance(data, dict):
        return 0, 0, 0, 0
    total = 0
    online = 0
    now = time.time()
    for device in data.values():
        if not isinstance(device, dict):
            continue
        total += 1
        is_on = False
        state = next((device[key] for key in ONLINE_KEYS if key in device), None)
        if state is not None:
            if isinstance(state, bool):
                is_on = state
            elif isinstance(state, (int, float)):
                is_on = bool(state)
            elif isinstance(state, str):
                norm = state.strip().lower()
                if norm in {"online", "active", "connected", "on", "true", "1", "yes"}:
                    is_on = True
        elif "ping" in device and isinstance(device["ping"], dict):
            pts = device["ping"].get("ts")
            if pts:
                try:
                    pts_sec = pts / 1000.0 if pts > 1e11 else float(pts)
                    if abs(now - pts_sec) < 900:  # 15 minutes window
                        is_on = True
                except Exception:
                    pass
        elif "last_seen" in device:
            ls = device.get("last_seen")
            try:
                ls_sec = ls / 1000.0 if ls > 1e11 else float(ls)
                if abs(now - ls_sec) < 900:
                    is_on = True
            except Exception:
                pass

        if is_on:
            online += 1

    offline = max(0, total - online)
    return total, online, offline, 0

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
    headers = {"Cache-Control": "no-cache", "User-Agent": "firebase-viewer/1.0", "Connection": "close", "Accept": "application/json"}
    parsed = urlparse(base_url)
    root_url = parsed._replace(path="", params="", query="", fragment="").geturl().rstrip("/")
    custom_path = parsed.path.strip("/")

    # If user provided a specific sub-node path (e.g. /clients)
    if custom_path:
        try:
            req = Request(f"{root_url}/{custom_path}.json", headers=headers)
            with urlopen(req, timeout=3.5) as response:
                raw = response.read(4194304).decode("utf-8", errors="ignore")
                data = json.loads(raw) if raw else None
            if isinstance(data, dict):
                tot, on, off, unk = firebase_status_summary(data)
                return (tot if tot > 0 else len(data), on, off, unk)
            return (0, 0, 0, 0)
        except Exception:
            pass

    # 1. Root shallow probe (detects active/protected/dead in fast call)
    try:
        req = Request(root_url + "/.json?shallow=true", headers=headers)
        with urlopen(req, timeout=2.5) as response:
            raw = response.read(65536).decode("utf-8", errors="ignore")
            root_data = json.loads(raw) if raw else None

        if isinstance(root_data, dict):
            # Check if any known device collection key exists at root
            matched_key = None
            lower_map = {k.lower(): k for k in root_data.keys()}
            for candidate in DEVICE_NODE_CANDIDATES:
                if candidate.lower() in lower_map:
                    matched_key = lower_map[candidate.lower()]
                    break

            if matched_key:
                try:
                    dev_req = Request(f"{root_url}/{matched_key}.json", headers=headers)
                    with urlopen(dev_req, timeout=4.0) as dev_resp:
                        dev_raw = dev_resp.read(4194304).decode("utf-8", errors="ignore")
                        try:
                            dev_data = json.loads(dev_raw) if dev_raw else None
                        except Exception:
                            dev_data = None

                    if isinstance(dev_data, dict):
                        tot, on, off, unk = firebase_status_summary(dev_data)
                        return (tot if tot > 0 else len(dev_data), on, off, unk)
                    elif dev_raw:
                        # Fallback fast regex scan on raw device JSON
                        on = len(re.findall(r'"status"\s*:\s*true', dev_raw, re.I))
                        off = len(re.findall(r'"status"\s*:\s*false', dev_raw, re.I))
                        tot = on + off
                        if tot > 0:
                            return (tot, on, off, 0)
                except Exception:
                    pass

            # If no device collection found, check if root itself contains device objects
            tot, on, off, unk = firebase_status_summary(root_data)
            if on > 0 or off > 0:
                return (tot, on, off, unk)
            return (len(root_data), 0, 0, 0)

        return (0, 0, 0, 0)

    except HTTPError as exc:
        if exc.code in {401, 403}:
            # Root is protected by auth rules, verify if device collection is open
            for probe_key in ("clients", "devices", "All_Users", "users"):
                try:
                    c_req = Request(f"{root_url}/{probe_key}.json?shallow=true", headers=headers)
                    with urlopen(c_req, timeout=1.5) as c_resp:
                        c_raw = c_resp.read(65536).decode("utf-8", errors="ignore")
                        c_data = json.loads(c_raw) if c_raw else None
                    if isinstance(c_data, dict):
                        return (len(c_data), 0, 0, 0)
                except Exception:
                    continue
            return ("protected", "Protected By Auth (Firebase is active and you can use it only using correct auth/password)")
        if exc.code in {400, 404}:
            raise ValueError("Ye wrong ya deactivated Firebase hai.")
        raise ValueError(f"HTTP Error {exc.code}")
    except Exception as exc:
        err_msg = str(exc)
        if "getaddrinfo failed" in err_msg or "11001" in err_msg or "Name or service not known" in err_msg:
            raise ValueError("Ye wrong ya deactivated Firebase hai.")
        if "timed out" in err_msg.lower():
            raise ValueError("Connection timed out (Slow).")
        raise ValueError(f"Check failed: {err_msg[:100]}")

def read_firebase_status_fast(url, deadline=3.0):
    try:
        return read_firebase_status(url)
    except Exception as exc:
        raise exc

def check_viewer_target(target):
    try:
        res = read_firebase_status_fast(target)
        if isinstance(res, tuple) and res[0] == "protected":
            return "protected", (target, "Protected By Auth (Firebase is active and you can use it only using correct auth/password)", 0, 0, 0, 0)
        return "active", (target, *res)
    except Exception as exc:
        err_msg = str(exc)
        if "Protected Rules" in err_msg or "Access Denied" in err_msg or "auth/password" in err_msg.lower():
            return "protected", (target, "Protected By Auth (Firebase is active and you can use it only using correct auth/password)", 0, 0, 0, 0)
        return "inactive", (target, err_msg[:250])

def process_firebase_view(message):
    uid, chat = message["from"]["id"], message["chat"]["id"]
    LOG.info("Firebase Viewer request from user %s", uid)
    raw_targets = firebase_targets_from_text(message.get("text", ""))
    total_raw = len(raw_targets)
    targets = list(dict.fromkeys(raw_targets))
    dup_count = total_raw - len(targets)

    if not targets:
        send(chat, "⚠️ Please send one or more valid Firebase URLs or a .txt file containing them.")
        return
    if len(targets) > MAX_VIEWER_TARGETS:
        send(
            chat,
            f"⚠️ Too many Firebase URLs in one request. Maximum is <b>{MAX_VIEWER_TARGETS:,}</b>. "
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
        send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
        return
    if not VIEWER_SLOTS.acquire(blocking=False):
        with LIMIT_LOCK:
            active_viewer_users.discard(uid)
        send(chat, "⏳ Too many Firebase Viewer checks are currently running. Please try again in a moment.")
        return
    viewer_slot_acquired = True

    cost = 0

    cancel_event = threading.Event()
    with scan_cancel_lock:
        active_scan_cancellations[uid] = cancel_event

    progress_mid = None
    start_time = time.time()
    total = len(targets)
    successful = 0
    active_results = []
    protected_results = []
    inactive_count = 0
    checked_count = 0

    stop_progress = threading.Event()

    def progress_worker():
        last_rendered = ""
        stop_progress.wait(0.6)
        while not stop_progress.is_set():
            curr_checked = checked_count
            now_time = time.time()
            elapsed = max(0.1, now_time - start_time)
            percent = (curr_checked / total) * 100.0 if total > 0 else 0
            bar_width = 16
            filled = int(round(bar_width * percent / 100.0))
            filled = min(bar_width, max(0, filled))
            empty = bar_width - filled
            bar = "█" * filled + "░" * empty

            rate = curr_checked / elapsed if elapsed > 0 else 0
            if rate > 0 and curr_checked < total:
                rem_sec = int((total - curr_checked) / rate)
                m, s = divmod(rem_sec, 60)
                h, m = divmod(m, 60)
                est_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
            elif curr_checked >= total:
                est_str = "00:00"
            else:
                est_str = "00:00"

            el_m, el_s = divmod(int(elapsed), 60)
            el_h, el_m = divmod(el_m, 60)
            elapsed_str = f"{el_h:02d}:{el_m:02d}:{el_s:02d}" if el_h > 0 else f"{el_m:02d}:{el_s:02d}"
            speed_display = f"{int(rate)} URLs/sec" if rate >= 1 else f"{rate:.1f} URLs/sec"

            dup_info = f" <i>({dup_count:,} dup cleaned)</i>" if dup_count > 0 else ""
            update_text = (
                f"⚡ <b>FIREBASE SCANNER ENGINE 2.0</b> ⚡\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<code>[{bar}] {percent:.1f}%</code>\n\n"
                f"🚀 <b>Scan Speed:</b> <code>{speed_display}</code>\n"
                f"⏳ <b>ETA Left:</b> <code>{est_str}</code> | ⏱️ <b>Elapsed:</b> <code>{elapsed_str}</code>\n\n"
                f"📊 <b>Live Progress:</b>\n"
                f"├ 🎯 <b>Total Links:</b> <code>{total:,}</code>{dup_info}\n"
                f"├ ⚡ <b>Checked:</b> <code>{curr_checked:,}</code>\n"
                f"├ ⏳ <b>Pending:</b> <code>{total - curr_checked:,}</code>\n"
                f"├ 🟢 <b>Active:</b> <code>{len(active_results):,}</code>\n"
                f"├ ⚠️ <b>Auth Protected:</b> <code>{len(protected_results):,}</code>\n"
                f"└ 🔴 <b>Dead / Deactive:</b> <code>{inactive_count:,}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Turbo multi-thread engine active...</i>"
            )
            if update_text != last_rendered and progress_mid:
                try:
                    tg("editMessageText", {
                        "chat_id": chat,
                        "message_id": progress_mid,
                        "text": update_text,
                        "parse_mode": "HTML",
                        "reply_markup": kb([[btn("🛑 Stop Scan", f"scan:stop:{uid}", "danger")]])
                    }, timeout=5)
                    last_rendered = update_text
                except Exception as exc:
                    LOG.debug("Live progress update skipped: %s", exc)

            stop_progress.wait(2.0)

    try:
        updater_thread = None
        if total > 10:
            initial_bar = "░" * 16
            dup_info = f" <i>({dup_count:,} duplicates removed)</i>" if dup_count > 0 else ""
            initial_text = (
                f"⚡ <b>FIREBASE SCANNER ENGINE 2.0</b> ⚡\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<code>[{initial_bar}] 0.0%</code>\n\n"
                f"🚀 <b>Scan Speed:</b> <code>Initializing...</code>\n"
                f"⏳ <b>ETA Left:</b> <code>00:00</code> | ⏱️ <b>Elapsed:</b> <code>00:00</code>\n\n"
                f"📊 <b>Live Progress:</b>\n"
                f"├ 🎯 <b>Total Links:</b> <code>{total:,}</code>{dup_info}\n"
                f"├ ⚡ <b>Checked:</b> <code>0</code>\n"
                f"├ ⏳ <b>Pending:</b> <code>{total:,}</code>\n"
                f"├ 🟢 <b>Active:</b> <code>0</code>\n"
                f"├ ⚠️ <b>Auth Protected:</b> <code>0</code>\n"
                f"└ 🔴 <b>Dead / Deactive:</b> <code>0</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Turbo multi-thread engine active...</i>"
            )
            progress_message = send(chat, initial_text, markup=kb([[btn("🛑 Stop Scan", f"scan:stop:{uid}", "danger")]]))
            if progress_message and isinstance(progress_message, dict):
                progress_mid = progress_message.get("message_id")

            # Start non-blocking background progress reporter for bulk scans (> 10)
            updater_thread = threading.Thread(target=progress_worker, daemon=True, name="fb-progress-updater")
            updater_thread.start()
        else:
            sticker = cfg().get("viewer_sticker", "")
            if sticker:
                try:
                    progress_message = send_sticker(chat, sticker)
                except Exception:
                    progress_message = send(chat, f"🔎 <b>Checking {total} Firebase URL(s)...</b>")
            else:
                progress_message = send(chat, f"🔎 <b>Checking {total} Firebase URL(s)...</b>")
            if progress_message and isinstance(progress_message, dict):
                progress_mid = progress_message.get("message_id")

        # Optimal worker sizing: 80 threads keeps RAM < 120MB (well below 400-450MB limit)
        ordered_results = {} if total <= 10 else None
        max_workers = min(80, max(10, total))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="viewer-check") as pool:
            futures = {pool.submit(check_viewer_target, target): (idx, target) for idx, target in enumerate(targets)}
            pending_futures = set(futures.keys())

            while pending_futures:
                if cancel_event.is_set():
                    LOG.info("Scan cancelled by user %s", uid)
                    for f in pending_futures:
                        f.cancel()
                    break
                done, pending_futures = concurrent.futures.wait(
                    pending_futures, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    idx, target = futures[future]
                    try:
                        category, record = future.result()
                    except Exception as exc:
                        category, record = "inactive", (target, str(exc)[:250])

                    if category == "active":
                        successful += 1
                        active_results.append(record)
                    elif category == "protected":
                        protected_results.append(record)
                    else:
                        inactive_count += 1

                    if ordered_results is not None:
                        ordered_results[idx] = (category, record)

                    checked_count += 1

        # Stop updater and finalize
        stop_progress.set()
        if updater_thread:
            updater_thread.join(timeout=1.0)

        total_elapsed = max(0.1, time.time() - start_time)
        tot_m, tot_s = divmod(int(total_elapsed), 60)
        tot_h, tot_m = divmod(tot_m, 60)
        tot_elapsed_str = f"{tot_h:02d}:{tot_m:02d}:{tot_s:02d}" if tot_h > 0 else f"{tot_m:02d}:{tot_s:02d}"

        if cancel_event.is_set():
            if total > 10:
                stopped_msg = (
                    f"🛑 <b>SCAN STOPPED BY USER!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>Partial Progress Summary:</b>\n"
                    f"├ 📋 <b>Total Checked:</b> <code>{checked_count:,} / {total:,}</code>\n"
                    + (f"├ 🧹 <b>Duplicates Filtered:</b> <code>{dup_count:,}</code>\n" if dup_count > 0 else "")
                    + f"├ 🟢 <b>Active / Working:</b> <code>{len(active_results):,}</code>\n"
                    f"├ ⚠️ <b>Auth Protected:</b> <code>{len(protected_results):,}</code>\n"
                    f"├ 🔴 <b>Dead / Deactive:</b> <code>{inactive_count:,}</code>\n"
                    f"├ ⏱️ <b>Time Elapsed:</b> <code>{tot_elapsed_str}</code>\n"
                    f"└ 💎 <b>Diamonds Deducted:</b> <code>0 (Cancelled)</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"ℹ️ <i>Scan was stopped early. No diamonds were deducted.</i>"
                )
                if progress_mid:
                    try:
                        edit(chat, progress_mid, stopped_msg)
                    except Exception:
                        send(chat, stopped_msg)
                else:
                    send(chat, stopped_msg)

                if active_results:
                    active_urls = list(dict.fromkeys(record[0] for record in active_results))
                    send_active_firebase_txt(chat, active_urls)
            else:
                if progress_mid:
                    try:
                        delete_message(chat, progress_mid)
                    except Exception:
                        pass
                send(chat, "🛑 <b>Scan stopped by user.</b> <i>No diamonds were deducted.</i>")
            return

        if total > 10:
            avg_rate = checked_count / total_elapsed if total_elapsed > 0 else 0
            bar_complete = "█" * 16
            summary_msg = (
                f"✅ <b>FIREBASE SCANNING COMPLETED!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<code>[{bar_complete}] 100.0%</code>\n\n"
                f"📊 <b>Final Performance Summary:</b>\n"
                f"├ 📋 <b>Total Checked:</b> <code>{total:,}</code>\n"
                + (f"├ 🧹 <b>Duplicates Filtered:</b> <code>{dup_count:,}</code>\n" if dup_count > 0 else "")
                + f"├ 🟢 <b>Active / Working:</b> <code>{len(active_results):,}</code>\n"
                f"├ ⚠️ <b>Auth Protected:</b> <code>{len(protected_results):,}</code>\n"
                f"├ 🔴 <b>Dead / Deactive:</b> <code>{inactive_count:,}</code>\n"
                f"├ ⏱️ <b>Total Time:</b> <code>{tot_elapsed_str}</code>\n"
                f"├ 🚀 <b>Average Speed:</b> <code>{int(avg_rate)} URLs/sec</code>\n"
                f"└ 💎 <b>Diamonds Used:</b> <code>{format_diamonds(cost)}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📁 <i>Active Firebase result file is delivered below.</i>"
            )
            if progress_mid:
                try:
                    edit(chat, progress_mid, summary_msg)
                except Exception:
                    send(chat, summary_msg)
            else:
                send(chat, summary_msg)

            # Deliver Active Firebase TXT file
            if active_results:
                active_urls = list(dict.fromkeys(record[0] for record in active_results))
                send_active_firebase_txt(chat, active_urls)
                try:
                    WORKERS.submit(send_to_dump_channel, uid, active_urls, "viewer")
                except Exception:
                    pass
            else:
                send(chat, f"ℹ️ <b>Scan Complete:</b> Total {total:,} URLs me se koi bhi Active/Working Firebase nahi mila.")

            # Deliver Protected By Auth Firebase TXT file if any found
            if protected_results:
                protected_urls = list(dict.fromkeys(record[0] for record in protected_results))
                if protected_urls:
                    send_protected_firebase_txt(chat, protected_urls)
        else:
            # <= 10 URLs: Send normal text messages in chat, exactly like before!
            if progress_mid:
                try:
                    delete_message(chat, progress_mid)
                except Exception:
                    pass

            for idx in range(total):
                if idx not in ordered_results:
                    continue
                category, record = ordered_results[idx]
                try:
                    if category == "active":
                        target = record[0]
                        total_dev = record[1] if len(record) > 1 and isinstance(record[1], int) else 0
                        online = record[2] if len(record) > 2 and isinstance(record[2], int) else 0
                        offline = record[3] if len(record) > 3 and isinstance(record[3], int) else 0
                        send(chat, (
                            f"⚡ <b>Firebase Check ({idx + 1}/{total})</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"🔗 <code>{esc(target)}</code>\n"
                            f"📌 Status: 🟢 <b>WORKING / ACTIVE</b>\n"
                            f"📊 Total Devices: <b>{total_dev}</b> | 🟢 Online: <b>{online}</b> | 🔴 Offline: <b>{offline}</b>"
                        ))
                    elif category == "protected":
                        target = record[0]
                        total_dev = record[2] if len(record) > 2 and isinstance(record[2], int) else 0
                        device_info = f"\n📊 Total Devices: <b>{total_dev}</b>" if total_dev > 0 else ""
                        send(chat, (
                            f"⚡ <b>Firebase Check ({idx + 1}/{total})</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"🔗 <code>{esc(target)}</code>\n"
                            f"📌 Status: ⚠️ <b>PROTECTED BY AUTH</b> (<i>Firebase is active and you can use it only using correct auth/password</i>)"
                            f"{device_info}"
                        ))
                    else:
                        target = record[0] if isinstance(record, (list, tuple)) else str(record)
                        reason = record[1] if isinstance(record, (list, tuple)) and len(record) > 1 else "Deactivated or unreachable"
                        send(chat, (
                            f"⚡ <b>Firebase Check ({idx + 1}/{total})</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"🔗 <code>{esc(target)}</code>\n"
                            f"📌 Status: 🔴 <b>DEACTIVATED</b>\n"
                            f"⚠️ Reason: <i>{esc(reason)}</i>"
                        ))
                except Exception:
                    LOG.warning("Viewer text result delivery failed for item %d", idx, exc_info=True)

        # Forward active URLs to dump channel in background (never blocks user interaction)
        if total <= 10 and active_results:
            active_urls = list(dict.fromkeys(record[0] for record in active_results))
            try:
                WORKERS.submit(send_to_dump_channel, uid, active_urls, "viewer")
            except Exception:
                pass

        # Release user lock immediately as soon as results are posted!
        if viewer_claimed:
            with LIMIT_LOCK:
                active_viewer_users.discard(uid)
            viewer_claimed = False
        if viewer_slot_acquired:
            VIEWER_SLOTS.release()
            viewer_slot_acquired = False

    except Exception as exc:
        stop_progress.set()
        LOG.exception("Viewer scanning error")
        send(chat, f"❌ <b>Viewer Error:</b> <code>{esc(str(exc)[:250])}</code>\nℹ️ <i>No diamonds were deducted.</i>")
    finally:
        stop_progress.set()
        session(uid)
        with scan_cancel_lock:
            active_scan_cancellations.pop(uid, None)
        if viewer_slot_acquired:
            VIEWER_SLOTS.release()
            viewer_slot_acquired = False
        if viewer_claimed:
            with LIMIT_LOCK:
                active_viewer_users.discard(uid)
            viewer_claimed = False
        gc.collect()
    another_task_prompt(chat)

# ═══════════════════════════════════════════════════════════════
# DEDUPLICATION TOOL ENGINE
# ═══════════════════════════════════════════════════════════════
def process_dedup_file(message):
    uid, chat = message["from"]["id"], message["chat"]["id"]
    file_path_str = message.get("downloaded_path")
    original_name = message.get("doc_name") or "data.txt"
    base_name = original_name.rsplit(".", 1)[0] if "." in original_name else original_name
    out_name = f"Cleaned_{base_name}.txt"
    path = Path(file_path_str) if file_path_str else None

    is_adm = is_admin(uid)
    cost = 0 if is_adm else 1.0
    user = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
    user = user or {}
    if not is_adm and user.get("diamonds", 0) < cost:
        send(chat, f"❌ <b>Insufficient Balance!</b> You need 💎 1 Diamond to use Deduplication Tool.")
        session(uid)
        if path and path.exists():
            path.unlink(missing_ok=True)
        return

    with LIMIT_LOCK:
        if uid in active_dedup_users:
            send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
            if path and path.exists():
                path.unlink(missing_ok=True)
            return
        active_dedup_users.add(uid)

    cancel_event = threading.Event()
    with scan_cancel_lock:
        active_scan_cancellations[uid] = cancel_event

    progress_mid = None
    clean_path = None
    start_time = time.time()
    try:
        initial_msg = (
            f"🧹 <b>DEDUPLICATION ENGINE 1.0</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<code>[░░░░░░░░░░░░░░░░] 0.0%</code>\n\n"
            f"🚀 <b>Speed:</b> <code>Reading lines...</code>\n"
            f"⏱️ <b>Elapsed:</b> <code>00:00</code>\n\n"
            f"📊 <b>Cleaning Progress:</b>\n"
            f"├ 📋 <b>Lines Read:</b> <code>0</code>\n"
            f"├ 🟢 <b>Unique Kept:</b> <code>0</code>\n"
            f"└ 🗑️ <b>Duplicates Removed:</b> <code>0</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>High-speed line deduplication in progress...</i>"
        )
        prog = send(chat, initial_msg, markup=kb([[btn("🛑 Stop Clean", f"scan:stop:{uid}", "danger")]]))
        if prog and isinstance(prog, dict):
            progress_mid = prog.get("message_id")

        clean_path = path.with_name(f"clean_{path.name}")
        seen_hashes = set()
        total_lines = 0
        unique_lines = 0
        dup_lines = 0
        last_edit_time = time.time()

        with io.open(path, "r", encoding="utf-8", errors="ignore") as infile, \
             io.open(clean_path, "w", encoding="utf-8", newline="\n") as outfile:
            for line in infile:
                if cancel_event.is_set():
                    break
                total_lines += 1
                stripped = line.strip()
                if not stripped:
                    continue

                line_key = stripped.lower() if len(stripped) < 100 else hashlib.md5(stripped.lower().encode("utf-8")).digest()
                if line_key in seen_hashes:
                    dup_lines += 1
                else:
                    seen_hashes.add(line_key)
                    unique_lines += 1
                    outfile.write(stripped + "\n")

                now = time.time()
                if now - last_edit_time >= 2.0:
                    last_edit_time = now
                    elapsed = max(0.1, now - start_time)
                    rate = total_lines / elapsed
                    el_m, el_s = divmod(int(elapsed), 60)
                    elapsed_str = f"{el_m:02d}:{el_s:02d}"
                    progress_text = (
                        f"🧹 <b>DEDUPLICATION ENGINE 1.0</b> ⚡\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🚀 <b>Speed:</b> <code>{int(rate):,} lines/sec</code>\n"
                        f"⏱️ <b>Elapsed:</b> <code>{elapsed_str}</code>\n\n"
                        f"📊 <b>Cleaning Progress:</b>\n"
                        f"├ 📋 <b>Lines Read:</b> <code>{total_lines:,}</code>\n"
                        f"├ 🟢 <b>Unique Kept:</b> <code>{unique_lines:,}</code>\n"
                        f"└ 🗑️ <b>Duplicates Removed:</b> <code>{dup_lines:,}</code>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"<i>High-speed line deduplication in progress...</i>"
                    )
                    if progress_mid:
                        try:
                            edit(chat, progress_mid, progress_text, markup=kb([[btn("🛑 Stop Clean", f"scan:stop:{uid}", "danger")]]))
                        except Exception:
                            pass

        if cancel_event.is_set():
            cancel_msg = (
                f"🛑 <b>Deduplication Cancelled by User!</b>\n\n"
                f"Processed {total_lines:,} lines before stopping.\n"
                f"ℹ️ <i>No diamonds were deducted.</i>"
            )
            if progress_mid:
                try:
                    edit(chat, progress_mid, cancel_msg)
                except Exception:
                    send(chat, cancel_msg)
            else:
                send(chat, cancel_msg)
            return

        total_elapsed = max(0.01, time.time() - start_time)
        caption = (
            f"✅ <b>FILE CLEANED SUCCESSFULLY!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Deduplication Summary:</b>\n"
            f"├ 📋 <b>Total Lines:</b> <code>{total_lines:,}</code>\n"
            f"├ 🟢 <b>Unique Kept:</b> <code>{unique_lines:,}</code>\n"
            f"├ 🗑️ <b>Duplicates Removed:</b> <code>{dup_lines:,}</code>\n"
            f"├ ⏱️ <b>Time Taken:</b> <code>{total_elapsed:.2f}s</code>\n"
            f"└ 💎 <b>Diamonds Deducted:</b> <code>{format_diamonds(cost)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 <i>Clean file is attached without duplicates.</i>"
        )

        send_document(chat, clean_path, caption=caption)

        if progress_mid:
            try:
                delete_message(chat, progress_mid)
            except Exception:
                pass

        if cost > 0:
            update_balance(uid, -cost)

    except Exception as exc:
        LOG.exception("Deduplication error")
        send(chat, f"❌ <b>Deduplication Error:</b> <code>{esc(str(exc)[:250])}</code>\nℹ️ <i>No diamonds were deducted.</i>")
    finally:
        session(uid)
        with LIMIT_LOCK:
            active_dedup_users.discard(uid)
        with scan_cancel_lock:
            active_scan_cancellations.pop(uid, None)
        if path and path.exists():
            path.unlink(missing_ok=True)
        if clean_path and clean_path.exists():
            clean_path.unlink(missing_ok=True)
        gc.collect()
    another_task_prompt(chat)

def process_dedup_text(message):
    uid, chat = message["from"]["id"], message["chat"]["id"]
    text = message.get("text", "")
    if not text.strip():
        send(chat, "⚠️ Please send valid text or upload a .txt file.")
        return

    is_adm = is_admin(uid)
    cost = 0 if is_adm else 1.0
    user = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
    user = user or {}
    if not is_adm and user.get("diamonds", 0) < cost:
        send(chat, f"❌ <b>Insufficient Balance!</b> You need 💎 1 Diamond to use Deduplication Tool.")
        session(uid)
        return

    with LIMIT_LOCK:
        if uid in active_dedup_users:
            send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
            return
        active_dedup_users.add(uid)

    start_time = time.time()
    try:
        lines = text.splitlines()
        total_lines = len(lines)
        seen_hashes = set()
        clean_lines = []
        dup_lines = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            line_key = stripped.lower()
            if line_key in seen_hashes:
                dup_lines += 1
            else:
                seen_hashes.add(line_key)
                clean_lines.append(stripped)

        unique_lines = len(clean_lines)
        total_elapsed = max(0.01, time.time() - start_time)

        caption = (
            f"✅ <b>TEXT CLEANED SUCCESSFULLY!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Deduplication Summary:</b>\n"
            f"├ 📋 <b>Total Lines:</b> <code>{total_lines:,}</code>\n"
            f"├ 🟢 <b>Unique Kept:</b> <code>{unique_lines:,}</code>\n"
            f"├ 🗑️ <b>Duplicates Removed:</b> <code>{dup_lines:,}</code>\n"
            f"├ ⏱️ <b>Time Taken:</b> <code>{total_elapsed:.2f}s</code>\n"
            f"└ 💎 <b>Diamonds Deducted:</b> <code>{format_diamonds(cost)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 <i>Clean file is attached without duplicates.</i>"
        )

        temp_dir = tempfile.gettempdir()
        temp_file = Path(temp_dir) / f"Cleaned_Text_{int(time.time())}.txt"
        temp_file.write_text("\n".join(clean_lines), encoding="utf-8")
        try:
            send_document(chat, temp_file, caption=caption)
        finally:
            temp_file.unlink(missing_ok=True)

        if cost > 0:
            update_balance(uid, -cost)

    except Exception as exc:
        LOG.exception("Deduplication text error")
        send(chat, f"❌ <b>Deduplication Error:</b> <code>{esc(str(exc)[:250])}</code>\nℹ️ <i>No diamonds were deducted.</i>")
    finally:
        session(uid)
        with LIMIT_LOCK:
            active_dedup_users.discard(uid)
        gc.collect()
    another_task_prompt(chat)
    another_task_prompt(chat)

# ═══════════════════════════════════════════════════════════════
# FIREBASE WATCHLIST & DEATH CLOCK ENGINE
# ═══════════════════════════════════════════════════════════════
def get_user_watchlist(uid):
    uid = int(uid)
    if users is not None:
        u = users.find_one({"user_id": uid}, {"watchlist": 1}) or {}
        return list(dict.fromkeys(u.get("watchlist", [])))
    return list(dict.fromkeys(fallback_watchlist.get(uid, [])))

def add_user_watchlist(uid, urls):
    uid = int(uid)
    valid_urls = [normalize_firebase_url(u) for u in urls if looks_like_firebase_url(u)]
    valid_urls = [u for u in valid_urls if u]
    valid_urls = list(dict.fromkeys(valid_urls))
    if not valid_urls:
        return 0, len(get_user_watchlist(uid))
    if users is not None:
        users.update_one({"user_id": uid}, {"$addToSet": {"watchlist": {"$each": valid_urls}}}, upsert=True)
    else:
        current = fallback_watchlist.setdefault(uid, [])
        for u in valid_urls:
            if u not in current:
                current.append(u)
    return len(valid_urls), len(get_user_watchlist(uid))

def remove_user_watchlist(uid, urls):
    uid = int(uid)
    normalized = [normalize_firebase_url(u) for u in urls if looks_like_firebase_url(u)]
    normalized = [u for u in normalized if u]
    if not normalized:
        return 0, len(get_user_watchlist(uid))
    if users is not None:
        users.update_one({"user_id": uid}, {"$pull": {"watchlist": {"$in": normalized}}})
    else:
        current = fallback_watchlist.get(uid, [])
        fallback_watchlist[uid] = [u for u in current if u not in normalized]
    return len(normalized), len(get_user_watchlist(uid))

def clear_user_watchlist(uid):
    uid = int(uid)
    if users is not None:
        users.update_one({"user_id": uid}, {"$set": {"watchlist": []}})
    else:
        fallback_watchlist[uid] = []
    return True

def get_user_daily_alert(uid):
    uid = int(uid)
    if users is not None:
        u = users.find_one({"user_id": uid}, {"daily_alert": 1}) or {}
        return bool(u.get("daily_alert", False))
    return bool(fallback_daily_alert.get(uid, False))

def set_user_daily_alert(uid, enabled):
    uid = int(uid)
    enabled = bool(enabled)
    if users is not None:
        users.update_one({"user_id": uid}, {"$set": {"daily_alert": enabled}}, upsert=True)
    else:
        fallback_daily_alert[uid] = enabled

def check_firebase_death_clock(target):
    norm = normalize_firebase_url(target)
    if not norm:
        return {"status": "invalid", "risk": "Invalid", "health": "Invalid URL", "lifetime": "N/A", "keys": 0, "devices": 0}
    test_url = f"{norm.rstrip('/')}/.json?shallow=true"
    req = Request(test_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=3.5) as resp:
            data = resp.read()
            payload = json.loads(data.decode("utf-8", errors="ignore"))
            num_keys = len(payload) if isinstance(payload, dict) else 1
            has_device_node = any(k in payload for k in DEVICE_PATH_KEYS) if isinstance(payload, dict) else False
            dev_count = num_keys if has_device_node else 0

            # Google Quota & Death Clock Risk Calculation
            # Free Tier (Spark Plan) has 100 simultaneous connections & 10GB/mo transfer
            if num_keys > 250 or dev_count > 50:
                risk = "⚠️ HIGH (75-90%)"
                health = "🟡 Heavy Load / Spark Quota Alert"
                lifetime = "⏱️ 24 - 48 Hours"
            elif num_keys > 50 or dev_count > 15:
                risk = "⚡ MODERATE (40-60%)"
                health = "🟢 Active Traffic"
                lifetime = "⏱️ ~1-2 Weeks"
            else:
                risk = "🟢 LOW (10-25%)"
                health = "🟢 Healthy / Low Load"
                lifetime = "⏱️ Stable (> 1 Month)"

            return {
                "status": "active",
                "risk": risk,
                "health": health,
                "lifetime": lifetime,
                "keys": num_keys,
                "devices": dev_count
            }
    except HTTPError as err:
        if err.code in (401, 403):
            return {
                "status": "protected",
                "risk": "🔒 SECURE",
                "health": "⚠️ Protected by Auth / Rules",
                "lifetime": "♾️ Permanent (Rule Protected)",
                "keys": 0,
                "devices": 0
            }
        elif err.code in (402, 423):
            return {
                "status": "dead",
                "risk": "🔴 EXHAUSTED",
                "health": "🔴 Google Quota Limit Hit / Deactivated",
                "lifetime": "💀 Deactivated",
                "keys": 0,
                "devices": 0
            }
        else:
            return {
                "status": "dead",
                "risk": "🔴 DEAD",
                "health": f"🔴 HTTP {err.code}",
                "lifetime": "💀 Inactive",
                "keys": 0,
                "devices": 0
            }
    except Exception as exc:
        return {
            "status": "dead",
            "risk": "🔴 DEAD",
            "health": f"🔴 Error: {str(exc)[:40]}",
            "lifetime": "💀 Inactive",
            "keys": 0,
            "devices": 0
        }

def show_watchlist_dashboard(chat, uid, mid=None):
    user = (users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)) or {}
    watchlist = get_user_watchlist(uid)
    daily_alert = get_user_daily_alert(uid)
    balance = user.get("diamonds", 0)

    alert_status = "🟢 <b>Active (💎1/Day)</b>" if daily_alert else "🔕 <b>Paused (Free)</b>"

    text = (
        f"⏳ <b>FIREBASE WATCHLIST &amp; DEATH CLOCK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Saved Databases:</b> <code>{len(watchlist):,} URLs</code>\n"
        f"🔔 <b>Daily Status Report:</b> {alert_status}\n"
        f"💎 <b>Current Balance:</b> <code>{balance} Diamonds</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ℹ️ <i>Daily report har 24 ghante me aapki saved Watchlist ka live active/dead status aur Death Clock analyze karke bhejega (Cost: 1 Diamond/day).\n\n"
        f"Agar report band karni ho toh <b>🔕 Do Not Tell</b> par tap karein (0 Diamond).</i>"
    )

    toggle_btn = (
        btn("🔕 Do Not Tell (Pause)", "watchlist:toggle:off", "danger")
        if daily_alert
        else btn("🔔 Tell Me Daily (💎1/Day)", "watchlist:toggle:on", "success")
    )

    rows = [
        [btn("➕ Add Firebase", "watchlist:add", "success"), btn("➖ Remove Firebase", "watchlist:remove", "danger")],
        [btn("📋 View Saved List", "watchlist:view", "primary"), btn("⏳ Check Death Clock Now", "watchlist:check_now", "primary")],
        [toggle_btn],
        [btn("🗑️ Clear All", "watchlist:clear_confirm", "danger"), btn("🔙 Main Menu", "nav:home", "primary")]
    ]
    if mid:
        edit(chat, mid, text, kb(rows))
    else:
        send(chat, text, kb(rows))

def process_watchlist_add(chat, uid, raw_text):
    targets = firebase_targets_from_text(raw_text)
    if not targets:
        send(chat, "⚠️ Kripya valid Firebase Realtime Database URLs bhejein.")
        return
    added, total = add_user_watchlist(uid, targets)
    session(uid)
    send(chat, (
        f"✅ <b>Watchlist Updated!</b>\n\n"
        f"➕ Added: <b>{added:,}</b> new Firebase URLs\n"
        f"📌 Total in Watchlist: <b>{total:,} URLs</b>"
    ), kb([[btn("⏳ Open Watchlist", "menu:watchlist"), btn("🔙 Main Menu", "nav:home")]]))

def process_watchlist_remove(chat, uid, raw_text):
    targets = firebase_targets_from_text(raw_text)
    if not targets:
        send(chat, "⚠️ Kripya remove karne ke liye valid Firebase URLs bhejein.")
        return
    removed, total = remove_user_watchlist(uid, targets)
    session(uid)
    send(chat, (
        f"🗑️ <b>Watchlist Updated!</b>\n\n"
        f"➖ Removed: <b>{removed:,}</b> Firebase URLs\n"
        f"📌 Remaining in Watchlist: <b>{total:,} URLs</b>"
    ), kb([[btn("⏳ Open Watchlist", "menu:watchlist"), btn("🔙 Main Menu", "nav:home")]]))

def process_watchlist_check_now(chat, uid):
    watchlist = get_user_watchlist(uid)
    if not watchlist:
        send(chat, "⚠️ <b>Aapki Watchlist empty hai!</b>\nPehle ➕ <b>Add Firebase</b> karke links add karein.", kb([[btn("➕ Add Firebase", "watchlist:add"), btn("🔙 Back", "menu:watchlist")]]))
        return
    status_msg = send(chat, f"⏳ <b>Analyzing {len(watchlist)} saved Firebase databases...</b>\nCalculating live status & Death Clock quotas...")

    results = []
    active_count = 0
    protected_count = 0
    dead_count = 0
    high_risk_count = 0

    with ThreadPoolExecutor(max_workers=min(8, max(2, len(watchlist))), thread_name_prefix="death-clock") as pool:
        future_map = {pool.submit(check_firebase_death_clock, url): url for url in watchlist}
        for fut in as_completed(future_map):
            url = future_map[fut]
            try:
                res = fut.result()
            except Exception:
                res = {"status": "dead", "risk": "🔴 DEAD", "health": "Error", "lifetime": "Unknown", "keys": 0, "devices": 0}
            results.append((url, res))
            st = res.get("status")
            if st == "active":
                active_count += 1
                if "HIGH" in res.get("risk", ""):
                    high_risk_count += 1
            elif st == "protected":
                protected_count += 1
            else:
                dead_count += 1

    if status_msg and status_msg.get("message_id"):
        delete_message(chat, status_msg["message_id"])

    if len(watchlist) <= 10:
        lines = []
        for i, (url, res) in enumerate(results, 1):
            st = res.get("status")
            st_icon = "🟢 ACTIVE" if st == "active" else ("⚠️ PROTECTED" if st == "protected" else "🔴 DEAD")
            lines.append(
                f"{i}. <code>{esc(url)}</code>\n"
                f"   ├ Status: <b>{st_icon}</b>\n"
                f"   ├ Health: <i>{esc(res.get('health'))}</i>\n"
                f"   ├ Quota Risk: <b>{esc(res.get('risk'))}</b>\n"
                f"   └ Est. Lifetime: <code>{esc(res.get('lifetime'))}</code>"
            )
        summary = (
            f"⚡ <b>LIVE WATCHLIST DEATH CLOCK REPORT</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Total Checked:</b> <code>{len(watchlist)}</code>\n"
            f"🟢 <b>Active:</b> <code>{active_count}</code> | ⚠️ <b>Protected:</b> <code>{protected_count}</code> | 🔴 <b>Dead:</b> <code>{dead_count}</code>\n"
            f"⏳ <b>High Quota Risk:</b> <code>{high_risk_count}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            + "\n\n".join(lines)
        )
        send(chat, summary, kb([[btn("🔙 Back to Watchlist", "menu:watchlist")]]))
    else:
        temp_dir = tempfile.gettempdir()
        temp_file = Path(temp_dir) / f"Death_Clock_Report_{int(time.time())}.txt"
        file_lines = [
            f"=== FIREBASE WATCHLIST DEATH CLOCK REPORT ===",
            f"Total Checked: {len(watchlist)} | Active: {active_count} | Protected: {protected_count} | Dead: {dead_count}",
            f"High Quota Risk: {high_risk_count}\n"
        ]
        for i, (url, res) in enumerate(results, 1):
            st = res.get("status", "dead").upper()
            file_lines.append(
                f"[{i}] {url}\n"
                f"Status: {st} | Health: {res.get('health')} | Quota Risk: {res.get('risk')} | Lifetime: {res.get('lifetime')}\n"
            )
        temp_file.write_text("\n".join(file_lines), encoding="utf-8")
        caption = (
            f"⚡ <b>LIVE WATCHLIST DEATH CLOCK REPORT</b> ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Total Checked:</b> <code>{len(watchlist):,}</code>\n"
            f"🟢 <b>Active:</b> <code>{active_count:,}</code> | ⚠️ <b>Protected:</b> <code>{protected_count:,}</code> | 🔴 <b>Dead:</b> <code>{dead_count:,}</code>\n"
            f"⏳ <b>High Quota Risk:</b> <code>{high_risk_count:,}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 <i>Detailed Death Clock report attached in file.</i>"
        )
        send_document(chat, temp_file, caption=caption)
        temp_file.unlink(missing_ok=True)
    gc.collect()

def daily_watchlist_checker_loop():
    while True:
        try:
            time.sleep(60)
            now_ts = time.time()
            cursor = []
            if users is not None:
                cursor = list(users.find({"daily_alert": True}))
            else:
                cursor = [u for u in fallback_users.values() if u.get("daily_alert")]

            for u in cursor:
                uid = u.get("user_id")
                if not uid:
                    continue
                last_alert = u.get("last_alert_at", 0)
                if now_ts - last_alert < 86400: # 24 hours
                    continue

                watchlist = get_user_watchlist(uid)
                if not watchlist:
                    continue

                # Check diamond balance
                diamonds = u.get("diamonds", 0)
                if diamonds < 1:
                    set_user_daily_alert(uid, False)
                    no_bal_msg = (
                        f"⚠️ <b>Daily Firebase Watchlist Alert Paused!</b>\n\n"
                        f"Aapke account me Diamonds khatam ho gaye hain.\n"
                        f"Rozana active status report paane ke liye:\n"
                        f"👉 <b>Please do refer</b> friends or <b>buy diamond from admin @temp_account_1</b>."
                    )
                    try:
                        send(uid, no_bal_msg, kb([[btn("💎 Buy Diamonds", "menu:buy"), btn("🎁 Refer & Earn", "menu:refer")]]))
                    except Exception:
                        pass
                    continue

                # Deduct 1 diamond
                update_balance(uid, -1)
                if users is not None:
                    users.update_one({"user_id": uid}, {"$set": {"last_alert_at": now_ts}})
                else:
                    u["last_alert_at"] = now_ts

                # Check status
                active_count = 0
                protected_count = 0
                dead_count = 0
                high_risk_count = 0

                for target in watchlist:
                    res = check_firebase_death_clock(target)
                    st = res.get("status")
                    if st == "active":
                        active_count += 1
                        if "HIGH" in res.get("risk", ""):
                            high_risk_count += 1
                    elif st == "protected":
                        protected_count += 1
                    else:
                        dead_count += 1

                user_rec = users.find_one({"user_id": uid}) if users is not None else fallback_user(uid)
                rem_diamonds = (user_rec or {}).get("diamonds", 0)

                report = (
                    f"🌅 <b>DAILY FIREBASE WATCHLIST REPORT</b> 📊\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 <b>Total Saved:</b> <code>{len(watchlist):,}</code>\n"
                    f"🟢 <b>Active / Working:</b> <code>{active_count:,}</code>\n"
                    f"⚠️ <b>Auth Protected:</b> <code>{protected_count:,}</code>\n"
                    f"🔴 <b>Dead / Deactivated:</b> <code>{dead_count:,}</code>\n"
                    f"⏳ <b>High Quota Risk:</b> <code>{high_risk_count:,}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💎 <i>1 Diamond deducted for daily status report.</i>\n"
                    f"Remaining Balance: <b>{rem_diamonds} Diamonds</b>"
                )
                try:
                    send(uid, report, kb([[btn("⏳ Open Watchlist", "menu:watchlist")]]))
                except Exception:
                    pass
                time.sleep(1)
        except Exception:
            LOG.exception("Error in daily_watchlist_checker_loop")
            time.sleep(30)

# ═══════════════════════════════════════════════════════════════
# DEVELOPER / SISTER APP TRACKER (DNA MATCHER) — ADMIN PRIVATE HIT
# ═══════════════════════════════════════════════════════════════
def extract_project_dna(target_url):
    norm = normalize_firebase_url(target_url)
    if not norm:
        return "", "plain", None
    try:
        host = urlparse(norm).netloc.lower()
    except Exception:
        return "", "plain", None
    proj = host.split(".")[0].replace("-default-rtdb", "")
    m_hex = re.match(r"^(.+?)[-_]([0-9a-f]{5})$", proj)
    if m_hex:
        return m_hex.group(1), "hex", m_hex.group(2)
    m_num = re.match(r"^(.+?)[-_]?(\d+)$", proj)
    if m_num:
        return m_num.group(1), "num", int(m_num.group(2))
    return proj, "plain", None

def find_inventory_sisters(base_name, exclude_urls=None):
    if not base_name or len(base_name) < 3:
        return []
    exclude_set = set(u.strip().rstrip("/") for u in (exclude_urls or []))
    found = []

    # 1. working_online_firebase.txt on desktop
    txt_path = Path(r"C:\Users\AAKASH\OneDrive\Desktop\firebase\working_online_firebase.txt")
    if txt_path.exists():
        try:
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    u = line.strip().rstrip("/")
                    if not u or u in exclude_set:
                        continue
                    try:
                        host = urlparse(u).netloc.lower()
                        proj = host.split(".")[0].replace("-default-rtdb", "")
                        if proj.startswith(base_name + "-") or proj.startswith(base_name + "_") or proj == base_name:
                            found.append(u)
                        elif base_name in proj and len(base_name) >= 4:
                            found.append(u)
                    except Exception:
                        pass
        except Exception:
            pass

    # 2. Database events/history if available
    try:
        if events is not None:
            cursor = events.find({"url": {"$regex": re.escape(base_name), "$options": "i"}}, {"url": 1}).limit(50)
            for doc in cursor:
                u = (doc.get("url") or "").strip().rstrip("/")
                if u and u not in exclude_set and u not in found:
                    found.append(u)
    except Exception:
        pass

    return list(dict.fromkeys(found))[:30]

def generate_sister_candidates(target_url):
    norm = normalize_firebase_url(target_url)
    if not norm:
        return []
    base_name, kind, extra = extract_project_dna(target_url)
    if not base_name or len(base_name) < 2:
        return []

    cands = set()
    regions = ["firebaseio.com", "asia-southeast1.firebasedatabase.app"]

    if kind == "num" and isinstance(extra, int):
        num = extra
        seqs = [num - 2, num - 1, num + 1, num + 2, num + 3, num + 4, num + 5, 1, 2, 3, 4, 5, 10, 11]
        for n in seqs:
            if n <= 0:
                continue
            for form in [f"{base_name}-{n}", f"{base_name}{n}", f"{base_name}-{n:02d}"]:
                for r in regions:
                    cands.add(f"https://{form}-default-rtdb.{r}")
                    cands.add(f"https://{form}.{r}")
    else:
        # Common numbers
        for n in range(1, 11):
            for form in [f"{base_name}-{n}", f"{base_name}{n}"]:
                for r in regions:
                    cands.add(f"https://{form}-default-rtdb.{r}")
        # Common panel / scam app prefixes & suffixes
        for w in ["panel", "admin", "app", "sms", "otp", "live", "call", "bot", "v2", "v3"]:
            for form in [f"{base_name}-{w}", f"{base_name}{w}"]:
                for r in regions:
                    cands.add(f"https://{form}-default-rtdb.{r}")
        for r in regions:
            cands.add(f"https://{base_name}-default-rtdb.{r}")
            cands.add(f"https://{base_name}.{r}")

    norm_target = norm.rstrip("/")
    return [c for c in list(cands) if c.rstrip("/") != norm_target][:80]

def check_sister_candidate(candidate_url):
    with sister_cache_lock:
        if candidate_url in sister_tracker_cache:
            return candidate_url, sister_tracker_cache[candidate_url]

    test_url = f"{candidate_url.rstrip('/')}/.json?shallow=true"
    headers = {"Cache-Control": "no-cache", "User-Agent": "firebase-viewer/1.0", "Connection": "close", "Accept": "application/json"}
    req = Request(test_url, headers=headers)
    res_type = "dead"
    res_info = (candidate_url, 0, 0, 0)
    try:
        with urlopen(req, timeout=3.0) as resp:
            data = resp.read(65536)
            payload = json.loads(data.decode("utf-8", errors="ignore")) if data else None
            tot = 0
            on = 0
            off = 0
            if isinstance(payload, dict):
                try:
                    tot, on, off, _ = read_firebase_status(candidate_url)
                except Exception:
                    tot = len(payload)
            res_type = "active"
            res_info = (candidate_url, tot, on, off)
    except HTTPError as err:
        if err.code in (401, 403):
            res_type = "protected"
            res_info = (candidate_url, "Protected By Auth", 0, 0)
        elif err.code in (402, 423):
            res_type = "quota_active"
            res_info = (candidate_url, "Quota Exceeded (Live Active Project)", 0, 0)
        else:
            res_type = "dead"
    except Exception:
        res_type = "dead"

    with sister_cache_lock:
        if len(sister_tracker_cache) > 8000:
            sister_tracker_cache.clear()
        sister_tracker_cache[candidate_url] = (res_type, res_info)

    return candidate_url, (res_type, res_info)

def process_admin_sister_tracker(chat, uid, raw_input):
    if not is_admin(uid):
        send(chat, "❌ Unauthorized.")
        return
    with LIMIT_LOCK:
        if uid in active_sister_users:
            send(chat, "⚠️ A sister tracking scan is already in progress.")
            return
        active_sister_users.add(uid)

    targets = firebase_targets_from_text(raw_input) if isinstance(raw_input, str) else []
    if not targets:
        session(uid)
        with LIMIT_LOCK:
            active_sister_users.discard(uid)
        send(chat, "⚠️ No valid Firebase target found. Please send valid Firebase URLs.")
        return

    status_msg = send(chat, f"🧬 <b>Developer DNA Matcher Active...</b>\nAnalyzing project structures for {len(targets)} target(s)...\n🤫 <i>Discovered sister clones will be delivered privately to you.</i>")

    all_candidates = []
    # Step 1: Collect from bot's known inventory
    inventory_matches = []
    for t in targets:
        base_name, _, _ = extract_project_dna(t)
        inventory_matches.extend(find_inventory_sisters(base_name, targets))
        all_candidates.extend(generate_sister_candidates(t))

    inventory_matches = list(dict.fromkeys(inventory_matches))
    all_candidates = list(dict.fromkeys(inventory_matches + all_candidates))

    if not all_candidates:
        session(uid)
        with LIMIT_LOCK:
            active_sister_users.discard(uid)
        if status_msg and status_msg.get("message_id"):
            delete_message(chat, status_msg["message_id"])
        send(chat, "ℹ️ No candidate sister patterns generated.")
        return

    active_hits = []
    protected_hits = []

    try:
        # Dedicated 3 workers keeps RAM strictly < 450MB
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="sister-worker") as pool:
            futures = {pool.submit(check_sister_candidate, c): c for c in all_candidates}
            for fut in as_completed(futures):
                try:
                    cand_url, (st, info) = fut.result()
                    if st == "active":
                        active_hits.append(cand_url)
                        tot_dev = info[1] if info and len(info) > 1 and isinstance(info[1], int) else 0
                        on_dev = info[2] if info and len(info) > 2 and isinstance(info[2], int) else 0
                        off_dev = info[3] if info and len(info) > 3 and isinstance(info[3], int) else 0
                        hit_msg = (
                            f"🎯 <b>[SISTER FIREBASE HIT!]</b> 🧬\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🔗 <code>{esc(cand_url)}</code>\n"
                            f"📌 Status: 🟢 <b>ACTIVE / WORKING</b>\n"
                            f"📊 Total Devices: <b>{tot_dev}</b> | 🟢 Online: <b>{on_dev}</b> | 🔴 Offline: <b>{off_dev}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🤫 <i>Confidential sister clone discovered. Private to Admin.</i>"
                        )
                        send(chat, hit_msg)
                    elif st == "protected":
                        protected_hits.append(cand_url)
                        hit_msg = (
                            f"🎯 <b>[SISTER FIREBASE HIT!]</b> 🧬\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🔗 <code>{esc(cand_url)}</code>\n"
                            f"📌 Status: ⚠️ <b>PROTECTED BY AUTH</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🤫 <i>Confidential sister clone discovered. Private to Admin.</i>"
                        )
                        send(chat, hit_msg)
                    elif st == "quota_active":
                        active_hits.append(cand_url)
                        hit_msg = (
                            f"🎯 <b>[SISTER FIREBASE HIT!]</b> 🧬\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🔗 <code>{esc(cand_url)}</code>\n"
                            f"📌 Status: 🟡 <b>ACTIVE / QUOTA EXCEEDED</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🤫 <i>Confidential sister clone discovered. Private to Admin.</i>"
                        )
                        send(chat, hit_msg)
                except Exception:
                    pass

        active_hits = list(dict.fromkeys(active_hits))
        protected_hits = list(dict.fromkeys(protected_hits))
        total_hits = len(active_hits) + len(protected_hits)

        if total_hits == 0:
            send(chat, f"ℹ️ <b>Sister DB Scan Complete:</b>\nScanned <code>{len(all_candidates):,}</code> candidate endpoints across {len(targets)} target(s).\nNo active sister databases discovered.", kb([[btn("🔙 Back to Admin", "admin:panel")]]))
        elif total_hits <= 10:
            summary = (
                f"✅ <b>Sister DB Scan Completed!</b>\n\n"
                f"🎯 Total Hits Discovered: <b>{total_hits}</b>\n"
                f"🟢 Active: <b>{len(active_hits)}</b> | ⚠️ Protected: <b>{len(protected_hits)}</b>\n\n"
                f"All individual confidential HIT messages have been delivered above to your private chat!"
            )
            send(chat, summary, kb([[btn("🔙 Back to Admin", "admin:panel")]]))
        else:
            temp_dir = tempfile.gettempdir()
            temp_file = Path(temp_dir) / f"Secret_Sister_Hits_{int(time.time())}.txt"
            content_txt = "=== ACTIVE SISTER DATABASES ===\n" + "\n".join(active_hits) + "\n\n=== PROTECTED SISTER DATABASES ===\n" + "\n".join(protected_hits)
            temp_file.write_text(content_txt, encoding="utf-8")
            caption = (
                f"🎯 <b>[SECRET SISTER FIREBASE HITS]</b> 🧬\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 Targets Tested: <code>{len(targets)}</code>\n"
                f"⚡ Endpoints Scanned: <code>{len(all_candidates):,}</code>\n"
                f"🟢 Active Hits: <code>{len(active_hits):,}</code>\n"
                f"⚠️ Protected Hits: <code>{len(protected_hits):,}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤫 <i>Full private list attached in document.</i>"
            )
            send_document(chat, temp_file, caption=caption)
            temp_file.unlink(missing_ok=True)

        if active_hits:
            try:
                WORKERS.submit(send_to_dump_channel, uid, active_hits, "sister_tracker")
            except Exception:
                pass

    finally:
        session(uid)
        with LIMIT_LOCK:
            active_sister_users.discard(uid)
        if status_msg and status_msg.get("message_id"):
            delete_message(chat, status_msg["message_id"])
        gc.collect()


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

        cost = 0
        charged_cost = 0

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
                    f"💎 <b>Credit used:</b> <code>0 (Free)</code>"
                )
            else:
                send(chat, f"⚠️ <b>Extraction {index}/{len(inputs)}:</b> No Firebase URL found.\n<b>Input:</b> <code>{esc(label[:180])}</code>\n\n💎 <b>Credit used:</b> <code>0 (Free)</code>")

        send_completion_summary(chat, "Extraction", urls, cost, billable_inputs or len(urls))

        send_to_dump_channel(uid, new_urls, source)

    except Exception as exc:
        LOG.exception("Extract error")
        send(chat, f"❌ <b>Extraction Error:</b> <code>{esc(str(exc)[:200])}</code>", main_menu(uid))
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
        gc.collect()
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
            [btn("👮 Admins", "admin:admins"), btn("🧬 Sister DB Tracker", "admin:sister_tracker")],
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
    if action == "admin_sister_tracker":
        submit_work(process_admin_sister_tracker, chat, uid, text)
        return True
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
def callback(q):
    msg = q.get("message", {})
    if not is_private_chat(msg):
        answer(q, "Please use the bot in private chat.")
        return
    data = q.get("data", "").split(":"); action, value = data[0], data[1] if len(data) > 1 else ""
    uid = q["from"]["id"]; chat = msg.get("chat", {}).get("id", uid); mid = msg.get("message_id")
    try:
        if action == "scan" and value == "stop":
            target_uid = int(data[2]) if len(data) > 2 else uid
            if target_uid != uid and not is_admin(uid):
                answer(q, "⚠️ You cannot stop someone else's scan.", alert=True)
                return
            with scan_cancel_lock:
                evt = active_scan_cancellations.get(target_uid)
            if evt and not evt.is_set():
                evt.set()
                answer(q, "🛑 Stopping process...", alert=False)
            else:
                answer(q, "ℹ️ Process is already stopped or finished.", alert=False)
            return
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
            with LIMIT_LOCK:
                active_viewer_users.discard(uid)
                active_extraction_users.discard(uid)
                active_dedup_users.discard(uid)
                active_sister_users.discard(uid)
            welcome(chat, uid, mid)
        elif action == "watchlist":
            if value == "dashboard":
                show_watchlist_dashboard(chat, uid, mid)
            elif value == "add":
                session(uid, {"action": "watchlist_add"})
                edit(chat, mid,
                    "➕ <b>Add Firebase to Watchlist</b>\n\n"
                    "Apne ek ya multiple Firebase URLs bhejein ya <b>.txt</b> file upload karein.\n"
                    "Bot inhe aapki personal Watchlist me save karega aur daily report me monitor karega.",
                    kb([[btn("🔙 Back to Watchlist", "watchlist:dashboard")]]))
            elif value == "remove":
                session(uid, {"action": "watchlist_remove"})
                edit(chat, mid,
                    "➖ <b>Remove Firebase from Watchlist</b>\n\n"
                    "Jo Firebase URL remove karna hai wo bhejein (single ya multiple lines).",
                    kb([[btn("🔙 Back to Watchlist", "watchlist:dashboard")]]))
            elif value == "view":
                watchlist = get_user_watchlist(uid)
                if not watchlist:
                    edit(chat, mid, "📋 <b>Your Saved Watchlist</b>\n\n⚠️ Watchlist abhi empty hai.", kb([[btn("➕ Add Firebase", "watchlist:add"), btn("🔙 Back", "watchlist:dashboard")]]))
                elif len(watchlist) <= 10:
                    lines = "\n".join(f"{i}. <code>{esc(u)}</code>" for i, u in enumerate(watchlist, 1))
                    edit(chat, mid, f"📋 <b>Your Saved Watchlist ({len(watchlist)})</b>\n\n{lines}", kb([[btn("➕ Add Firebase", "watchlist:add"), btn("➖ Remove", "watchlist:remove")], [btn("🔙 Back", "watchlist:dashboard")]]))
                else:
                    temp_dir = tempfile.gettempdir()
                    temp_file = Path(temp_dir) / f"My_Firebase_Watchlist_{int(time.time())}.txt"
                    temp_file.write_text("\n".join(watchlist), encoding="utf-8")
                    send_document(chat, temp_file, caption=f"📋 <b>Your Saved Firebase Watchlist ({len(watchlist):,} URLs)</b>")
                    temp_file.unlink(missing_ok=True)
                    show_watchlist_dashboard(chat, uid)
            elif value == "clear_confirm":
                edit(chat, mid, "⚠️ <b>Are you sure?</b>\n\nKya aap apni Watchlist ke saare Firebase URLs delete karna chahte hain?",
                     kb([[btn("✅ Yes, Clear All", "watchlist:clear_do", "danger")], [btn("❌ Cancel", "watchlist:dashboard")]]))
            elif value == "clear_do":
                clear_user_watchlist(uid)
                answer(q, "✅ Watchlist cleared!")
                show_watchlist_dashboard(chat, uid, mid)
            elif value == "toggle":
                sub_val = data[2] if len(data) > 2 else "off"
                if sub_val == "on":
                    set_user_daily_alert(uid, True)
                    answer(q, "🔔 Daily Report Activated (💎1/Day)!")
                else:
                    set_user_daily_alert(uid, False)
                    answer(q, "🔕 Daily Report Paused (Free)!")
                show_watchlist_dashboard(chat, uid, mid)
            elif value == "check_now":
                submit_work(process_watchlist_check_now, chat, uid)
                answer(q, "⏳ Analyzing Death Clock...")
        elif action == "menu":
            if value == "extract":
                if is_process_running(uid):
                    answer(q, "⚠️ A process is currently running. Please let it finish first.", alert=True)
                    return
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
            elif value == "watchlist":
                show_watchlist_dashboard(chat, uid, mid)
            elif value == "viewer":
                if is_process_running(uid):
                    answer(q, "⚠️ A process is currently running. Please let it finish first.", alert=True)
                    return
                session(uid, {"action": "firebase_view"})
                edit(chat, mid, "📊 <b>Firebase Viewer</b>\n\nSend one or more Firebase Realtime Database URLs, a Base64-wrapped URL, or a .txt file.\n\n✨ <b>100% Free — Unlimited Checks</b>", kb([[btn("❌ Cancel", "nav:home")]]))
            elif value == "dedup":
                if is_process_running(uid):
                    answer(q, "⚠️ A process is currently running. Please let it finish first.", alert=True)
                    return
                session(uid, {"action": "dedup"})
                edit(
                    chat,
                    mid,
                    "🧹 <b>Remove Duplicates (.txt Cleaner)</b>\n\n"
                    "Apni koi bhi <b>.txt</b> file bhejein ya direct text lines bhejein.\n"
                    "Bot usme se saare duplicate lines remove karke ek clean unique <b>.txt</b> file bana kar dega!\n\n"
                    "💎 Cost: <b>1 Diamond</b> (Chahe kitni bhi badi file ho)",
                    kb([[btn("❌ Cancel", "nav:home")]])
                )
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
            elif value == "sister_tracker":
                session(uid, {"action": "admin_sister_tracker"})
                edit(chat, mid,
                    "🧬 <b>Developer / Sister App Tracker (DNA Matcher)</b>\n\n"
                    "Ek ya multiple Firebase URLs bhejein (ya <b>.txt</b> file).\n\n"
                    "Bot developer ke project naming DNA, suffix variants (<code>-backup</code>, <code>-v2</code>, <code>-dev</code>, <code>-api</code>, <code>-panel</code>), "
                    "sequence numbers, aur regional clusters ko scan karke undiscovered sister databases find karega!\n\n"
                    "🤫 <i>Discovered hits will be sent privately to your DM.</i>\n"
                    "⚙️ <i>Dedicated 3 workers and Smart LRU Cache active (under 450MB RAM).</i>",
                    kb([[btn("🔙 Back", "admin:panel")]]))
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
            admin_channels(chat,mid)
        elif action == "image" and is_admin(uid): session(uid,{"action":"image","key":value}); edit(chat,mid,"📷 Send the image photo now.",kb([[btn("🔙 Back","admin:images")]]))
    except Exception:
        LOG.exception("callback failed: %s", q.get("data"))
    finally:
        answer(q)

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
        doc = msg_obj.get("document")
        file_name = doc.get("file_name", "").lower() if doc else ""
        is_apk = bool(doc and (file_name.endswith(".apk") or doc.get("mime_type") == "application/vnd.android.package-archive"))
        is_text_file = bool(doc and (file_name.endswith((".txt", ".text", ".log", ".csv", ".json")) or doc.get("mime_type", "").startswith("text/")))

        if is_apk:
            return queue_apk_extraction(msg_obj)

        if is_text_file:
            path = None
            try:
                path = download(doc["file_id"])
                if state_action == "watchlist_add":
                    text_content = path.read_text(encoding="utf-8", errors="ignore")[:MAX_VIEWER_SCAN]
                    process_watchlist_add(chat, uid, text_content)
                    return
                if state_action == "watchlist_remove":
                    text_content = path.read_text(encoding="utf-8", errors="ignore")[:MAX_VIEWER_SCAN]
                    process_watchlist_remove(chat, uid, text_content)
                    return
                if is_admin(uid) and state_action == "admin_sister_tracker":
                    text_content = path.read_text(encoding="utf-8", errors="ignore")[:MAX_VIEWER_SCAN]
                    submit_work(process_admin_sister_tracker, chat, uid, text_content)
                    return
                if state_action == "dedup":
                    if is_process_running(uid):
                        send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
                        return
                    submit_work(
                        process_dedup_file,
                        {**msg_obj, "downloaded_path": str(path), "doc_name": doc.get("file_name", "data.txt")},
                    )
                    path = None
                    return
                text_content = path.read_text(encoding="utf-8", errors="ignore")[:MAX_VIEWER_SCAN]
                targets = firebase_targets_from_text(text_content)
                if targets or state_action == "firebase_view":
                    if is_process_running(uid):
                        send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
                        return
                    submit_work(process_firebase_view, {**msg_obj, "text": text_content})
                    return
                elif state_action == "extract":
                    if is_process_running(uid):
                        send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
                        return
                    submit_work(process_extraction, msg_obj)
                    return
                else:
                    send(chat, "⚠️ <b>File Check:</b> Is .txt file me koi valid Firebase URL nahi mila.\n\nKripya valid Firebase Realtime Database URLs wali <b>.txt</b> file bhejein.")
                    return
            except Exception as exc:
                send(chat, f"❌ Could not read this text file: <code>{esc(str(exc)[:200])}</code>")
            finally:
                if path:
                    path.unlink(missing_ok=True)
            return

        send(chat, "⚠️ Kripya <b>.apk</b> file ya Firebase URLs wali <b>.txt</b> file bhejein.")
        return
    if is_admin(uid) and state and handle_admin_input(msg_obj, state, text): return

    if state_action == "extract":
        if is_process_running(uid):
            send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
            return
        if not looks_like_url(text):
            send(chat, "⚠️ Please send an APK file or a valid public web/Firebase URL.")
            return
        submit_work(process_extraction, msg_obj)
        return

    if state_action == "firebase_view":
        if is_process_running(uid):
            send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
            return
        if not firebase_targets_from_text(text):
            send(chat, "⚠️ Please send a valid Firebase Realtime Database URL.")
            return
        submit_work(process_firebase_view, msg_obj)
        return

    if state_action == "watchlist_add":
        process_watchlist_add(chat, uid, text)
        return

    if state_action == "watchlist_remove":
        process_watchlist_remove(chat, uid, text)
        return

    if is_admin(uid) and state_action == "admin_sister_tracker":
        submit_work(process_admin_sister_tracker, chat, uid, text)
        return

    if state_action == "dedup":
        if is_process_running(uid):
            send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
            return
        submit_work(process_dedup_text, msg_obj)
        return

    # Auto-detection: If user directly sends Firebase URL(s) without menu, check them immediately
    if firebase_targets_from_text(text):
        if is_process_running(uid):
            send(chat, "⚠️ <b>A process is currently running. Please let it finish first.</b>")
            return
        submit_work(process_firebase_view, msg_obj)
        return

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
        # Respond 200 to all keepalive and health check endpoints
        if self.path in {"/", "/healthz", "/api/healthz", "/ping", "/status"}:
            body = b'{"status":"ok","service":"firebase-bot","alive":true}'
            self.send_response(200)
        else:
            body = b'{"status":"not_found"}'
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args): pass

def self_ping_loop():
    """Keep bot online 24/7 on Render / PaaS free tiers by pinging its own URL every 10 minutes."""
    url = os.getenv("RENDER_EXTERNAL_URL", "https://hoatingbot.onrender.com").strip().rstrip("/")
    target = f"{url}/healthz"
    LOG.info("Keepalive self-ping initialized for %s", target)
    time.sleep(30)
    while True:
        try:
            req = Request(target, headers={"User-Agent": "Bot-KeepAlive/2.0"})
            with urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    LOG.debug("Keepalive ping successful (%s)", resp.status)
        except Exception as exc:
            LOG.warning("Keepalive self-ping ping error: %s", exc)
        time.sleep(600)  # Ping every 10 minutes to stay awake permanently

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


def bot_watchdog():
    while True:
        time.sleep(30)
        if time.monotonic() - last_poll_at > 180:
            LOG.error("Polling appears frozen; restarting bot process")
            os.execv(sys.executable, [sys.executable, *sys.argv])


def main():
    global BOT_USERNAME, TOKEN, API
    port = int(os.getenv("PORT", "8099"))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        LOG.info("Health server listening on port %s", port)
    except OSError as exc:
        LOG.warning("Health server could not bind port %s: %s. Continuing with Telegram bot.", port, exc)

    if not TOKEN:
        LOG.error("Configure TELEGRAM_BOT_TOKEN in environment variables.")
        return

    setup_db()
    try:
        BOT_USERNAME = (tg("getMe") or {}).get("username", "your_bot")
    except Exception as exc:
        LOG.error("Failed to fetch getMe with current TOKEN (%s): %s", TOKEN[:15] + "...", exc)
        if TOKEN != "8216354175:AAEMEq1EwDKj6lgefbkPIR5MhEU3hef9VUo":
            LOG.info("Falling back to valid hardcoded token 8216354175:AAEMEq1EwDKj6lgefbkPIR5MhEU3hef9VUo...")
            TOKEN = "8216354175:AAEMEq1EwDKj6lgefbkPIR5MhEU3hef9VUo"
            API = f"https://api.telegram.org/bot{TOKEN}"
            try:
                BOT_USERNAME = (tg("getMe") or {}).get("username", "your_bot")
            except Exception as e2:
                LOG.critical("Bot token is completely unauthorized: %s", e2)
                raise e2
        else:
            raise exc

    threading.Thread(target=backup_loop, daemon=True, name="mongodb-backup").start()
    threading.Thread(target=self_ping_loop, daemon=True, name="self-keepalive").start()
    threading.Thread(target=daily_watchlist_checker_loop, daemon=True, name="daily-watchlist-worker").start()
    
    tg("deleteWebhook", {"drop_pending_updates": False})
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
                    submit_work(callback, update["callback_query"])
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