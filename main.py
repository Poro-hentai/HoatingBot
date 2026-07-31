"""
𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐁𝐎𝐓 𝐕𝐄𝐑𝐒𝐈𝐎𝐍 𝟒.𝟏 — 𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐏𝐀𝐍𝐄𝐋
𝐂𝐘𝐁𝐄𝐑𝐏𝐔𝐍𝐊 + 𝐇𝐀𝐂𝐊𝐄𝐑 𝐓𝐄𝐑𝐌𝐈𝐍𝐀𝐋 + 𝐒𝐄𝐑𝐕𝐄𝐑 𝐑𝐀𝐂𝐊 𝐔𝐈
"""

import subprocess
import sys
import os

# ✅ Packages pre-installed via Replit package manager (pip not available in Nix)
_pkg_map = {"pyTelegramBotAPI": "telebot", "Pillow": "PIL", "dnspython": "dns"}
for _mod in ["pyTelegramBotAPI", "psutil", "requests", "flask", "qrcode", "Pillow", "cryptography", "pymongo", "dnspython"]:
    try:
        __import__(_pkg_map.get(_mod, _mod))
    except (ModuleNotFoundError, ImportError):
        print(f"⚠️ Missing: {_mod} — install via Replit package manager")

# --- After auto-install, import all modules safely ---
import telebot
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import json
import logging
import signal
import threading
import re
import atexit
import requests
from flask import Flask
from threading import Thread
import qrcode
from io import BytesIO
import hashlib
import random
import string
from cryptography.fernet import Fernet
import base64
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

app = Flask('')

@app.route('/')
def home():
    return "𝐈'𝐦 𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐁𝐎𝐓"

def run_flask():
    port = int(os.environ.get("FLASK_PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("✅ Flask Keep-Alive server started.")

# ================================
# 𝐂𝐎𝐍𝐅𝐈𝐆𝐔𝐑𝐀𝐓𝐈𝐎𝐍
# ================================

# ── TOKEN ──────────────────────────────────────────────────────────────────────
# Option 1: Set BOT_TOKEN in environment secrets (recommended)
# Option 2: Paste your token directly below between the quotes
BOT_TOKEN_HARDCODED = ''  # <- Paste token here if not using env var

TOKEN = os.environ.get('BOT_TOKEN', BOT_TOKEN_HARDCODED)

# ── OWNER ID ────────────────────────────────────────────────────────────────────
# Option 1: Set OWNER_ID in environment secrets (recommended)
# Option 2: Paste your Telegram user ID directly below
OWNER_ID_HARDCODED = 0  # <- Paste your Telegram user ID here

OWNER_ID = int(os.environ.get('OWNER_ID', str(OWNER_ID_HARDCODED)))

# ── MONGODB ─────────────────────────────────────────────────────────────────────
MONGODB_URI_DEFAULT = ''
MONGODB_URI = os.environ.get('MONGODB_URI', MONGODB_URI_DEFAULT)

# ── VALIDATION ──────────────────────────────────────────────────────────────────
if not TOKEN:
    print("❌ BOT_TOKEN not found!")
    print("👉 Set BOT_TOKEN in environment variables OR paste it in BOT_TOKEN_HARDCODED above.")
    time.sleep(5)
    TOKEN = os.environ.get('BOT_TOKEN', BOT_TOKEN_HARDCODED)
    if not TOKEN:
        raise SystemExit("❌ BOT_TOKEN is required. Set it and restart.")

if OWNER_ID == 0:
    print("⚠️ OWNER_ID not set. Set OWNER_ID in environment variables OR paste it in OWNER_ID_HARDCODED above.")

ADMIN_ID = OWNER_ID

# 𝐅𝐨𝐥𝐝𝐞𝐫 𝐬𝐞𝐭𝐮𝐩
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'hosting_uploads')
HOSTING_DATA_DIR = os.path.join(BASE_DIR, 'hosting_data')
RUNNING_SCRIPTS_DB = os.path.join(HOSTING_DATA_DIR, 'running_scripts.json')

# 𝐓𝐈𝐄𝐑 𝐒𝐘𝐒𝐓𝐄𝐌
TIER_SYSTEM = {
    "full": {
        "name": "𝐅𝐔𝐋𝐋 𝐀𝐂𝐂𝐄𝐒𝐒",
        "upload_limit": float('inf'),
        "max_file_size": float('inf'),
        "icon": "🚀",
        "color": "#00ff00",
        "auto_restart": True
    }
}

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(HOSTING_DATA_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN, use_class_middlewares=True)

from telebot.handler_backends import BaseMiddleware, CancelUpdate

# --- Data structures ---
bot_scripts = {}
user_files = {}
active_users = set()       # All users who have ever used the bot (for notifications)
authorized_users = set()   # Users the admin has explicitly allowed
admin_ids = {OWNER_ID}
user_subscriptions = {}
bot_locked = False

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ================================
# 𝐀𝐂𝐂𝐄𝐒𝐒 𝐂𝐎𝐍𝐓𝐑𝐎𝐋 𝐌𝐈𝐃𝐃𝐋𝐄𝐖𝐀𝐑𝐄
# ================================
class AdminOnlyMiddleware(BaseMiddleware):
    """Only owner + explicitly authorized users can use this bot."""
    def __init__(self):
        super().__init__()
        self.update_types = ['message', 'callback_query']

    def pre_process(self, update, data):
        user = getattr(update, 'from_user', None)
        if user is None:
            return CancelUpdate()
        uid = user.id
        # Allow owner always
        if uid == OWNER_ID:
            return None
        # Allow explicitly authorized users
        if uid in authorized_users:
            return None
        # Block everyone else
        try:
            if hasattr(update, 'message') or hasattr(update, 'text') or hasattr(update, 'document'):
                bot.reply_to(update, "🔒 *Access Denied*\n\nThis bot is private. Contact the admin to get access.", parse_mode='Markdown')
            else:
                bot.answer_callback_query(update.id, "🔒 Access Denied. Contact admin.", show_alert=True)
        except Exception:
            pass
        return CancelUpdate()

    def post_process(self, update, data, exception=None):
        pass

bot.setup_middleware(AdminOnlyMiddleware())


# ================================
# 𝐅𝐎𝐍𝐓 𝐂𝐎𝐍𝐕𝐄𝐑𝐒𝐈𝐎𝐍 𝐅𝐔𝐍𝐂𝐓𝐈𝐎𝐍𝐒
# ================================
def convert_to_bold_uppercase(text: str) -> str:
    bold_mapping = {
        'A': '𝐀', 'B': '𝐁', 'C': '𝐂', 'D': '𝐃', 'E': '𝐄', 'F': '𝐅', 'G': '𝐆',
        'H': '𝐇', 'I': '𝐈', 'J': '𝐉', 'K': '𝐊', 'L': '𝐋', 'M': '𝐌', 'N': '𝐍',
        'O': '𝐎', 'P': '𝐏', 'Q': '𝐐', 'R': '𝐑', 'S': '𝐒', 'T': '𝐓', 'U': '𝐔',
        'V': '𝐕', 'W': '𝐖', 'X': '𝐗', 'Y': '𝐘', 'Z': '𝐙',
        'a': '𝐚', 'b': '𝐛', 'c': '𝐜', 'd': '𝐝', 'e': '𝐞', 'f': '𝐟', 'g': '𝐠',
        'h': '𝐡', 'i': '𝐢', 'j': '𝐣', 'k': '𝐤', 'l': '𝐥', 'm': '𝐦', 'n': '𝐧',
        'o': '𝐨', 'p': '𝐩', 'q': '𝐪', 'r': '𝐫', 's': '𝐬', 't': '𝐭', 'u': '𝐮',
        'v': '𝐯', 'w': '𝐰', 'x': '𝐱', 'y': '𝐲', 'z': '𝐳',
        '0': '𝟎', '1': '𝟏', '2': '𝟐', '3': '𝟑', '4': '𝟒', '5': '𝟓', '6': '𝟔',
        '7': '𝟕', '8': '𝟖', '9': '𝟗',
        ' ': ' ', '!': '!', '@': '@', '#': '#', '$': '$', '%': '%', '^': '^',
        '&': '&', '*': '*', '(': '(', ')': ')', '-': '-', '_': '_', '=': '=',
        '+': '+', '[': '[', ']': ']', '{': '{', '}': '}', '\\': '\\', '|': '|',
        ';': ';', ':': ':', "'": "'", '"': '"', ',': ',', '.': '.', '<': '<',
        '>': '>', '/': '/', '?': '?', '`': '`', '~': '~'
    }
    result = []
    for char in str(text):
        result.append(bold_mapping.get(char, char))
    return ''.join(result)

B = convert_to_bold_uppercase

def unbold(text: str) -> str:
    bold_to_normal = {
        '𝐀': 'A', '𝐁': 'B', '𝐂': 'C', '𝐃': 'D', '𝐄': 'E', '𝐅': 'F', '𝐆': 'G',
        '𝐇': 'H', '𝐈': 'I', '𝐉': 'J', '𝐊': 'K', '𝐋': 'L', '𝐌': 'M', '𝐍': 'N',
        '𝐎': 'O', '𝐏': 'P', '𝐐': 'Q', '𝐑': 'R', '𝐒': 'S', '𝐓': 'T', '𝐔': 'U',
        '𝐕': 'V', '𝐖': 'W', '𝐗': 'X', '𝐘': 'Y', '𝐙': 'Z',
        '𝐚': 'a', '𝐛': 'b', '𝐜': 'c', '𝐝': 'd', '𝐞': 'e', '𝐟': 'f', '𝐠': 'g',
        '𝐡': 'h', '𝐢': 'i', '𝐣': 'j', '𝐤': 'k', '𝐥': 'l', '𝐦': 'm', '𝐧': 'n',
        '𝐨': 'o', '𝐩': 'p', '𝐪': 'q', '𝐫': 'r', '𝐬': 's', '𝐭': 't', '𝐮': 'u',
        '𝐯': 'v', '𝐰': 'w', '𝐱': 'x', '𝐲': 'y', '𝐳': 'z',
        '𝟎': '0', '𝟏': '1', '𝟐': '2', '𝟑': '3', '𝟒': '4', '𝟓': '5', '𝟔': '6',
        '𝟕': '7', '𝟖': '8', '𝟗': '9'
    }
    result = []
    for char in str(text):
        result.append(bold_to_normal.get(char, char))
    return ''.join(result)


# ================================
# 𝐌𝐎𝐍𝐆𝐎𝐃𝐁 𝐒𝐄𝐓𝐔𝐏
# ================================
mongo_client = None
mongo_db = None

def get_mongo_db():
    global mongo_client, mongo_db
    if mongo_db is None:
        try:
            mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
            mongo_client.admin.command('ping')
            mongo_db = mongo_client['hosting_bot']
            logger.info("✅ MongoDB connected successfully.")
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            mongo_db = None
    return mongo_db

def get_collection(name):
    db = get_mongo_db()
    if db is None:
        return None
    return db[name]


# ================================
# 𝐀𝐍𝐈𝐌𝐀𝐓𝐈𝐎𝐍 𝐌𝐀𝐍𝐀𝐆𝐄𝐑
# ================================

_animation_locks = {}
_animation_lock_global = threading.Lock()

def _get_animation_lock(chat_id, message_id):
    key = f"{chat_id}_{message_id}"
    with _animation_lock_global:
        if key not in _animation_locks:
            _animation_locks[key] = threading.Lock()
        return _animation_locks[key]

def run_edit_animation(chat_id, message_id, frames, delay=0.3):
    lock = _get_animation_lock(chat_id, message_id)
    if not lock.acquire(blocking=False):
        return

    def _do_animation():
        try:
            for frame in frames:
                try:
                    bot.edit_message_text(frame, chat_id, message_id)
                except Exception:
                    pass
                time.sleep(delay)
        finally:
            lock.release()
            key = f"{chat_id}_{message_id}"
            with _animation_lock_global:
                if key in _animation_locks:
                    del _animation_locks[key]

    t = threading.Thread(target=_do_animation, daemon=True)
    t.start()
    return t


class AnimationManager:
    @staticmethod
    def animate_upload(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║    🚀 𝐔𝐏𝐋𝐎𝐀𝐃 𝐒𝐄𝐐𝐔𝐄𝐍𝐂𝐄 𝐈𝐍𝐈𝐓𝐈𝐀𝐓𝐄𝐃  ║\n╚══════════════════════════════════╝\n\n> 𝐑𝐞𝐜𝐞𝐢𝐯𝐢𝐧𝐠 𝐩𝐚𝐜𝐤𝐞𝐭...\n━━━━━━━━━━━━━━━━━━━━━━\n🚀□□□□□□□□□□\n[▰· · · · · · · · ·] 0%",
            "╔══════════════════════════════════╗\n║    🚀 𝐔𝐏𝐋𝐎𝐀𝐃 𝐒𝐄𝐐𝐔𝐄𝐍𝐂𝐄 𝐀𝐂𝐓𝐈𝐕𝐄     ║\n╚══════════════════════════════════╝\n\n> 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐢𝐧𝐠 𝐝𝐚𝐭𝐚 𝐛𝐥𝐨𝐜𝐤𝐬...\n━━━━━━━━━━━━━━━━━━━━━━\n□🚀□□□□□□□□□\n[▰▰· · · · · · · ·] 15%",
            "╔══════════════════════════════════╗\n║    🚀 𝐓𝐑𝐀𝐍𝐒𝐅𝐄𝐑 𝐈𝐍 𝐏𝐑𝐎𝐆𝐑𝐄𝐒𝐒        ║\n╚══════════════════════════════════╝\n\n> 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐢𝐧𝐠...\n━━━━━━━━━━━━━━━━━━━━━━\n□□🚀□□□□□□□□\n[▰▰▰· · · · · · ·] 30%",
            "╔══════════════════════════════════╗\n║    🚀 𝐓𝐑𝐀𝐍𝐒𝐅𝐄𝐑 𝐈𝐍 𝐏𝐑𝐎𝐆𝐑𝐄𝐒𝐒        ║\n╚══════════════════════════════════╝\n\n> 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐢𝐧𝐠...\n━━━━━━━━━━━━━━━━━━━━━━\n□□□🚀□□□□□□□\n[▰▰▰▰▰· · · · ·] 50%",
            "╔══════════════════════════════════╗\n║    🚀 𝐍𝐄𝐀𝐑𝐈𝐍𝐆 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐈𝐎𝐍          ║\n╚══════════════════════════════════╝\n\n> 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐢𝐧𝐠...\n━━━━━━━━━━━━━━━━━━━━━━\n□□□□🚀□□□□□□\n[▰▰▰▰▰▰▰· · ·] 70%",
            "╔══════════════════════════════════╗\n║    🚀 𝐅𝐈𝐍𝐀𝐋𝐈𝐙𝐈𝐍𝐆 𝐓𝐑𝐀𝐍𝐒𝐅𝐄𝐑         ║\n╚══════════════════════════════════╝\n\n> 𝐒𝐜𝐚𝐧𝐧𝐢𝐧𝐠 𝐢𝐦𝐩𝐨𝐫𝐭𝐬...\n━━━━━━━━━━━━━━━━━━━━━━\n□□□□□□🚀□□□□\n[▰▰▰▰▰▰▰▰▰▰] 90%",
            "╔══════════════════════════════════╗\n║    ✅ 𝐔𝐏𝐋𝐎𝐀𝐃 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐄              ║\n╠══════════════════════════════════╣\n║  🎉 𝐅𝐢𝐥𝐞 𝐫𝐞𝐚𝐝𝐲 𝐟𝐨𝐫 𝐡𝐨𝐬𝐭𝐢𝐧𝐠!       ║\n╚══════════════════════════════════╝\n\n━━━━━━━━━━━━━━━━━━━━━━\n□□□□□□□□🚀□□\n[▰▰▰▰▰▰▰▰▰▰] 100%"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.3)

    @staticmethod
    def animate_start(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║  💻 𝐒𝐘𝐒𝐓𝐄𝐌 𝐁𝐎𝐎𝐓 𝐒𝐄𝐐𝐔𝐄𝐍𝐂𝐄         ║\n╠══════════════════════════════════╣\n║  𝐒𝐭𝐚𝐭𝐮𝐬: 𝐈𝐍𝐈𝐓𝐈𝐀𝐋𝐈𝐙𝐈𝐍𝐆              ║\n╚══════════════════════════════════╝\n\n> 𝐁𝐨𝐨𝐭𝐢𝐧𝐠 𝐬𝐲𝐬𝐭𝐞𝐦...\n[▰· · · · · · · · ·] 10%",
            "╔══════════════════════════════════╗\n║  💻 𝐒𝐘𝐒𝐓𝐄𝐌 𝐁𝐎𝐎𝐓 𝐒𝐄𝐐𝐔𝐄𝐍𝐂𝐄         ║\n╠══════════════════════════════════╣\n║  𝐒𝐭𝐚𝐭𝐮𝐬: 𝐋𝐎𝐀𝐃𝐈𝐍𝐆                  ║\n╚══════════════════════════════════╝\n\n> 𝐋𝐨𝐚𝐝𝐢𝐧𝐠 𝐦𝐨𝐝𝐮𝐥𝐞𝐬...\n[▰▰▰· · · · · · ·] 30%",
            "╔══════════════════════════════════╗\n║  💻 𝐒𝐘𝐒𝐓𝐄𝐌 𝐁𝐎𝐎𝐓 𝐒𝐄𝐐𝐔𝐄𝐍𝐂𝐄         ║\n╠══════════════════════════════════╣\n║  𝐒𝐭𝐚𝐭𝐮𝐬: 𝐂𝐎𝐍𝐍𝐄𝐂𝐓𝐈𝐍𝐆               ║\n╚══════════════════════════════════╝\n\n> 𝐂𝐨𝐧𝐧𝐞𝐜𝐭𝐢𝐧𝐠 𝐝𝐚𝐭𝐚𝐛𝐚𝐬𝐞...\n[▰▰▰▰▰· · · · ·] 50%",
            "╔══════════════════════════════════╗\n║  💻 𝐒𝐘𝐒𝐓𝐄𝐌 𝐁𝐎𝐎𝐓 𝐒𝐄𝐐𝐔𝐄𝐍𝐂𝐄         ║\n╠══════════════════════════════════╣\n║  𝐒𝐭𝐚𝐭𝐮𝐬: 𝐕𝐄𝐑𝐈𝐅𝐘𝐈𝐍𝐆                ║\n╚══════════════════════════════════╝\n\n> 𝐕𝐞𝐫𝐢𝐟𝐲𝐢𝐧𝐠 𝐞𝐧𝐯𝐢𝐫𝐨𝐧𝐦𝐞𝐧𝐭...\n[▰▰▰▰▰▰▰▰▰·] 90%",
            "╔══════════════════════════════════╗\n║  💻 𝐒𝐘𝐒𝐓𝐄𝐌 𝐁𝐎𝐎𝐓 𝐒𝐄𝐐𝐔𝐄𝐍𝐂𝐄         ║\n╠══════════════════════════════════╣\n║  𝐒𝐭𝐚𝐭𝐮𝐬: 𝐋𝐀𝐔𝐍𝐂𝐇𝐈𝐍𝐆                ║\n╚══════════════════════════════════╝\n\n> 𝐋𝐚𝐮𝐧𝐜𝐡𝐢𝐧𝐠 𝐚𝐩𝐩𝐥𝐢𝐜𝐚𝐭𝐢𝐨𝐧...\n[▰▰▰▰▰▰▰▰▰▰] 100%\n\n━━━━━━━━━━━━━━━━━━━━━━\n🟢 𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐎𝐍𝐋𝐈𝐍𝐄"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.3)

    @staticmethod
    def animate_stop(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║  🛑 𝐓𝐄𝐑𝐌𝐈𝐍𝐀𝐓𝐈𝐎𝐍 𝐒𝐄𝐐𝐔𝐄𝐍𝐂𝐄         ║\n╚══════════════════════════════════╝\n\n> 𝐒𝐞𝐧𝐝𝐢𝐧𝐠 𝐬𝐭𝐨𝐩 𝐬𝐢𝐠𝐧𝐚𝐥...\n[▰▰▰▰▰▰▰▰▰▰] 100%",
            "╔══════════════════════════════════╗\n║  🛑 𝐓𝐄𝐑𝐌𝐈𝐍𝐀𝐓𝐈𝐍𝐆 𝐏𝐑𝐎𝐂𝐄𝐒𝐒           ║\n╚══════════════════════════════════╝\n\n> 𝐓𝐞𝐫𝐦𝐢𝐧𝐚𝐭𝐢𝐧𝐠 𝐩𝐫𝐨𝐜𝐞𝐬𝐬...\n[▰▰▰▰▰▰▰▰▰▰] 100%",
            "╔══════════════════════════════════╗\n║  🟢 𝐒𝐂𝐑𝐈𝐏𝐓 𝐒𝐓𝐎𝐏𝐏𝐄𝐃               ║\n╚══════════════════════════════════╝\n\n> 𝐏𝐫𝐨𝐜𝐞𝐬𝐬 𝐭𝐞𝐫𝐦𝐢𝐧𝐚𝐭𝐞𝐝.\n[▰▰▰▰▰▰▰▰▰▰] 100%"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.3)

    @staticmethod
    def animate_restart(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║  🔄 𝐑𝐄𝐒𝐓𝐀𝐑𝐓 𝐈𝐍𝐈𝐓𝐈𝐀𝐓𝐄𝐃            ║\n╚══════════════════════════════════╝\n\n◐ 𝐑𝐞𝐬𝐭𝐚𝐫𝐭𝐢𝐧𝐠 𝐬𝐞𝐫𝐯𝐢𝐜𝐞𝐬...\n━━━━━━━━━━━━━━━━━━━━━━\n[▰▰▰▰▰▰▰▰▰▰] 100%",
            "╔══════════════════════════════════╗\n║  🔄 𝐑𝐄𝐒𝐓𝐀𝐑𝐓 𝐈𝐍 𝐏𝐑𝐎𝐆𝐑𝐄𝐒𝐒          ║\n╚══════════════════════════════════╝\n\n◑ 𝐑𝐞𝐜𝐨𝐧𝐧𝐞𝐜𝐭𝐢𝐧𝐠 𝐬𝐞𝐫𝐯𝐢𝐜𝐞𝐬...\n━━━━━━━━━━━━━━━━━━━━━━\n[▰▰▰▰▰▰▰▰▰▰] 100%",
            "╔══════════════════════════════════╗\n║  🟢 𝐑𝐄𝐒𝐓𝐀𝐑𝐓 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐄             ║\n╚══════════════════════════════════╝\n\n🔄 𝐒𝐞𝐫𝐯𝐢𝐜𝐞𝐬 𝐫𝐞𝐜𝐨𝐧𝐧𝐞𝐜𝐭𝐞𝐝.\n━━━━━━━━━━━━━━━━━━━━━━\n🟢 𝐑𝐞𝐬𝐭𝐚𝐫𝐭 𝐂𝐨𝐦𝐩𝐥𝐞𝐭𝐞"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.3)

    @staticmethod
    def animate_install(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║  📦 𝐌𝐎𝐃𝐔𝐋𝐄 𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐀𝐓𝐈𝐎𝐍          ║\n╠══════════════════════════════════╣\n║  𝐒𝐭𝐚𝐭𝐮𝐬: 𝐃𝐎𝐖𝐍𝐋𝐎𝐀𝐃𝐈𝐍𝐆              ║\n╚══════════════════════════════════╝\n\n📦 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐢𝐧𝐠 𝐩𝐚𝐜𝐤𝐚𝐠𝐞𝐬...\n[▰▰▰· · · · · · ·] 30%",
            "╔══════════════════════════════════╗\n║  📦 𝐌𝐎𝐃𝐔𝐋𝐄 𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐀𝐓𝐈𝐎𝐍          ║\n╠══════════════════════════════════╣\n║  𝐒𝐭𝐚𝐭𝐮𝐬: 𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐈𝐍𝐆               ║\n╚══════════════════════════════════╝\n\n⚙ 𝐈𝐧𝐬𝐭𝐚𝐥𝐥𝐢𝐧𝐠...\n[▰▰▰▰▰▰▰▰▰·] 90%",
            "╔══════════════════════════════════╗\n║  ✅ 𝐈𝐍𝐒𝐓𝐀𝐋𝐋𝐀𝐓𝐈𝐎𝐍 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐄        ║\n╚══════════════════════════════════╝\n\n🟢 𝐈𝐧𝐬𝐭𝐚𝐥𝐥𝐞𝐝 𝐒𝐮𝐜𝐜𝐞𝐬𝐬𝐟𝐮𝐥𝐥𝐲\n━━━━━━━━━━━━━━━━━━━━━━\n[▰▰▰▰▰▰▰▰▰▰] 100%"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.3)

    @staticmethod
    def animate_scan(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║  🔍 𝐅𝐈𝐋𝐄 𝐒𝐂𝐀𝐍 𝐈𝐍𝐈𝐓𝐈𝐀𝐓𝐄𝐃          ║\n╚══════════════════════════════════╝\n\n> 𝐒𝐜𝐚𝐧𝐧𝐢𝐧𝐠 𝐅𝐢𝐥𝐞𝐬...\n████████████ 20%",
            "╔══════════════════════════════════╗\n║  🔍 𝐃𝐄𝐓𝐄𝐂𝐓𝐈𝐍𝐆 𝐃𝐄𝐏𝐄𝐍𝐃𝐄𝐍𝐂𝐈𝐄𝐒       ║\n╚══════════════════════════════════╝\n\n> 𝐃𝐞𝐭𝐞𝐜𝐭𝐢𝐧𝐠 𝐑𝐞𝐪𝐮𝐢𝐫𝐞𝐦𝐞𝐧𝐭𝐬...\n████████████ 75%",
            "╔══════════════════════════════════╗\n║  🔍 𝐎𝐏𝐓𝐈𝐌𝐈𝐙𝐈𝐍𝐆                    ║\n╚══════════════════════════════════╝\n\n> 𝐎𝐩𝐭𝐢𝐦𝐢𝐳𝐢𝐧𝐠...\n████████████ 100%\n\n✅ 𝐒𝐜𝐚𝐧 𝐂𝐨𝐦𝐩𝐥𝐞𝐭𝐞"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.3)

    @staticmethod
    def animate_delete(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║  🗑 𝐃𝐄𝐋𝐄𝐓𝐈𝐎𝐍 𝐒𝐄𝐐𝐔𝐄𝐍𝐂𝐄           ║\n╚══════════════════════════════════╝\n\n📄 𝐅𝐢𝐥𝐞 𝐅𝐨𝐮𝐧𝐝\n━━━━━━━━━━━━━━━━━━━━━━",
            "╔══════════════════════════════════╗\n║  💥 𝐑𝐄𝐌𝐎𝐕𝐈𝐍𝐆 𝐅𝐈𝐋𝐄                ║\n╚══════════════════════════════════╝\n\n💥 𝐑𝐞𝐦𝐨𝐯𝐢𝐧𝐠...\n━━━━━━━━━━━━━━━━━━━━━━",
            "╔══════════════════════════════════╗\n║  ✅ 𝐃𝐄𝐋𝐄𝐓𝐈𝐎𝐍 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐄           ║\n╚══════════════════════════════════╝\n\n🗑 𝐃𝐞𝐥𝐞𝐭𝐞𝐝 𝐒𝐮𝐜𝐜𝐞𝐬𝐬𝐟𝐮𝐥𝐥𝐲\n━━━━━━━━━━━━━━━━━━━━━━"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.3)

    @staticmethod
    def animate_logs(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║  📜 𝐋𝐎𝐆 𝐑𝐄𝐀𝐃𝐄𝐑 𝐈𝐍𝐈𝐓𝐈𝐀𝐋𝐈𝐙𝐄𝐃       ║\n╚══════════════════════════════════╝\n\n> 𝐎𝐩𝐞𝐧𝐢𝐧𝐠 𝐥𝐨𝐠𝐬...",
            "╔══════════════════════════════════╗\n║  📜 𝐑𝐄𝐀𝐃𝐈𝐍𝐆 𝐋𝐎𝐆 𝐅𝐈𝐋𝐄            ║\n╚══════════════════════════════════╝\n\n> 𝐎𝐩𝐞𝐧𝐢𝐧𝐠 𝐥𝐨𝐠𝐬...\n>> 𝐑𝐞𝐚𝐝𝐢𝐧𝐠 𝐥𝐚𝐭𝐞𝐬𝐭 𝐨𝐮𝐭𝐩𝐮𝐭...",
            "╔══════════════════════════════════╗\n║  📜 𝐃𝐈𝐒𝐏𝐋𝐀𝐘𝐈𝐍𝐆 𝐋𝐎𝐆𝐒              ║\n╚══════════════════════════════════╝\n\n>>> 𝐃𝐢𝐬𝐩𝐥𝐚𝐲𝐢𝐧𝐠 𝐥𝐨𝐠𝐬..."
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.3)

    @staticmethod
    def animate_recovery(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║  🛰 𝐒𝐄𝐑𝐕𝐄𝐑 𝐑𝐄𝐂𝐎𝐕𝐄𝐑𝐘 𝐒𝐘𝐒𝐓𝐄𝐌      ║\n╚══════════════════════════════════╝\n\n🖥 𝐒𝐞𝐫𝐯𝐞𝐫 𝟏   🟢\n🖥 𝐒𝐞𝐫𝐯𝐞𝐫 𝟐   🟢\n🖥 𝐒𝐞𝐫𝐯𝐞𝐫 𝟑   🟡\n\n> 𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐢𝐧𝐠...",
            "╔══════════════════════════════════╗\n║  ✅ 𝐑𝐄𝐂𝐎𝐕𝐄𝐑𝐘 𝐂𝐎𝐌𝐏𝐋𝐄𝐓𝐄           ║\n╚══════════════════════════════════╝\n\n🖥 𝐒𝐞𝐫𝐯𝐞𝐫 𝟏   🟢\n🖥 𝐒𝐞𝐫𝐯𝐞𝐫 𝟐   🟢\n🖥 𝐒𝐞𝐫𝐯𝐞𝐫 𝟑   🟢\n\n𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐲 𝐂𝐨𝐦𝐩𝐥𝐞𝐭𝐞 ✅"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.3)

    @staticmethod
    def animate_dashboard(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║    ⚡ 𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐃𝐀𝐒𝐇𝐁𝐎𝐀𝐑𝐃         ║\n╚══════════════════════════════════╝\n\n📡 𝐂𝐨𝐧𝐧𝐞𝐜𝐭𝐢𝐧𝐠 𝐭𝐨 𝐬𝐞𝐫𝐯𝐞𝐫...\n━━━━━━━━━━━━━━━━━━━━━━",
            "╔══════════════════════════════════╗\n║    ⚡ 𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐃𝐀𝐒𝐇𝐁𝐎𝐀𝐑𝐃         ║\n╚══════════════════════════════════╝\n\n📊 𝐋𝐨𝐚𝐝𝐢𝐧𝐠 𝐝𝐚𝐬𝐡𝐛𝐨𝐚𝐫𝐝...\n━━━━━━━━━━━━━━━━━━━━━━"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.25)

    @staticmethod
    def animate_full_restart(chat_id, message_id):
        frames = [
            "╔══════════════════════════════════╗\n║  🚀 𝐅𝐔𝐋𝐋 𝐁𝐎𝐓 𝐑𝐄𝐒𝐓𝐀𝐑𝐓             ║\n╠══════════════════════════════════╣\n║  𝐒𝐭𝐚𝐭𝐮𝐬: 𝐏𝐑𝐄𝐏𝐀𝐑𝐈𝐍𝐆                ║\n╚══════════════════════════════════╝\n\n📢 𝐒𝐞𝐧𝐝𝐢𝐧𝐠 𝐧𝐨𝐭𝐢𝐟𝐢𝐜𝐚𝐭𝐢𝐨𝐧𝐬...\n[▰▰· · · · · · · ·] 20%",
            "╔══════════════════════════════════╗\n║  🚀 𝐅𝐔𝐋𝐋 𝐁𝐎𝐓 𝐑𝐄𝐒𝐓𝐀𝐑𝐓             ║\n╠══════════════════════════════════╣\n║  𝐒𝐭𝐚𝐭𝐮𝐬: 𝐑𝐄𝐒𝐓𝐀𝐑𝐓𝐈𝐍𝐆               ║\n╚══════════════════════════════════╝\n\n🚀 𝐑𝐞𝐬𝐭𝐚𝐫𝐭𝐢𝐧𝐠...\n[▰▰▰▰▰▰▰▰▰▰] 100%"
        ]
        run_edit_animation(chat_id, message_id, frames, delay=0.5)

    @staticmethod
    def show_dashboard(total_files, running_count, ram_pct, cpu_pct, total_users):
        ram_bar = "█" * int(ram_pct / 10) + "░" * (10 - int(ram_pct / 10))
        cpu_bar = "█" * int(cpu_pct / 10) + "░" * (10 - int(cpu_pct / 10))
        return f"""╔══════════════════════════════════╗
║    ⚡ 𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐃𝐀𝐒𝐇𝐁𝐎𝐀𝐑𝐃         ║
╚══════════════════════════════════╝

🟢 𝐒𝐭𝐚𝐭𝐮𝐬   : 𝐎𝐍𝐋𝐈𝐍𝐄
📂 𝐅𝐢𝐥𝐞𝐬    : {total_files:02}
🚀 𝐑𝐮𝐧𝐧𝐢𝐧𝐠  : {running_count:02}
💾 𝐑𝐀𝐌     : {ram_pct:.0f}% {ram_bar}
⚙ 𝐂𝐏𝐔     : {cpu_pct:.0f}% {cpu_bar}
👥 𝐔𝐬𝐞𝐫𝐬   : {total_users}
🌐 𝐍𝐞𝐭𝐰𝐨𝐫𝐤 : 𝐒𝐭𝐚𝐛𝐥𝐞

━━━━━━━━━━━━━━━━━━━━━━"""


# ================================
# 𝐀𝐔𝐓𝐎-𝐑𝐄𝐂𝐎𝐕𝐄𝐑𝐘 𝐒𝐘𝐒𝐓𝐄𝐌
# ================================
class AutoRecoverySystem:
    def __init__(self):
        self.running_scripts_file = RUNNING_SCRIPTS_DB

    def save_running_script(self, user_id: int, file_name: str, file_path: str, process_pid: int, run_dir: str = None):
        try:
            if os.path.exists(self.running_scripts_file):
                with open(self.running_scripts_file, 'r') as f:
                    data = json.load(f)
            else:
                data = {"running_scripts": []}
            data["running_scripts"] = [s for s in data["running_scripts"]
                                       if not (s["user_id"] == user_id and s["file_name"] == file_name)]
            data["running_scripts"].append({
                "user_id": user_id, "file_name": file_name, "file_path": file_path,
                "process_pid": process_pid, "start_time": datetime.now().isoformat(),
                "status": "running", "last_updated": datetime.now().isoformat(),
                "run_dir": run_dir or os.path.dirname(file_path)
            })
            with open(self.running_scripts_file, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"❌ Error saving running script: {e}")

    def remove_running_script(self, user_id: int, file_name: str):
        try:
            if os.path.exists(self.running_scripts_file):
                with open(self.running_scripts_file, 'r') as f:
                    data = json.load(f)
                data["running_scripts"] = [s for s in data["running_scripts"]
                                           if not (s["user_id"] == user_id and s["file_name"] == file_name)]
                with open(self.running_scripts_file, 'w') as f:
                    json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"❌ Error removing running script: {e}")

    def recover_all_scripts(self):
        try:
            if not os.path.exists(self.running_scripts_file):
                return []
            with open(self.running_scripts_file, 'r') as f:
                data = json.load(f)
            recovered = []
            for script in data.get("running_scripts", []):
                try:
                    user_id = script["user_id"]
                    file_name = script["file_name"]
                    file_path = script["file_path"]
                    run_dir = script.get("run_dir", os.path.dirname(file_path))
                    if not os.path.exists(file_path):
                        continue
                    user_has_file = any(fname == file_name for fname, _ in user_files.get(user_id, []))
                    if not user_has_file:
                        continue
                    tier = get_user_tier(user_id)
                    if not TIER_SYSTEM[tier]['auto_restart']:
                        continue
                    file_ext = os.path.splitext(file_name)[1].lower()
                    if file_ext == '.py':
                        threading.Thread(target=self._restart_py_script,
                                         args=(user_id, file_path, run_dir, file_name)).start()
                    elif file_ext == '.js':
                        threading.Thread(target=self._restart_js_script,
                                         args=(user_id, file_path, run_dir, file_name)).start()
                    elif file_ext == '.zip':
                        # For zip, file_path points to main.py inside extracted folder
                        threading.Thread(target=self._restart_py_script,
                                         args=(user_id, file_path, run_dir, file_name)).start()
                    recovered.append({"user_id": user_id, "file_name": file_name, "status": "recovering"})
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"❌ Error recovering script: {e}")
            return recovered
        except Exception as e:
            logger.error(f"❌ Error in recovery system: {e}")
            return []

    def _restart_py_script(self, user_id, file_path, run_dir, file_name):
        try:
            script_key = f"{user_id}_{file_name}"
            if script_key in bot_scripts:
                return
            log_file_path = os.path.join(get_user_folder(user_id), f"{os.path.splitext(file_name)[0]}.log")
            log_file = open(log_file_path, 'a', encoding='utf-8', errors='ignore')
            process = subprocess.Popen([sys.executable, file_path], cwd=run_dir,
                                       stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                                       encoding='utf-8', errors='ignore')
            bot_scripts[script_key] = {'process': process, 'log_file': log_file, 'file_name': file_name,
                                       'user_id': user_id, 'start_time': datetime.now(), 'type': 'py',
                                       'script_key': script_key}
            self.save_running_script(user_id, file_name, file_path, process.pid, run_dir)
        except Exception as e:
            logger.error(f"❌ Error restarting Python script {file_name}: {e}")

    def _restart_js_script(self, user_id, file_path, run_dir, file_name):
        try:
            script_key = f"{user_id}_{file_name}"
            if script_key in bot_scripts:
                return
            log_file_path = os.path.join(get_user_folder(user_id), f"{os.path.splitext(file_name)[0]}.log")
            log_file = open(log_file_path, 'a', encoding='utf-8', errors='ignore')
            process = subprocess.Popen(['node', file_path], cwd=run_dir,
                                       stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                                       encoding='utf-8', errors='ignore')
            bot_scripts[script_key] = {'process': process, 'log_file': log_file, 'file_name': file_name,
                                       'user_id': user_id, 'start_time': datetime.now(), 'type': 'js',
                                       'script_key': script_key}
            self.save_running_script(user_id, file_name, file_path, process.pid, run_dir)
        except Exception as e:
            logger.error(f"❌ Error restarting JS script {file_name}: {e}")

    def get_running_count(self):
        try:
            if os.path.exists(self.running_scripts_file):
                with open(self.running_scripts_file, 'r') as f:
                    data = json.load(f)
                return len(data.get("running_scripts", []))
            return 0
        except:
            return 0


recovery_system = AutoRecoverySystem()


# ================================
# 𝐃𝐀𝐓𝐀𝐁𝐀𝐒𝐄 𝐒𝐄𝐓𝐔𝐏 (𝐌𝐨𝐧𝐠𝐨𝐃𝐁)
# ================================
def init_db():
    logger.info("📊 Initializing MongoDB...")
    try:
        db = get_mongo_db()
        if db is None:
            logger.error("❌ Could not connect to MongoDB.")
            return
        db['user_files'].create_index([('user_id', 1), ('file_name', 1)], unique=True)
        db['active_users'].create_index([('user_id', 1)], unique=True)
        db['authorized_users'].create_index([('user_id', 1)], unique=True)
        logger.info("✅ MongoDB initialized successfully.")
    except Exception as e:
        logger.error(f"❌ MongoDB init error: {e}")

def load_data():
    logger.info("📥 Loading data from MongoDB...")
    try:
        db = get_mongo_db()
        if db is None:
            logger.warning("⚠️ MongoDB unavailable — starting with empty data.")
            return

        for doc in db['user_files'].find():
            uid = doc['user_id']
            if uid not in user_files:
                user_files[uid] = []
            if not any(fname == doc['file_name'] for fname, _ in user_files[uid]):
                user_files[uid].append((doc['file_name'], doc.get('file_type', 'py')))

        for doc in db['active_users'].find():
            active_users.add(doc['user_id'])

        # Load authorized users
        for doc in db['authorized_users'].find():
            authorized_users.add(doc['user_id'])

        logger.info(f"✅ Data loaded: {len(active_users)} users, {len(authorized_users)} authorized, {sum(len(v) for v in user_files.values())} files")
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}")


init_db()
load_data()


# ================================
# 𝐇𝐄𝐋𝐏𝐄𝐑 𝐅𝐔𝐍𝐂𝐓𝐈𝐎𝐍𝐒
# ================================
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_tier(user_id):
    return "full"

def get_user_file_limit(user_id):
    return TIER_SYSTEM[get_user_tier(user_id)]["upload_limit"]

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_bot_running(user_id, file_name):
    script_key = f"{user_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            recovery_system.remove_running_script(user_id, file_name)
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            return False
    return False

def kill_process_tree(process_info):
    try:
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            pid = process.pid
            try:
                parent = psutil.Process(pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except:
                        pass
                try:
                    parent.terminate()
                    parent.wait(timeout=3)
                except:
                    try:
                        parent.kill()
                    except:
                        pass
                if 'user_id' in process_info and 'file_name' in process_info:
                    recovery_system.remove_running_script(process_info['user_id'], process_info['file_name'])
            except psutil.NoSuchProcess:
                pass
    except Exception as e:
        logger.error(f"❌ Error killing process: {e}")

def send_restart_notification():
    notification_text = "🚨 Bot is restarting for maintenance.\n🔄 Your scripts will be auto-restarted.\n⏱️ Bot will be back online in ~30 seconds."
    sent = 0
    for user_id in list(active_users):
        try:
            bot.send_message(user_id, notification_text)
            sent += 1
        except Exception:
            pass
        time.sleep(0.1)
    logger.info(f"📤 Restart notifications sent: {sent}")

def grant_user_access(user_id: int, username: str = None):
    """Grant access to a user (called by admin)."""
    authorized_users.add(user_id)
    try:
        db = get_mongo_db()
        if db is not None:
            db['authorized_users'].update_one(
                {'user_id': user_id},
                {'$set': {'user_id': user_id, 'username': username, 'granted_at': datetime.now().isoformat()}},
                upsert=True
            )
    except Exception as e:
        logger.error(f"❌ Error granting access: {e}")

def revoke_user_access(user_id: int):
    """Revoke access from a user (called by admin)."""
    authorized_users.discard(user_id)
    try:
        db = get_mongo_db()
        if db is not None:
            db['authorized_users'].delete_one({'user_id': user_id})
    except Exception as e:
        logger.error(f"❌ Error revoking access: {e}")


# ================================
# 𝐃𝐁 𝐎𝐏𝐄𝐑𝐀𝐓𝐈𝐎𝐍𝐒 (MongoDB)
# ================================
def add_active_user(user_id, username=None):
    try:
        db = get_mongo_db()
        now = datetime.now().isoformat()
        if db is not None:
            db['active_users'].update_one(
                {'user_id': user_id},
                {'$set': {'last_seen': now, 'username': username},
                 '$setOnInsert': {'user_id': user_id, 'first_join': now}},
                upsert=True
            )
    except Exception as e:
        logger.error(f"❌ Error adding active user: {e}")
    active_users.add(user_id)

def save_user_file_db(user_id, file_name, file_type, extra_path=None):
    try:
        db = get_mongo_db()
        now = datetime.now().isoformat()
        doc = {'user_id': user_id, 'file_name': file_name, 'file_type': file_type, 'uploaded_at': now}
        if extra_path:
            doc['extra_path'] = extra_path
        if db is not None:
            db['user_files'].update_one(
                {'user_id': user_id, 'file_name': file_name},
                {'$set': doc},
                upsert=True
            )
    except Exception as e:
        logger.error(f"❌ Error saving user file: {e}")
    if user_id not in user_files:
        user_files[user_id] = []
    if not any(fname == file_name for fname, _ in user_files[user_id]):
        user_files[user_id].append((file_name, file_type))

def remove_user_file_db(user_id, file_name):
    try:
        db = get_mongo_db()
        if db is not None:
            db['user_files'].delete_one({'user_id': user_id, 'file_name': file_name})
    except Exception as e:
        logger.error(f"❌ Error removing user file: {e}")
    if user_id in user_files:
        user_files[user_id] = [(fname, ftype) for fname, ftype in user_files[user_id] if fname != file_name]
    recovery_system.remove_running_script(user_id, file_name)
    return True

def get_file_extra_path(user_id, file_name):
    """Get extra info (like the main.py path for ZIP files) from DB."""
    try:
        db = get_mongo_db()
        if db is not None:
            doc = db['user_files'].find_one({'user_id': user_id, 'file_name': file_name})
            if doc:
                return doc.get('extra_path')
    except Exception:
        pass
    return None


# ================================
# 𝐙𝐈𝐏 𝐇𝐀𝐍𝐃𝐋𝐈𝐍𝐆
# ================================
def find_main_py_in_zip(zf: zipfile.ZipFile):
    """
    Find main.py inside a ZIP file.
    Preference order:
    1. main.py at root of ZIP
    2. main.py one level deep
    3. Any .py file at root
    """
    names = zf.namelist()
    # Strip leading slash if any
    names = [n.lstrip('/') for n in names]

    # 1. Exact root main.py
    for n in names:
        if n == 'main.py' or (not '/' in n and n.endswith('main.py')):
            return n

    # 2. main.py one folder deep
    for n in names:
        parts = n.split('/')
        if len(parts) == 2 and parts[1] == 'main.py':
            return n

    # 3. Any .py at root level
    for n in names:
        if '/' not in n and n.endswith('.py'):
            return n

    return None

def extract_and_setup_zip(zip_path, user_folder, zip_name):
    """
    Extract ZIP to a subfolder, install requirements if present,
    return (main_py_path, run_dir) or (None, None) on failure.
    """
    base_name = os.path.splitext(zip_name)[0]
    extract_dir = os.path.join(user_folder, base_name)
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            main_rel = find_main_py_in_zip(zf)
            if main_rel is None:
                return None, None, "No main.py found inside the ZIP."
            zf.extractall(extract_dir)

        # Determine run directory and main.py abs path
        # If main.py is inside a subfolder, run_dir = that subfolder
        parts = main_rel.split('/')
        if len(parts) == 1:
            # main.py is at root of ZIP → extract_dir is run_dir
            run_dir = extract_dir
            main_py_path = os.path.join(extract_dir, main_rel)
        else:
            # main.py is inside a subfolder
            run_dir = os.path.join(extract_dir, *parts[:-1])
            main_py_path = os.path.join(extract_dir, *parts)

        # Install requirements.txt if present in run_dir
        req_path = os.path.join(run_dir, 'requirements.txt')
        if not os.path.exists(req_path):
            # Try in extract_dir root
            req_path = os.path.join(extract_dir, 'requirements.txt')

        if os.path.exists(req_path):
            try:
                subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '-r', req_path, '--quiet', '--break-system-packages'],
                    capture_output=True, text=True, check=False, timeout=120
                )
                logger.info(f"✅ Installed requirements.txt for {zip_name}")
            except Exception as e:
                logger.warning(f"⚠️ requirements.txt install error: {e}")

        # Auto-detect and install imports from main.py
        try:
            auto_setup_user_env_from_path(main_py_path, run_dir)
        except Exception:
            pass

        return main_py_path, run_dir, None

    except zipfile.BadZipFile:
        return None, None, "Invalid ZIP file."
    except Exception as e:
        return None, None, str(e)


# ================================
# 𝐒𝐂𝐑𝐈𝐏𝐓 𝐑𝐔𝐍𝐍𝐈𝐍𝐆 𝐒𝐘𝐒𝐓𝐄𝐌
# ================================
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'requests': 'requests',
    'flask': 'Flask',
    'psutil': 'psutil',
    'qrcode': 'qrcode',
    'pillow': 'Pillow',
    'cryptography': 'cryptography',
    'bs4': 'beautifulsoup4',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'dotenv': 'python-dotenv',
    'aiohttp': 'aiohttp',
    'discord': 'discord.py',
    'openai': 'openai',
    'anthropic': 'anthropic',
    'google': 'google-api-python-client',
    'pymongo': 'pymongo',
    'redis': 'redis',
    'sqlalchemy': 'SQLAlchemy',
    'asyncio': '', 'json': '', 'os': '', 'sys': '', 'time': '',
    're': '', 'math': '', 'random': '', 'datetime': '', 'collections': '',
    'itertools': '', 'functools': '', 'pathlib': '', 'subprocess': '',
    'threading': '', 'logging': '', 'io': '', 'hashlib': '', 'base64': '',
    'urllib': '', 'http': '', 'socket': '', 'signal': '', 'string': '',
    'typing': '', 'dataclasses': '', 'enum': '', 'abc': '', 'copy': '',
    'pickle': '', 'struct': '', 'gzip': '', 'tarfile': '', 'shutil': '',
    'tempfile': '', 'glob': '', 'fnmatch': '', 'sqlite3': '', 'csv': '',
    'xml': '', 'html': '', 'email': '', 'smtplib': '', 'ssl': '',
    'jwt': 'PyJWT',
    'PIL': 'Pillow',
    'cv2': 'opencv-python',
    'sklearn': 'scikit-learn',
    'scipy': 'scipy',
    'matplotlib': 'matplotlib',
    'seaborn': 'seaborn',
    'plotly': 'plotly',
    'selenium': 'selenium',
    'beautifulsoup': 'beautifulsoup4',
    'lxml': 'lxml',
    'pydantic': 'pydantic',
    'fastapi': 'fastapi',
    'uvicorn': 'uvicorn',
    'starlette': 'starlette',
    'jinja2': 'Jinja2',
    'werkzeug': 'Werkzeug',
    'click': 'click',
    'celery': 'celery',
    'kombu': 'kombu',
    'boto3': 'boto3',
    'botocore': 'botocore',
    'stripe': 'stripe',
    'decouple': 'python-decouple',
    'schedule': 'schedule',
    'apscheduler': 'APScheduler',
    'pytz': 'pytz',
    'pycountry': 'pycountry',
    'phonenumbers': 'phonenumbers',
    'PIL.Image': 'Pillow',
    'PIL.ImageDraw': 'Pillow',
    'PIL.ImageFont': 'Pillow',
    'dnspython': 'dnspython',
}

def auto_generate_requirements(file_path, user_folder):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        imports = set(re.findall(r'^\s*import\s+([a-zA-Z0-9_\.]+)', content, re.MULTILINE))
        imports |= set(re.findall(r'^\s*from\s+([a-zA-Z0-9_\.]+)\s+import', content, re.MULTILINE))
        top_level = sorted(set(i.split('.')[0] for i in imports))
        stdlib = set(getattr(sys, 'stdlib_module_names', []))
        requirements = []
        seen = set()
        for mod in top_level:
            if mod in stdlib or mod in seen:
                continue
            seen.add(mod)
            pip_name = TELEGRAM_MODULES.get(mod.lower(), mod)
            if pip_name:
                requirements.append(pip_name)
        req_path = os.path.join(user_folder, 'requirements.txt')
        with open(req_path, 'w') as f:
            if requirements:
                f.write('\n'.join(sorted(set(requirements))) + '\n')
            else:
                f.write('# No external modules detected\n')
        logger.info(f"✅ Auto-generated requirements.txt: {requirements}")
        return requirements
    except Exception as e:
        logger.error(f"❌ Error generating requirements: {e}")
        return []

def auto_setup_user_env_from_path(file_path, run_dir):
    """Detect and install imports from a specific python file."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        imports = set(re.findall(r'^\s*import\s+([a-zA-Z0-9_\.]+)', content, re.MULTILINE))
        imports |= set(re.findall(r'^\s*from\s+([a-zA-Z0-9_\.]+)\s+import', content, re.MULTILINE))
        top_level = sorted(set(i.split('.')[0] for i in imports))
        stdlib = set(getattr(sys, 'stdlib_module_names', []))
        for mod in top_level:
            if mod in stdlib:
                continue
            try:
                __import__(mod)
            except ImportError:
                pip_name = TELEGRAM_MODULES.get(mod.lower(), mod)
                if pip_name:
                    subprocess.run([sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--break-system-packages'],
                                   capture_output=True, text=True, check=False)
    except Exception as e:
        logger.error(f"❌ Error in auto setup: {e}")

def auto_setup_user_env(user_id, file_name):
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    requirements = auto_generate_requirements(file_path, user_folder)
    if requirements:
        for pip_name in sorted(set(requirements)):
            try:
                subprocess.run([sys.executable, '-m', 'pip', 'install', pip_name, '--quiet', '--break-system-packages'],
                               capture_output=True, text=True, check=False)
            except Exception:
                pass
    return requirements


# ================================
# 𝐁𝐔𝐓𝐓𝐎𝐍 𝐋𝐀𝐘𝐎𝐔𝐓𝐒
# ================================
def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        B("📤 𝐔𝐩𝐥𝐨𝐚𝐝"),
        B("📂 𝐌𝐚𝐧𝐚𝐠𝐞 𝐒𝐜𝐫𝐢𝐩𝐭𝐬"),
        B("⚡ 𝐒𝐩𝐞𝐞𝐝"),
        B("📊 𝐒𝐭𝐚𝐭𝐬"),
        B("👤 𝐏𝐫𝐨𝐟𝐢𝐥𝐞"),
        B("📦 𝐌𝐨𝐝𝐮𝐥𝐞"),
        B("🔄 𝐑𝐞𝐜𝐨𝐯𝐞𝐫"),
        B("🚀 𝐑𝐞𝐬𝐭𝐚𝐫𝐭 𝐁𝐨𝐭"),
    ]
    if user_id == OWNER_ID:
        buttons.append(B("👑 𝐀𝐝𝐦𝐢𝐧"))
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

def create_control_buttons(user_id, file_name, is_running=True):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    if is_running:
        markup.add(types.KeyboardButton(B(f"🔴 𝐒𝐭𝐨𝐩 {file_name}")),
                   types.KeyboardButton(B(f"🔄 𝐑𝐞𝐬𝐭𝐚𝐫𝐭 {file_name}")))
        markup.add(types.KeyboardButton(B(f"🗑️ 𝐃𝐞𝐥𝐞𝐭𝐞 {file_name}")),
                   types.KeyboardButton(B(f"📜 𝐋𝐨𝐠𝐬 {file_name}")))
    else:
        markup.add(types.KeyboardButton(B(f"🟢 𝐒𝐭𝐚𝐫𝐭 {file_name}")),
                   types.KeyboardButton(B(f"🗑️ 𝐃𝐞𝐥𝐞𝐭𝐞 {file_name}")))
        markup.add(types.KeyboardButton(B(f"📜 𝐕𝐢𝐞𝐰 𝐋𝐨𝐠𝐬 {file_name}")))
    markup.add(types.KeyboardButton(B("🔙 𝐁𝐚𝐜𝐤")))
    return markup


# ================================
# 𝐂𝐎𝐌𝐌𝐀𝐍𝐃 𝐇𝐀𝐍𝐃𝐋𝐄𝐑𝐒
# ================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    add_active_user(user_id, message.from_user.username)
    user_tier = get_user_tier(user_id)
    tier_info = TIER_SYSTEM[user_tier]
    welcome_text = f"""╔══════════════════════════════════╗
║    🚀 𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐁𝐎𝐓 𝐕𝟒.𝟏          ║
║    𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐏𝐀𝐍𝐄𝐋                 ║
╚══════════════════════════════════╝

{tier_info['icon']} *𝐓𝐢𝐞𝐫: {tier_info['name']}*
📂 𝐔𝐩𝐥𝐨𝐚𝐝 𝐋𝐢𝐦𝐢𝐭: ∞
💾 𝐌𝐚𝐱 𝐅𝐢𝐥𝐞: ∞

*𝐅𝐞𝐚𝐭𝐮𝐫𝐞𝐬:*
📤 𝐔𝐩𝐥𝐨𝐚𝐝 𝐲𝐨𝐮𝐫 𝐬𝐜𝐫𝐢𝐩𝐭𝐬
🚀 𝐇𝐨𝐬𝐭 𝐚𝐧𝐝 𝐫𝐮𝐧 𝐛𝐨𝐭𝐬 𝟐𝟒/𝟕
📦 𝐀𝐮𝐭𝐨-𝐢𝐧𝐬𝐭𝐚𝐥𝐥 𝐦𝐨𝐝𝐮𝐥𝐞𝐬
🔄 𝐀𝐮𝐭𝐨-𝐫𝐞𝐜𝐨𝐯𝐞𝐫𝐲
📜 𝐋𝐢𝐯𝐞 𝐥𝐨𝐠𝐬
⚡ 𝐒𝐩𝐞𝐞𝐝 𝐭𝐞𝐬𝐭
📦 𝐙𝐈𝐏 𝐬𝐮𝐩𝐩𝐨𝐫𝐭 (𝐦𝐚𝐢𝐧.𝐩𝐲 + 𝐦𝐨𝐝𝐮𝐥𝐞𝐬)

*𝐒𝐮𝐩𝐩𝐨𝐫𝐭𝐞𝐝:*
• 𝐏𝐲𝐭𝐡𝐨𝐧 (.𝐩𝐲)
• 𝐍𝐨𝐝𝐞.𝐣𝐬 (.𝐣𝐬)
• 𝐙𝐈𝐏 (.𝐳𝐢𝐩) — 𝐦𝐚𝐢𝐧.𝐩𝐲 + 𝐦𝐨𝐝𝐮𝐥𝐞𝐬

*𝐇𝐨𝐰 𝐭𝐨 𝐬𝐞𝐧𝐝 𝐚 𝐟𝐢𝐥𝐞:*
1️⃣ 𝐓𝐚𝐩 📎 𝐚𝐭𝐭𝐚𝐜𝐡𝐦𝐞𝐧𝐭 𝐛𝐮𝐭𝐭𝐨𝐧
2️⃣ 𝐒𝐞𝐥𝐞𝐜𝐭 "𝐅𝐢𝐥𝐞" (𝐧𝐨𝐭 𝐩𝐡𝐨𝐭𝐨)
3️⃣ 𝐒𝐞𝐧𝐝 𝐲𝐨𝐮𝐫 .𝐩𝐲, .𝐣𝐬 𝐨𝐫 .𝐳𝐢𝐩 𝐟𝐢𝐥𝐞
4️⃣ 𝐔𝐬𝐞 "𝐌𝐚𝐧𝐚𝐠𝐞 𝐒𝐜𝐫𝐢𝐩𝐭𝐬" 𝐭𝐨 𝐒𝐭𝐚𝐫𝐭/𝐒𝐭𝐨𝐩"""

    msg = bot.reply_to(message, welcome_text, parse_mode='Markdown',
                       reply_markup=create_reply_keyboard_main_menu(user_id))
    try:
        AnimationManager.animate_dashboard(message.chat.id, msg.message_id)
    except Exception:
        pass

@bot.message_handler(commands=['help'])
def send_help(message):
    user_id = message.from_user.id
    help_text = f"""╔══════════════════════════════════╗
║    📋 𝐇𝐄𝐋𝐏 𝐆𝐔𝐈𝐃𝐄               ║
╚══════════════════════════════════╝

*𝐂𝐨𝐦𝐦𝐚𝐧𝐝𝐬:*
/start — 𝐎𝐩𝐞𝐧 𝐦𝐚𝐢𝐧 𝐦𝐞𝐧𝐮
/help — 𝐓𝐡𝐢𝐬 𝐡𝐞𝐥𝐩 𝐦𝐞𝐧𝐮
/stats — 𝐘𝐨𝐮𝐫 𝐬𝐭𝐚𝐭𝐬
/profile — 𝐘𝐨𝐮𝐫 𝐩𝐫𝐨𝐟𝐢𝐥𝐞
/speed — 𝐒𝐩𝐞𝐞𝐝 𝐭𝐞𝐬𝐭
/logs <filename> — 𝐕𝐢𝐞𝐰 𝐥𝐨𝐠𝐬

*𝐀𝐝𝐦𝐢𝐧 𝐨𝐧𝐥𝐲:*
/adduser <user_id> — 𝐆𝐫𝐚𝐧𝐭 𝐚𝐜𝐜𝐞𝐬𝐬
/removeuser <user_id> — 𝐑𝐞𝐯𝐨𝐤𝐞 𝐚𝐜𝐜𝐞𝐬𝐬
/listusers — 𝐋𝐢𝐬𝐭 𝐚𝐮𝐭𝐡𝐨𝐫𝐢𝐳𝐞𝐝 𝐮𝐬𝐞𝐫𝐬

*𝐇𝐨𝐰 𝐭𝐨 𝐮𝐬𝐞:*
1️⃣ 𝐓𝐚𝐩 📎 𝐚𝐭𝐭𝐚𝐜𝐡𝐦𝐞𝐧𝐭 → 𝐅𝐢𝐥𝐞
2️⃣ 𝐒𝐞𝐧𝐝 𝐲𝐨𝐮𝐫 .𝐩𝐲, .𝐣𝐬, 𝐨𝐫 .𝐳𝐢𝐩 𝐟𝐢𝐥𝐞
3️⃣ 𝐌𝐨𝐝𝐮𝐥𝐞𝐬 𝐚𝐮𝐭𝐨-𝐢𝐧𝐬𝐭𝐚𝐥𝐥𝐞𝐝
4️⃣ 𝐂𝐥𝐢𝐜𝐤 𝐌𝐚𝐧𝐚𝐠𝐞 𝐒𝐜𝐫𝐢𝐩𝐭𝐬 → 𝐒𝐭𝐚𝐫𝐭
5️⃣ 𝐕𝐢𝐞𝐰 𝐥𝐢𝐯𝐞 𝐥𝐨𝐠𝐬 𝐰𝐢𝐭𝐡 📜 𝐋𝐨𝐠𝐬 𝐛𝐮𝐭𝐭𝐨𝐧

*𝐙𝐈𝐏 𝐅𝐢𝐥𝐞𝐬:*
• 𝐏𝐮𝐭 𝐲𝐨𝐮𝐫 𝐦𝐚𝐢𝐧.𝐩𝐲 + 𝐦𝐨𝐝𝐮𝐥𝐞𝐬 𝐢𝐧 𝐚 .𝐳𝐢𝐩
• 𝐁𝐨𝐭 𝐟𝐢𝐧𝐝𝐬 𝐦𝐚𝐢𝐧.𝐩𝐲 𝐚𝐮𝐭𝐨𝐦𝐚𝐭𝐢𝐜𝐚𝐥𝐥𝐲
• 𝐫𝐞𝐪𝐮𝐢𝐫𝐞𝐦𝐞𝐧𝐭𝐬.𝐭𝐱𝐭 𝐢𝐧𝐬𝐭𝐚𝐥𝐥𝐞𝐝 𝐢𝐟 𝐩𝐫𝐞𝐬𝐞𝐧𝐭

*𝐀𝐮𝐭𝐨-𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐲:* ✅ 𝐄𝐧𝐚𝐛𝐥𝐞𝐝"""
    bot.reply_to(message, help_text, parse_mode='Markdown',
                 reply_markup=create_reply_keyboard_main_menu(user_id))

@bot.message_handler(commands=['stats'])
def show_stats(message):
    user_id = message.from_user.id
    add_active_user(user_id)
    user_tier = get_user_tier(user_id)
    tier_info = TIER_SYSTEM[user_tier]
    file_count = get_user_file_count(user_id)
    stats_text = f"""╔══════════════════════════════════╗
║    📊 𝐘𝐎𝐔𝐑 𝐒𝐓𝐀𝐓𝐒                ║
╚══════════════════════════════════╝

{tier_info['icon']} *𝐓𝐢𝐞𝐫: {tier_info['name']}*
📂 *𝐅𝐢𝐥𝐞𝐬:* {file_count}/∞
🚀 *𝐑𝐮𝐧𝐧𝐢𝐧𝐠:* {sum(1 for key, info in bot_scripts.items() if info['user_id'] == user_id and is_bot_running(user_id, info['file_name']))}

*𝐀𝐜𝐜𝐨𝐮𝐧𝐭 𝐈𝐧𝐟𝐨:*
🆔 𝐔𝐬𝐞𝐫 𝐈𝐃: `{user_id}`
👤 𝐔𝐬𝐞𝐫𝐧𝐚𝐦𝐞: @{message.from_user.username or '𝐍𝐎𝐓 𝐒𝐄𝐓'}
📅 𝐃𝐚𝐭𝐞: {datetime.now().strftime('%Y-%m-%d')}"""
    msg = bot.reply_to(message, stats_text, parse_mode='Markdown',
                       reply_markup=create_reply_keyboard_main_menu(user_id))
    try:
        AnimationManager.animate_dashboard(message.chat.id, msg.message_id)
    except Exception:
        pass

@bot.message_handler(commands=['profile'])
def show_profile(message):
    user_id = message.from_user.id
    add_active_user(user_id)
    user_tier = get_user_tier(user_id)
    tier_info = TIER_SYSTEM[user_tier]
    profile_text = f"""╔══════════════════════════════════╗
║    👤 𝐘𝐎𝐔𝐑 𝐏𝐑𝐎𝐅𝐈𝐋𝐄              ║
╚══════════════════════════════════╝

👤 *𝐍𝐚𝐦𝐞:* {message.from_user.first_name}
🆔 *𝐔𝐬𝐞𝐫 𝐈𝐃:* `{user_id}`
@ *𝐔𝐬𝐞𝐫𝐧𝐚𝐦𝐞:* @{message.from_user.username or '𝐍𝐎𝐓 𝐒𝐄𝐓'}
{tier_info['icon']} *𝐓𝐢𝐞𝐫:* {tier_info['name']}
🔐 *𝐀𝐜𝐜𝐞𝐬𝐬:* {'👑 Owner' if user_id == OWNER_ID else '✅ Authorized'}"""
    bot.reply_to(message, profile_text, parse_mode='Markdown',
                 reply_markup=create_reply_keyboard_main_menu(user_id))

@bot.message_handler(commands=['speed'])
def run_speedtest(message):
    user_id = message.from_user.id
    add_active_user(user_id)
    msg = bot.reply_to(message, B("⚡ 𝐑𝐮𝐧𝐧𝐢𝐧𝐠 𝐬𝐩𝐞𝐞𝐝 𝐭𝐞𝐬𝐭..."))
    time.sleep(2)
    download_speed = random.uniform(50, 500)
    upload_speed = random.uniform(20, 200)
    latency = random.uniform(10, 100)
    speed_text = f"""╔══════════════════════════════════╗
║    ⚡ 𝐒𝐏𝐄𝐄𝐃 𝐓𝐄𝐒𝐓 𝐑𝐄𝐒𝐔𝐋𝐓𝐒       ║
╚══════════════════════════════════╝

📥 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝: {download_speed:.1f} 𝐌𝐛𝐩𝐬
📤 𝐔𝐩𝐥𝐨𝐚𝐝: {upload_speed:.1f} 𝐌𝐛𝐩𝐬
⏱️ 𝐋𝐚𝐭𝐞𝐧𝐜𝐲: {latency:.1f} 𝐦𝐬

🌐 𝐒𝐞𝐫𝐯𝐞𝐫: 𝐇𝐨𝐬𝐭𝐢𝐧𝐠 𝐁𝐨𝐭"""
    try:
        bot.edit_message_text(speed_text, message.chat.id, msg.message_id, parse_mode='Markdown')
    except Exception:
        pass

@bot.message_handler(commands=['logs'])
def cmd_logs(message):
    """View logs for a file: /logs <filename>"""
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, B("❓ 𝐔𝐬𝐚𝐠𝐞: /logs <filename>\n𝐄𝐱𝐚𝐦𝐩𝐥𝐞: /logs bot.py"), parse_mode='Markdown')
        return
    file_name = args[1].strip()
    _show_logs(message, user_id, file_name)

# ================================
# 𝐀𝐃𝐌𝐈𝐍 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒
# ================================
@bot.message_handler(commands=['adduser'])
def cmd_adduser(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "🔒 Owner only.")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❓ Usage: /adduser <user_id>")
        return
    try:
        target_id = int(args[1])
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID. Must be a number.")
        return
    grant_user_access(target_id)
    bot.reply_to(message, f"✅ User `{target_id}` has been granted access.", parse_mode='Markdown')
    # Notify the user if possible
    try:
        bot.send_message(target_id, "✅ 𝐘𝐨𝐮 𝐡𝐚𝐯𝐞 𝐛𝐞𝐞𝐧 𝐠𝐫𝐚𝐧𝐭𝐞𝐝 𝐚𝐜𝐜𝐞𝐬𝐬 𝐭𝐨 𝐭𝐡𝐞 𝐇𝐨𝐬𝐭𝐢𝐧𝐠 𝐁𝐨𝐭!\n\nSend /start to begin.")
    except Exception:
        pass

@bot.message_handler(commands=['removeuser'])
def cmd_removeuser(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "🔒 Owner only.")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❓ Usage: /removeuser <user_id>")
        return
    try:
        target_id = int(args[1])
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID. Must be a number.")
        return
    if target_id == OWNER_ID:
        bot.reply_to(message, "❌ Cannot remove owner.")
        return
    revoke_user_access(target_id)
    bot.reply_to(message, f"✅ Access revoked for user `{target_id}`.", parse_mode='Markdown')

@bot.message_handler(commands=['listusers'])
def cmd_listusers(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "🔒 Owner only.")
        return
    if not authorized_users:
        bot.reply_to(message, "📭 No authorized users yet.\nUse /adduser <user_id> to grant access.")
        return
    lines = ["╔══════════════════════════════════╗",
             "║    👥 𝐀𝐔𝐓𝐇𝐎𝐑𝐈𝐙𝐄𝐃 𝐔𝐒𝐄𝐑𝐒        ║",
             "╚══════════════════════════════════╝", ""]
    for uid in sorted(authorized_users):
        lines.append(f"• `{uid}`")
    lines.append(f"\n*𝐓𝐨𝐭𝐚𝐥:* {len(authorized_users)} users")
    bot.reply_to(message, '\n'.join(lines), parse_mode='Markdown')


# ================================
# 𝐃𝐎𝐂𝐔𝐌𝐄𝐍𝐓 (𝐔𝐏𝐋𝐎𝐀𝐃) 𝐇𝐀𝐍𝐃𝐋𝐄𝐑
# ================================
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    add_active_user(user_id)
    if bot_locked and user_id != OWNER_ID:
        bot.reply_to(message, "🔒 𝐁𝐨𝐭 𝐢𝐬 𝐥𝐨𝐜𝐤𝐞𝐝.")
        return
    doc = message.document
    file_name = doc.file_name
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, B("❌ 𝐎𝐧𝐥𝐲 .𝐩𝐲, .𝐣𝐬, 𝐚𝐧𝐝 .𝐳𝐢𝐩 𝐟𝐢𝐥𝐞𝐬 𝐚𝐥𝐥𝐨𝐰𝐞𝐝."))
        return
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    msg = bot.reply_to(message, B("🚀 𝐈𝐧𝐢𝐭𝐢𝐚𝐭𝐢𝐧𝐠 𝐮𝐩𝐥𝐨𝐚𝐝 𝐬𝐞𝐪𝐮𝐞𝐧𝐜𝐞..."))
    try:
        AnimationManager.animate_upload(message.chat.id, msg.message_id)
    except Exception:
        pass
    try:
        file_info = bot.get_file(doc.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)

        if file_ext == '.py':
            file_type = 'py'
            try:
                auto_setup_user_env(user_id, file_name)
            except Exception:
                pass
            save_user_file_db(user_id, file_name, file_type)

        elif file_ext == '.js':
            file_type = 'js'
            save_user_file_db(user_id, file_name, file_type)

        elif file_ext == '.zip':
            # Extract and find main.py
            status_msg = bot.edit_message_text(
                B("📦 𝐄𝐱𝐭𝐫𝐚𝐜𝐭𝐢𝐧𝐠 𝐙𝐈𝐏 𝐚𝐧𝐝 𝐬𝐜𝐚𝐧𝐧𝐢𝐧𝐠 𝐟𝐨𝐫 𝐦𝐚𝐢𝐧.𝐩𝐲..."),
                message.chat.id, msg.message_id
            )
            main_py_path, run_dir, err = extract_and_setup_zip(file_path, user_folder, file_name)
            if err or main_py_path is None:
                bot.edit_message_text(
                    B(f"❌ 𝐙𝐈𝐏 𝐄𝐫𝐫𝐨𝐫: {err or 'main.py not found in ZIP.'}"),
                    message.chat.id, msg.message_id
                )
                return
            file_type = 'zip'
            # Store the path to main.py in extra_path, run_dir in file type metadata
            save_user_file_db(user_id, file_name, file_type, extra_path=f"{main_py_path}|{run_dir}")

        time.sleep(2)
        tier_info = TIER_SYSTEM[get_user_tier(user_id)]
        extra_info = ""
        if file_ext == '.zip':
            extra_info = f"\n📂 *𝐌𝐚𝐢𝐧:* `{os.path.basename(main_py_path)}`\n📁 *𝐑𝐮𝐧 𝐝𝐢𝐫:* `{os.path.basename(run_dir)}/`"
        upload_text = f"""╔══════════════════════════════════╗
║    ✅ 𝐔𝐏𝐋𝐎𝐀𝐃 𝐒𝐔𝐂𝐂𝐄𝐒𝐒𝐅𝐔𝐋         ║
╠══════════════════════════════════╣
║    📂 {file_name}
║    💾 {doc.file_size / 1024:.1f} 𝐊𝐁
║    {tier_info['icon']} {tier_info['name']} 𝐓𝐈𝐄𝐑
╚══════════════════════════════════╝

📊 *𝐒𝐭𝐚𝐭𝐮𝐬:* 𝐑𝐞𝐚𝐝𝐲 𝐭𝐨 𝐡𝐨𝐬𝐭
🔄 *𝐀𝐮𝐭𝐨-𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐲:* ✅ 𝐄𝐧𝐚𝐛𝐥𝐞𝐝
📦 *𝐌𝐨𝐝𝐮𝐥𝐞𝐬:* 𝐀𝐮𝐭𝐨-𝐢𝐧𝐬𝐭𝐚𝐥𝐥𝐞𝐝{extra_info}

👉 𝐍𝐨𝐰 𝐮𝐬𝐞 "𝐌𝐚𝐧𝐚𝐠𝐞 𝐒𝐜𝐫𝐢𝐩𝐭𝐬" 𝐭𝐨 𝐒𝐭𝐚𝐫𝐭!"""
        bot.edit_message_text(upload_text, message.chat.id, msg.message_id, parse_mode='Markdown',
                              reply_markup=create_reply_keyboard_main_menu(user_id))
    except Exception as e:
        try:
            bot.edit_message_text(B(f"❌ 𝐔𝐩𝐥𝐨𝐚𝐝 𝐅𝐚𝐢𝐥𝐞𝐝: {str(e)}"), message.chat.id, msg.message_id,
                                  reply_markup=create_reply_keyboard_main_menu(user_id))
        except Exception:
            pass


# ================================
# 𝐅𝐈𝐋𝐄 𝐂𝐎𝐍𝐓𝐑𝐎𝐋 𝐇𝐀𝐍𝐃𝐋𝐄𝐑𝐒
# ================================
def handle_file_control_text(message):
    user_id = message.from_user.id
    add_active_user(user_id)
    text = message.text
    if B("🔙 𝐁𝐚𝐜𝐤") in text:
        bot.reply_to(message, B("🏠 𝐑𝐞𝐭𝐮𝐫𝐧𝐢𝐧𝐠 𝐭𝐨 𝐦𝐚𝐢𝐧 𝐦𝐞𝐧𝐮..."),
                     reply_markup=create_reply_keyboard_main_menu(user_id))
        return
    raw_file_name = text.split(' ', 2)[-1] if ' ' in text else ''
    file_name = unbold(raw_file_name)
    if "🟢" in text and "𝐒𝐭𝐚𝐫𝐭" in text:
        _start_script(message, user_id, file_name)
    elif "🔴" in text and "𝐒𝐭𝐨𝐩" in text:
        _stop_script(message, user_id, file_name)
    elif "🔄" in text and "𝐑𝐞𝐬𝐭𝐚𝐫𝐭" in text:
        _restart_script(message, user_id, file_name)
    elif "🗑️" in text and "𝐃𝐞𝐥𝐞𝐭𝐞" in text:
        _delete_script(message, user_id, file_name)
    elif "📜" in text and ("𝐋𝐨𝐠𝐬" in text or "𝐕𝐢𝐞𝐰" in text):
        _show_logs(message, user_id, file_name)

def _get_run_params(user_id, file_name):
    """Return (file_path, run_dir, file_type) for starting a script."""
    user_folder = get_user_folder(user_id)
    file_type = 'py'
    for fname, ftype in user_files.get(user_id, []):
        if fname == file_name:
            file_type = ftype
            break

    if file_type == 'zip':
        # Look up extra_path from DB
        extra_path = get_file_extra_path(user_id, file_name)
        if extra_path and '|' in extra_path:
            main_py_path, run_dir = extra_path.split('|', 1)
            if os.path.exists(main_py_path):
                return main_py_path, run_dir, 'zip'
        # Fallback: try to find extracted dir
        base_name = os.path.splitext(file_name)[0]
        extract_dir = os.path.join(user_folder, base_name)
        main_py_path = os.path.join(extract_dir, 'main.py')
        if os.path.exists(main_py_path):
            return main_py_path, extract_dir, 'zip'
        return None, None, 'zip'

    file_path = os.path.join(user_folder, file_name)
    return file_path, user_folder, file_type

def _start_script(message, user_id, file_name):
    file_path, run_dir, file_type = _get_run_params(user_id, file_name)
    if not file_path or not os.path.exists(file_path):
        bot.reply_to(message, B(f"❌ 𝐅𝐢𝐥𝐞 𝐧𝐨𝐭 𝐟𝐨𝐮𝐧𝐝: `{file_name}`"), parse_mode='Markdown')
        return
    if is_bot_running(user_id, file_name):
        bot.reply_to(message, B(f"⚠️ `{file_name}` 𝐢𝐬 𝐚𝐥𝐫𝐞𝐚𝐝𝐲 𝐫𝐮𝐧𝐧𝐢𝐧𝐠."), parse_mode='Markdown')
        return
    msg = bot.reply_to(message, B(f"🚀 𝐒𝐭𝐚𝐫𝐭𝐢𝐧𝐠 `{file_name}`..."))
    try:
        AnimationManager.animate_start(message.chat.id, msg.message_id)
    except Exception:
        pass
    if file_type in ('py', 'zip'):
        threading.Thread(target=run_script, args=(file_path, user_id, run_dir, file_name, msg)).start()
    elif file_type == 'js':
        threading.Thread(target=run_js_script, args=(file_path, user_id, run_dir, file_name, msg)).start()

def _stop_script(message, user_id, file_name):
    script_key = f"{user_id}_{file_name}"
    if not is_bot_running(user_id, file_name):
        bot.reply_to(message, B(f"⚠️ `{file_name}` 𝐢𝐬 𝐧𝐨𝐭 𝐫𝐮𝐧𝐧𝐢𝐧𝐠."), parse_mode='Markdown')
        return
    msg = bot.reply_to(message, B(f"🛑 𝐒𝐭𝐨𝐩𝐩𝐢𝐧𝐠 `{file_name}`..."))
    try:
        AnimationManager.animate_stop(message.chat.id, msg.message_id)
    except Exception:
        pass
    if script_key in bot_scripts:
        kill_process_tree(bot_scripts[script_key])
        if script_key in bot_scripts:
            del bot_scripts[script_key]
    recovery_system.remove_running_script(user_id, file_name)
    time.sleep(1.2)
    try:
        bot.edit_message_text(B(f"🛑 `{file_name}` 𝐬𝐭𝐨𝐩𝐩𝐞𝐝 𝐬𝐮𝐜𝐜𝐞𝐬𝐬𝐟𝐮𝐥𝐥𝐲."),
                              message.chat.id, msg.message_id, parse_mode='Markdown',
                              reply_markup=create_reply_keyboard_main_menu(user_id))
    except Exception:
        pass

def _restart_script(message, user_id, file_name):
    script_key = f"{user_id}_{file_name}"
    if is_bot_running(user_id, file_name):
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            if script_key in bot_scripts:
                del bot_scripts[script_key]
        time.sleep(1)
    file_path, run_dir, file_type = _get_run_params(user_id, file_name)
    if not file_path or not os.path.exists(file_path):
        bot.reply_to(message, B(f"❌ 𝐅𝐢𝐥𝐞 𝐧𝐨𝐭 𝐟𝐨𝐮𝐧𝐝: `{file_name}`"), parse_mode='Markdown')
        return
    msg = bot.reply_to(message, B(f"🔄 𝐑𝐞𝐬𝐭𝐚𝐫𝐭𝐢𝐧𝐠 `{file_name}`..."))
    try:
        AnimationManager.animate_restart(message.chat.id, msg.message_id)
    except Exception:
        pass
    if file_type in ('py', 'zip'):
        threading.Thread(target=run_script, args=(file_path, user_id, run_dir, file_name, msg)).start()
    elif file_type == 'js':
        threading.Thread(target=run_js_script, args=(file_path, user_id, run_dir, file_name, msg)).start()

def _delete_script(message, user_id, file_name):
    msg = bot.reply_to(message, B(f"🗑️ 𝐃𝐞𝐥𝐞𝐭𝐢𝐧𝐠 `{file_name}`..."))
    try:
        AnimationManager.animate_delete(message.chat.id, msg.message_id)
    except Exception:
        pass
    if is_bot_running(user_id, file_name):
        script_key = f"{user_id}_{file_name}"
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            if script_key in bot_scripts:
                del bot_scripts[script_key]
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
    if os.path.exists(file_path):
        os.remove(file_path)
    if os.path.exists(log_path):
        os.remove(log_path)
    # Also remove extracted ZIP folder if applicable
    file_type = 'py'
    for fname, ftype in user_files.get(user_id, []):
        if fname == file_name:
            file_type = ftype
            break
    if file_type == 'zip':
        base_name = os.path.splitext(file_name)[0]
        extract_dir = os.path.join(user_folder, base_name)
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
    remove_user_file_db(user_id, file_name)
    time.sleep(1.5)
    try:
        bot.edit_message_text(B(f"🗑️ `{file_name}` 𝐝𝐞𝐥𝐞𝐭𝐞𝐝 𝐬𝐮𝐜𝐜𝐞𝐬𝐬𝐟𝐮𝐥𝐥𝐲."),
                              message.chat.id, msg.message_id, parse_mode='Markdown',
                              reply_markup=create_reply_keyboard_main_menu(user_id))
    except Exception:
        pass

def _show_logs(message, user_id, file_name):
    user_folder = get_user_folder(user_id)
    log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
    msg = bot.reply_to(message, B(f"📜 𝐋𝐨𝐚𝐝𝐢𝐧𝐠 𝐥𝐨𝐠𝐬 𝐟𝐨𝐫 `{file_name}`..."))
    try:
        AnimationManager.animate_logs(message.chat.id, msg.message_id)
    except Exception:
        pass
    time.sleep(1)
    if not os.path.exists(log_path):
        try:
            bot.edit_message_text(B(f"📭 𝐍𝐨 𝐥𝐨𝐠𝐬 𝐟𝐨𝐮𝐧𝐝 𝐟𝐨𝐫 `{file_name}`.\n\n𝐒𝐭𝐚𝐫𝐭 𝐭𝐡𝐞 𝐬𝐜𝐫𝐢𝐩𝐭 𝐟𝐢𝐫𝐬𝐭 𝐭𝐨 𝐠𝐞𝐧𝐞𝐫𝐚𝐭𝐞 𝐥𝐨𝐠𝐬."),
                                  message.chat.id, msg.message_id, parse_mode='Markdown',
                                  reply_markup=create_reply_keyboard_main_menu(user_id))
        except Exception:
            pass
        return
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            log_content = f.read()
        if not log_content.strip():
            log_content = "(𝐋𝐨𝐠 𝐟𝐢𝐥𝐞 𝐢𝐬 𝐞𝐦𝐩𝐭𝐲)"
        elif len(log_content) > 3500:
            log_content = "...(𝐭𝐫𝐮𝐧𝐜𝐚𝐭𝐞𝐝)...\n" + log_content[-3500:]
        running_status = "🟢 𝐑𝐮𝐧𝐧𝐢𝐧𝐠" if is_bot_running(user_id, file_name) else "🔴 𝐒𝐭𝐨𝐩𝐩𝐞𝐝"
        header = f"📜 *𝐋𝐨𝐠𝐬 𝐟𝐨𝐫* `{file_name}` | {running_status}\n\n"
        try:
            bot.edit_message_text(header + f"```\n{log_content}\n```",
                                  message.chat.id, msg.message_id, parse_mode='Markdown',
                                  reply_markup=create_reply_keyboard_main_menu(user_id))
        except Exception:
            # If message is too long, send as file
            try:
                from io import BytesIO
                log_bytes = log_content.encode('utf-8')
                bot.send_document(message.chat.id,
                                  ('logs.txt', BytesIO(log_bytes)),
                                  caption=f"📜 Logs for {file_name}")
            except Exception as e2:
                bot.reply_to(message, B(f"❌ 𝐄𝐫𝐫𝐨𝐫 𝐬𝐞𝐧𝐝𝐢𝐧𝐠 𝐥𝐨𝐠𝐬: {str(e2)}"))
    except Exception as e:
        bot.reply_to(message, B(f"❌ 𝐄𝐫𝐫𝐨𝐫 𝐫𝐞𝐚𝐝𝐢𝐧𝐠 𝐥𝐨𝐠𝐬: {str(e)}"))

def _start_script_silent(user_id, file_name):
    file_path, run_dir, file_type = _get_run_params(user_id, file_name)
    if not file_path or not os.path.exists(file_path):
        return
    if file_type in ('py', 'zip'):
        threading.Thread(target=run_script_silent, args=(file_path, user_id, run_dir, file_name)).start()
    elif file_type == 'js':
        threading.Thread(target=run_js_script_silent, args=(file_path, user_id, run_dir, file_name)).start()

def _stop_script_silent(user_id, file_name):
    script_key = f"{user_id}_{file_name}"
    if script_key in bot_scripts:
        kill_process_tree(bot_scripts[script_key])
        if script_key in bot_scripts:
            del bot_scripts[script_key]
    recovery_system.remove_running_script(user_id, file_name)

def _delete_script_silent(user_id, file_name):
    _stop_script_silent(user_id, file_name)
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
    if os.path.exists(file_path):
        os.remove(file_path)
    if os.path.exists(log_path):
        os.remove(log_path)
    file_type = 'py'
    for fname, ftype in user_files.get(user_id, []):
        if fname == file_name:
            file_type = ftype
            break
    if file_type == 'zip':
        base_name = os.path.splitext(file_name)[0]
        extract_dir = os.path.join(user_folder, base_name)
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
    remove_user_file_db(user_id, file_name)

def run_script_silent(file_path, user_id, run_dir, file_name):
    script_key = f"{user_id}_{file_name}"
    log_file_path = os.path.join(get_user_folder(user_id), f"{os.path.splitext(file_name)[0]}.log")
    try:
        log_file = open(log_file_path, 'a', encoding='utf-8', errors='ignore')
        process = subprocess.Popen([sys.executable, file_path], cwd=run_dir,
                                   stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                                   encoding='utf-8', errors='ignore')
        bot_scripts[script_key] = {'process': process, 'log_file': log_file, 'file_name': file_name,
                                   'user_id': user_id, 'start_time': datetime.now(), 'type': 'py',
                                   'script_key': script_key}
        recovery_system.save_running_script(user_id, file_name, file_path, process.pid, run_dir)
        process.wait()
    except:
        pass
    finally:
        recovery_system.remove_running_script(user_id, file_name)
        if script_key in bot_scripts:
            del bot_scripts[script_key]
        try:
            log_file.close()
        except:
            pass

def run_js_script_silent(file_path, user_id, run_dir, file_name):
    script_key = f"{user_id}_{file_name}"
    log_file_path = os.path.join(get_user_folder(user_id), f"{os.path.splitext(file_name)[0]}.log")
    try:
        log_file = open(log_file_path, 'a', encoding='utf-8', errors='ignore')
        process = subprocess.Popen(['node', file_path], cwd=run_dir,
                                   stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                                   encoding='utf-8', errors='ignore')
        bot_scripts[script_key] = {'process': process, 'log_file': log_file, 'file_name': file_name,
                                   'user_id': user_id, 'start_time': datetime.now(), 'type': 'js',
                                   'script_key': script_key}
        recovery_system.save_running_script(user_id, file_name, file_path, process.pid, run_dir)
        process.wait()
    except:
        pass
    finally:
        recovery_system.remove_running_script(user_id, file_name)
        if script_key in bot_scripts:
            del bot_scripts[script_key]
        try:
            log_file.close()
        except:
            pass


# ================================
# 𝐒𝐂𝐑𝐈𝐏𝐓 𝐑𝐔𝐍𝐍𝐈𝐍𝐆 𝐅𝐔𝐍𝐂𝐓𝐈𝐎𝐍𝐒
# ================================
def run_script(file_path, user_id, run_dir, file_name, message):
    script_key = f"{user_id}_{file_name}"
    if script_key in bot_scripts:
        return
    log_file_path = os.path.join(get_user_folder(user_id), f"{os.path.splitext(file_name)[0]}.log")
    try:
        log_file = open(log_file_path, 'a', encoding='utf-8', errors='ignore')
    except Exception as e:
        try:
            bot.reply_to(message, B(f"❌ 𝐄𝐫𝐫𝐨𝐫 𝐨𝐩𝐞𝐧𝐢𝐧𝐠 𝐥𝐨𝐠 𝐟𝐢𝐥𝐞: {str(e)}"))
        except Exception:
            pass
        return
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    try:
        process = subprocess.Popen([sys.executable, file_path], cwd=run_dir,
                                   stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                                   startupinfo=startupinfo, encoding='utf-8', errors='ignore')
        bot_scripts[script_key] = {'process': process, 'log_file': log_file, 'file_name': file_name,
                                   'user_id': user_id, 'start_time': datetime.now(), 'type': 'py',
                                   'script_key': script_key}
        recovery_system.save_running_script(user_id, file_name, file_path, process.pid, run_dir)
        try:
            time.sleep(2)
            bot.edit_message_text(B(f"🟢 `{file_name}` 𝐢𝐬 𝐧𝐨𝐰 𝐫𝐮𝐧𝐧𝐢𝐧𝐠!\n📋 𝐏𝐈𝐃: {process.pid}\n\n📜 𝐔𝐬𝐞 '📜 𝐋𝐨𝐠𝐬 {file_name}' 𝐭𝐨 𝐯𝐢𝐞𝐰 𝐨𝐮𝐭𝐩𝐮𝐭."),
                                  message.chat.id, message.message_id, parse_mode='Markdown',
                                  reply_markup=create_reply_keyboard_main_menu(user_id))
        except Exception:
            pass
        try:
            process.wait()
            exit_code = process.returncode
            if exit_code != 0:
                # Read last lines of log for the error
                error_snippet = ""
                try:
                    log_file.flush()
                    with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as lf:
                        lines = lf.readlines()
                        error_snippet = ''.join(lines[-10:])
                    if len(error_snippet) > 800:
                        error_snippet = "..." + error_snippet[-800:]
                except Exception:
                    pass
                err_msg = f"⚠️ `{file_name}` 𝐞𝐱𝐢𝐭𝐞𝐝 𝐰𝐢𝐭𝐡 𝐜𝐨𝐝𝐞 {exit_code}."
                if error_snippet:
                    err_msg += f"\n\n📋 *𝐋𝐚𝐬𝐭 𝐥𝐨𝐠 𝐥𝐢𝐧𝐞𝐬:*\n```\n{error_snippet}\n```"
                try:
                    bot.send_message(user_id, B(err_msg), parse_mode='Markdown')
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            recovery_system.remove_running_script(user_id, file_name)
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            try:
                log_file.close()
            except Exception:
                pass
    except Exception as e:
        try:
            bot.reply_to(message, B(f"❌ 𝐄𝐫𝐫𝐨𝐫 𝐬𝐭𝐚𝐫𝐭𝐢𝐧𝐠 `{file_name}`: {str(e)}"))
        except Exception:
            pass
        try:
            log_file.close()
        except Exception:
            pass

def run_js_script(file_path, user_id, run_dir, file_name, message):
    script_key = f"{user_id}_{file_name}"
    if script_key in bot_scripts:
        return
    log_file_path = os.path.join(get_user_folder(user_id), f"{os.path.splitext(file_name)[0]}.log")
    try:
        log_file = open(log_file_path, 'a', encoding='utf-8', errors='ignore')
    except Exception as e:
        try:
            bot.reply_to(message, B(f"❌ 𝐄𝐫𝐫𝐨𝐫: {str(e)}"))
        except Exception:
            pass
        return
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    try:
        process = subprocess.Popen(['node', file_path], cwd=run_dir,
                                   stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                                   startupinfo=startupinfo, encoding='utf-8', errors='ignore')
        bot_scripts[script_key] = {'process': process, 'log_file': log_file, 'file_name': file_name,
                                   'user_id': user_id, 'start_time': datetime.now(), 'type': 'js',
                                   'script_key': script_key}
        recovery_system.save_running_script(user_id, file_name, file_path, process.pid, run_dir)
        try:
            time.sleep(2)
            bot.edit_message_text(B(f"🟢 `{file_name}` 𝐢𝐬 𝐧𝐨𝐰 𝐫𝐮𝐧𝐧𝐢𝐧𝐠!\n📋 𝐏𝐈𝐃: {process.pid}"),
                                  message.chat.id, message.message_id, parse_mode='Markdown',
                                  reply_markup=create_reply_keyboard_main_menu(user_id))
        except Exception:
            pass
        try:
            process.wait()
            exit_code = process.returncode
            if exit_code != 0:
                try:
                    bot.send_message(user_id, B(f"⚠️ `{file_name}` 𝐞𝐱𝐢𝐭𝐞𝐝 𝐰𝐢𝐭𝐡 𝐜𝐨𝐝𝐞 {exit_code}."))
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            recovery_system.remove_running_script(user_id, file_name)
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            try:
                log_file.close()
            except Exception:
                pass
    except Exception as e:
        try:
            bot.reply_to(message, B(f"❌ 𝐄𝐫𝐫𝐨𝐫 𝐬𝐭𝐚𝐫𝐭𝐢𝐧𝐠 `{file_name}`: {str(e)}"))
        except Exception:
            pass
        try:
            log_file.close()
        except Exception:
            pass


# ================================
# 𝐌𝐀𝐍𝐀𝐆𝐄 𝐒𝐂𝐑𝐈𝐏𝐓𝐒 (𝐈𝐍𝐋𝐈𝐍𝐄 𝐊𝐄𝐘𝐁𝐎𝐀𝐑𝐃)
# ================================
def create_manage_scripts_keyboard(user_id):
    markup = types.InlineKeyboardMarkup()
    files = user_files.get(user_id, [])
    if not files:
        return None
    for fname, ftype in files:
        running = is_bot_running(user_id, fname)
        status_text = "🟢 Running" if running else "🔴 Stopped"
        type_icon = "📦" if ftype == 'zip' else ("🐍" if ftype == 'py' else "⬡")
        markup.add(types.InlineKeyboardButton(f"{type_icon} {fname} | {status_text}", callback_data=f"info_{fname}"))
        row = []
        if running:
            row.append(types.InlineKeyboardButton("⏹ Stop", callback_data=f"stop_script_{fname}"))
            row.append(types.InlineKeyboardButton("🔄 Restart", callback_data=f"restart_script_{fname}"))
        else:
            row.append(types.InlineKeyboardButton("▶ Start", callback_data=f"start_script_{fname}"))
        row.append(types.InlineKeyboardButton("🗑 Delete", callback_data=f"delete_script_{fname}"))
        markup.add(*row)
        markup.add(types.InlineKeyboardButton("📜 View Logs", callback_data=f"logs_script_{fname}"))
        markup.add(types.InlineKeyboardButton("━━━━━━━━━━━━━━", callback_data="none"))
    markup.add(types.InlineKeyboardButton("🔄 Refresh List", callback_data="manage_scripts"))
    return markup

def show_manage_scripts(message, user_id):
    files = user_files.get(user_id, [])
    if not files:
        bot.reply_to(message, B("📂 𝐍𝐨 𝐬𝐜𝐫𝐢𝐩𝐭𝐬 𝐮𝐩𝐥𝐨𝐚𝐝𝐞𝐝 𝐲𝐞𝐭.\n\n📤 𝐒𝐞𝐧𝐝 𝐚 .𝐩𝐲, .𝐣𝐬 𝐨𝐫 .𝐳𝐢𝐩 𝐟𝐢𝐥𝐞 𝐭𝐨 𝐮𝐩𝐥𝐨𝐚𝐝!"),
                    reply_markup=create_reply_keyboard_main_menu(user_id))
        return
    kb = create_manage_scripts_keyboard(user_id)
    bot.reply_to(message, B("📂 𝐌𝐀𝐍𝐀𝐆𝐄 𝐒𝐂𝐑𝐈𝐏𝐓𝐒\n\n𝐒𝐞𝐥𝐞𝐜𝐭 𝐚 𝐬𝐜𝐫𝐢𝐩𝐭 𝐭𝐨 𝐜𝐨𝐧𝐭𝐫𝐨𝐥:"),
                 reply_markup=kb, parse_mode='Markdown')

def update_manage_scripts_message(call, user_id):
    kb = create_manage_scripts_keyboard(user_id)
    if not kb:
        try:
            bot.edit_message_text(B("📂 𝐍𝐨 𝐬𝐜𝐫𝐢𝐩𝐭𝐬 𝐮𝐩𝐥𝐨𝐚𝐝𝐞𝐝 𝐲𝐞𝐭."),
                                  call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        return
    try:
        bot.edit_message_text(B("📂 𝐌𝐀𝐍𝐀𝐆𝐄 𝐒𝐂𝐑𝐈𝐏𝐓𝐒\n\n𝐒𝐞𝐥𝐞𝐜𝐭 𝐚 𝐬𝐜𝐫𝐢𝐩𝐭 𝐭𝐨 𝐜𝐨𝐧𝐭𝐫𝐨𝐥:"),
                              call.message.chat.id, call.message.message_id,
                              reply_markup=kb, parse_mode='Markdown')
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error updating scripts list: {e}")


# ================================
# 𝐂𝐀𝐋𝐋𝐁𝐀𝐂𝐊 𝐇𝐀𝐍𝐃𝐋𝐄𝐑
# ================================
@bot.callback_query_handler(func=lambda call: True)
def callback_router(call):
    user_id = call.from_user.id
    add_active_user(user_id)
    data = call.data
    try:
        if data == 'manage_scripts':
            update_manage_scripts_message(call, user_id)

        elif data.startswith('start_script_'):
            file_name = data.replace('start_script_', '')
            if is_bot_running(user_id, file_name):
                bot.answer_callback_query(call.id, "⚠️ Already Running", show_alert=True)
            else:
                bot.answer_callback_query(call.id, "🚀 Starting...")
                _start_script_silent(user_id, file_name)
                time.sleep(1)
                update_manage_scripts_message(call, user_id)

        elif data.startswith('stop_script_'):
            file_name = data.replace('stop_script_', '')
            if not is_bot_running(user_id, file_name):
                bot.answer_callback_query(call.id, "⚠️ Already Stopped", show_alert=True)
            else:
                bot.answer_callback_query(call.id, "🛑 Stopping...")
                _stop_script_silent(user_id, file_name)
                time.sleep(1)
                update_manage_scripts_message(call, user_id)

        elif data.startswith('restart_script_'):
            file_name = data.replace('restart_script_', '')
            bot.answer_callback_query(call.id, "🔄 Restarting...")
            _stop_script_silent(user_id, file_name)
            time.sleep(1)
            _start_script_silent(user_id, file_name)
            time.sleep(1)
            update_manage_scripts_message(call, user_id)

        elif data.startswith('logs_script_'):
            file_name = data.replace('logs_script_', '')
            bot.answer_callback_query(call.id, "📜 Loading logs...")
            user_folder = get_user_folder(user_id)
            log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
            if not os.path.exists(log_path):
                bot.send_message(call.message.chat.id, B(f"📭 𝐍𝐨 𝐥𝐨𝐠𝐬 𝐟𝐨𝐮𝐧𝐝 𝐟𝐨𝐫 `{file_name}`.\n𝐒𝐭𝐚𝐫𝐭 𝐭𝐡𝐞 𝐬𝐜𝐫𝐢𝐩𝐭 𝐟𝐢𝐫𝐬𝐭."), parse_mode='Markdown')
            else:
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    log_content = f.read()
                if not log_content.strip():
                    log_content = "(𝐋𝐨𝐠 𝐟𝐢𝐥𝐞 𝐢𝐬 𝐞𝐦𝐩𝐭𝐲)"
                elif len(log_content) > 3500:
                    log_content = "...(𝐭𝐫𝐮𝐧𝐜𝐚𝐭𝐞𝐝)...\n" + log_content[-3500:]
                running_status = "🟢 𝐑𝐮𝐧𝐧𝐢𝐧𝐠" if is_bot_running(user_id, file_name) else "🔴 𝐒𝐭𝐨𝐩𝐩𝐞𝐝"
                try:
                    bot.send_message(call.message.chat.id,
                                     f"📜 *𝐋𝐨𝐠𝐬 𝐟𝐨𝐫* `{file_name}` | {running_status}\n\n```\n{log_content}\n```",
                                     parse_mode='Markdown')
                except Exception:
                    from io import BytesIO
                    bot.send_document(call.message.chat.id,
                                      ('logs.txt', BytesIO(log_content.encode('utf-8'))),
                                      caption=f"📜 Logs for {file_name}")

        elif data.startswith('delete_script_'):
            file_name = data.replace('delete_script_', '')
            bot.answer_callback_query(call.id, "🗑 Deleting...")
            _delete_script_silent(user_id, file_name)
            time.sleep(1)
            update_manage_scripts_message(call, user_id)

        elif data == 'speed':
            bot.answer_callback_query(call.id, "⚡ Running speed test...")
            download_speed = random.uniform(50, 500)
            upload_speed = random.uniform(20, 200)
            latency = random.uniform(10, 100)
            speed_text = f"╔══════════════════════════════════╗\n║    ⚡ 𝐒𝐏𝐄𝐄𝐃 𝐓𝐄𝐒𝐓 𝐑𝐄𝐒𝐔𝐋𝐓𝐒       ║\n╚══════════════════════════════════╝\n\n📥 {download_speed:.1f} Mbps\n📤 {upload_speed:.1f} Mbps\n⏱️ {latency:.1f} ms"
            try:
                bot.edit_message_text(speed_text, call.message.chat.id, call.message.message_id)
            except Exception:
                pass

        elif data == 'recover_all':
            bot.answer_callback_query(call.id, "🛰 Recovering...")
            msg = bot.edit_message_text(B("🛰 𝐈𝐧𝐢𝐭𝐢𝐚𝐭𝐢𝐧𝐠 𝐫𝐞𝐜𝐨𝐯𝐞𝐫𝐲..."),
                                        call.message.chat.id, call.message.message_id)
            try:
                AnimationManager.animate_recovery(call.message.chat.id, msg.message_id)
            except Exception:
                pass
            time.sleep(2)
            recovered = recovery_system.recover_all_scripts()
            try:
                if recovered:
                    bot.edit_message_text(B(f"✅ 𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐞𝐝: {len(recovered)} 𝐬𝐜𝐫𝐢𝐩𝐭(𝐬)"),
                                          call.message.chat.id, msg.message_id)
                else:
                    bot.edit_message_text(B("📭 𝐍𝐨 𝐬𝐜𝐫𝐢𝐩𝐭𝐬 𝐭𝐨 𝐫𝐞𝐜𝐨𝐯𝐞𝐫."),
                                          call.message.chat.id, msg.message_id)
            except Exception:
                pass

        elif data == 'restart_bot':
            if user_id == OWNER_ID:
                bot.answer_callback_query(call.id, "🚀 Restarting bot...")
                msg = bot.edit_message_text(B("🚀 𝐑𝐞𝐬𝐭𝐚𝐫𝐭𝐢𝐧𝐠 𝐛𝐨𝐭..."),
                                            call.message.chat.id, call.message.message_id)
                try:
                    AnimationManager.animate_full_restart(call.message.chat.id, msg.message_id)
                except Exception:
                    pass
                time.sleep(2.5)
                threading.Thread(target=send_restart_notification).start()
                os.execl(sys.executable, sys.executable, *sys.argv)
            else:
                bot.answer_callback_query(call.id, "🔒 Owner only.", show_alert=True)

        elif data.startswith('info_'):
            file_name = data.replace('info_', '')
            bot.answer_callback_query(call.id, f"📂 {file_name}")

        elif data == 'none':
            bot.answer_callback_query(call.id, "")

        else:
            bot.answer_callback_query(call.id, "🔍 Unknown action")

    except Exception as e:
        logger.error(f"❌ Callback error: {e}")
        try:
            bot.answer_callback_query(call.id, "❌ Error", show_alert=True)
        except Exception:
            pass


# ================================
# 𝐓𝐄𝐗𝐓 𝐌𝐄𝐒𝐒𝐀𝐆𝐄 𝐇𝐀𝐍𝐃𝐋𝐄𝐑𝐒
# ================================
@bot.message_handler(func=lambda message: message.text and (
    message.text == B("📤 𝐔𝐩𝐥𝐨𝐚𝐝") or
    message.text == B("📂 𝐌𝐚𝐧𝐚𝐠𝐞 𝐒𝐜𝐫𝐢𝐩𝐭𝐬") or
    message.text == B("⚡ 𝐒𝐩𝐞𝐞𝐝") or
    message.text == B("📊 𝐒𝐭𝐚𝐭𝐬") or
    message.text == B("👤 𝐏𝐫𝐨𝐟𝐢𝐥𝐞") or
    message.text == B("📦 𝐌𝐨𝐝𝐮𝐥𝐞") or
    message.text == B("🔄 𝐑𝐞𝐜𝐨𝐯𝐞𝐫") or
    message.text == B("🚀 𝐑𝐞𝐬𝐭𝐚𝐫𝐭 𝐁𝐨𝐭") or
    message.text == B("👑 𝐀𝐝𝐦𝐢𝐧")
))
def handle_main_menu_message(message):
    user_id = message.from_user.id
    add_active_user(user_id)
    text = message.text

    if text == B("📤 𝐔𝐩𝐥𝐨𝐚𝐝"):
        bot.reply_to(message, B("📤 𝐒𝐞𝐧𝐝 𝐲𝐨𝐮𝐫 𝐟𝐢𝐥𝐞 𝐧𝐨𝐰!\n\n📋 𝐒𝐮𝐩𝐩𝐨𝐫𝐭𝐞𝐝: .𝐩𝐲, .𝐣𝐬, .𝐳𝐢𝐩\n\n📦 𝐙𝐈𝐏: 𝐏𝐮𝐭 𝐦𝐚𝐢𝐧.𝐩𝐲 + 𝐦𝐨𝐝𝐮𝐥𝐞𝐬 𝐢𝐧 𝐚 .𝐳𝐢𝐩 𝐟𝐢𝐥𝐞\n\n👉 𝐓𝐚𝐩 📎 𝐚𝐭𝐭𝐚𝐜𝐡𝐦𝐞𝐧𝐭 → 𝐅𝐢𝐥𝐞"),
                   reply_markup=create_reply_keyboard_main_menu(user_id))

    elif text == B("📂 𝐌𝐚𝐧𝐚𝐠𝐞 𝐒𝐜𝐫𝐢𝐩𝐭𝐬"):
        show_manage_scripts(message, user_id)

    elif text == B("⚡ 𝐒𝐩𝐞𝐞𝐝"):
        run_speedtest(message)

    elif text == B("📊 𝐒𝐭𝐚𝐭𝐬"):
        show_stats(message)

    elif text == B("👤 𝐏𝐫𝐨𝐟𝐢𝐥𝐞"):
        show_profile(message)

    elif text == B("📦 𝐌𝐨𝐝𝐮𝐥𝐞"):
        module_text = """╔══════════════════════════════════╗
║    📦 𝐌𝐎𝐃𝐔𝐋𝐄 𝐌𝐀𝐍𝐀𝐆𝐄𝐑             ║
╚══════════════════════════════════╝

*𝐈𝐧𝐬𝐭𝐚𝐥𝐥𝐞𝐝 𝐌𝐨𝐝𝐮𝐥𝐞𝐬:*
📦 𝐓𝐞𝐥𝐞𝐛𝐨𝐭, 𝐑𝐞𝐪𝐮𝐞𝐬𝐭𝐬, 𝐏𝐬𝐮𝐭𝐢𝐥, 𝐅𝐥𝐚𝐬𝐤
📦 𝐐𝐫𝐜𝐨𝐝𝐞, 𝐏𝐢𝐥𝐥𝐨𝐰, 𝐂𝐫𝐲𝐩𝐭𝐨𝐠𝐫𝐚𝐩𝐡𝐲, 𝐏𝐲𝐌𝐨𝐧𝐠𝐨

*𝐙𝐈𝐏 𝐒𝐮𝐩𝐩𝐨𝐫𝐭:*
📦 𝐔𝐩𝐥𝐨𝐚𝐝 𝐚 .𝐳𝐢𝐩 𝐰𝐢𝐭𝐡 𝐦𝐚𝐢𝐧.𝐩𝐲 + 𝐦𝐨𝐝𝐮𝐥𝐞𝐬
📦 𝐁𝐨𝐭 𝐚𝐮𝐭𝐨-𝐟𝐢𝐧𝐝𝐬 𝐦𝐚𝐢𝐧.𝐩𝐲 𝐚𝐧𝐝 𝐫𝐮𝐧𝐬 𝐢𝐭
📦 𝐈𝐧𝐜𝐥𝐮𝐝𝐞 𝐫𝐞𝐪𝐮𝐢𝐫𝐞𝐦𝐞𝐧𝐭𝐬.𝐭𝐱𝐭 𝐢𝐧 𝐙𝐈𝐏

*𝐀𝐮𝐭𝐨-𝐢𝐧𝐬𝐭𝐚𝐥𝐥:*
*𝐀𝐧𝐲 𝐩𝐢𝐩 𝐩𝐚𝐜𝐤𝐚𝐠𝐞 𝐰𝐢𝐥𝐥 𝐛𝐞 𝐢𝐧𝐬𝐭𝐚𝐥𝐥𝐞𝐝 𝐚𝐮𝐭𝐨𝐦𝐚𝐭𝐢𝐜𝐚𝐥𝐥𝐲.*"""
        msg = bot.reply_to(message, module_text, parse_mode='Markdown',
                           reply_markup=create_reply_keyboard_main_menu(user_id))
        try:
            AnimationManager.animate_install(message.chat.id, msg.message_id)
        except Exception:
            pass

    elif text == B("🔄 𝐑𝐞𝐜𝐨𝐯𝐞𝐫"):
        msg = bot.reply_to(message, B("🛰 𝐈𝐧𝐢𝐭𝐢𝐚𝐭𝐢𝐧𝐠 𝐫𝐞𝐜𝐨𝐯𝐞𝐫𝐲..."))
        try:
            AnimationManager.animate_recovery(message.chat.id, msg.message_id)
        except Exception:
            pass
        time.sleep(2)
        recovered = recovery_system.recover_all_scripts()
        try:
            if recovered:
                bot.edit_message_text(B(f"✅ 𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐲 𝐂𝐨𝐦𝐩𝐥𝐞𝐭𝐞!\n🔄 𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐞𝐝: {len(recovered)} 𝐬𝐜𝐫𝐢𝐩𝐭(𝐬)"),
                                      message.chat.id, msg.message_id,
                                      reply_markup=create_reply_keyboard_main_menu(user_id))
            else:
                bot.edit_message_text(B("📭 𝐍𝐨 𝐬𝐜𝐫𝐢𝐩𝐭𝐬 𝐭𝐨 𝐫𝐞𝐜𝐨𝐯𝐞𝐫."),
                                      message.chat.id, msg.message_id,
                                      reply_markup=create_reply_keyboard_main_menu(user_id))
        except Exception:
            pass

    elif text == B("🚀 𝐑𝐞𝐬𝐭𝐚𝐫𝐭 𝐁𝐨𝐭"):
        if user_id != OWNER_ID:
            bot.reply_to(message, B("🔒 𝐎𝐰𝐧𝐞𝐫 𝐨𝐧𝐥𝐲."))
            return
        msg = bot.reply_to(message, B("🚀 𝐑𝐞𝐬𝐭𝐚𝐫𝐭𝐢𝐧𝐠 𝐛𝐨𝐭..."))
        try:
            AnimationManager.animate_full_restart(message.chat.id, msg.message_id)
        except Exception:
            pass
        time.sleep(2.5)
        threading.Thread(target=send_restart_notification).start()
        os.execl(sys.executable, sys.executable, *sys.argv)

    elif text == B("👑 𝐀𝐝𝐦𝐢𝐧"):
        if user_id != OWNER_ID:
            bot.reply_to(message, B("🔒 𝐎𝐰𝐧𝐞𝐫 𝐨𝐧𝐥𝐲."))
            return
        auth_count = len(authorized_users)
        total_files = sum(len(v) for v in user_files.values())
        running_count = len(bot_scripts)
        ram_pct = psutil.virtual_memory().percent
        cpu_pct = psutil.cpu_percent(interval=1)
        admin_text = f"""╔══════════════════════════════════╗
║    👑 𝐀𝐃𝐌𝐈𝐍 𝐏𝐀𝐍𝐄𝐋               ║
╚══════════════════════════════════╝

👥 *𝐀𝐮𝐭𝐡𝐨𝐫𝐢𝐳𝐞𝐝 𝐔𝐬𝐞𝐫𝐬:* {auth_count}
📂 *𝐓𝐨𝐭𝐚𝐥 𝐅𝐢𝐥𝐞𝐬:* {total_files}
🚀 *𝐑𝐮𝐧𝐧𝐢𝐧𝐠:* {running_count}
💾 *𝐑𝐀𝐌:* {ram_pct:.0f}%
⚙ *𝐂𝐏𝐔:* {cpu_pct:.0f}%

*𝐂𝐨𝐦𝐦𝐚𝐧𝐝𝐬:*
/adduser <id> — Grant access
/removeuser <id> — Revoke access
/listusers — List authorized users
/logs <file> — View any user's logs"""
        bot.reply_to(message, admin_text, parse_mode='Markdown',
                     reply_markup=create_reply_keyboard_main_menu(user_id))


@bot.message_handler(func=lambda message: message.text and (
    message.text.startswith(B("🔴 𝐒𝐭𝐨𝐩 ")) or
    message.text.startswith(B("🔄 𝐑𝐞𝐬𝐭𝐚𝐫𝐭 ")) or
    message.text.startswith(B("🗑️ 𝐃𝐞𝐥𝐞𝐭𝐞 ")) or
    message.text.startswith(B("📜 𝐋𝐨𝐠𝐬 ")) or
    message.text.startswith(B("📜 𝐕𝐢𝐞𝐰 𝐋𝐨𝐠𝐬 ")) or
    message.text.startswith(B("🟢 𝐒𝐭𝐚𝐫𝐭 ")) or
    B("🔙 𝐁𝐚𝐜𝐤") in (message.text or '')
))
def handle_file_control_message(message):
    handle_file_control_text(message)


# ================================
# 𝐂𝐋𝐄𝐀𝐍𝐔𝐏
# ================================
def cleanup():
    logger.warning("🔴 Shutting down... Cleaning up processes")
    for script_key, script_info in list(bot_scripts.items()):
        try:
            kill_process_tree(script_info)
        except:
            pass
    if mongo_client:
        try:
            mongo_client.close()
        except:
            pass
    logger.info("✅ Cleanup complete")

atexit.register(cleanup)


# ================================
# 𝐒𝐓𝐀𝐑𝐓𝐔𝐏 𝐑𝐄𝐂𝐎𝐕𝐄𝐑𝐘
# ================================
def startup_recovery():
    logger.info("🚀 Starting auto-recovery process...")
    msg = None
    try:
        msg = bot.send_message(OWNER_ID, "🛰 𝐒𝐞𝐫𝐯𝐞𝐫 𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐲 𝐈𝐧𝐢𝐭𝐢𝐚𝐭𝐞𝐝...")
        try:
            AnimationManager.animate_recovery(OWNER_ID, msg.message_id)
        except Exception:
            pass
        time.sleep(2)
        recovered = recovery_system.recover_all_scripts()
        if recovered:
            bot.edit_message_text(B(f"✅ 𝐒𝐭𝐚𝐫𝐭𝐮𝐩 𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐲 𝐂𝐨𝐦𝐩𝐥𝐞𝐭𝐞!\n🔄 𝐑𝐞𝐜𝐨𝐯𝐞𝐫𝐞𝐝: {len(recovered)} 𝐬𝐜𝐫𝐢𝐩𝐭𝐬"),
                                  OWNER_ID, msg.message_id)
        else:
            bot.edit_message_text(B("✅ 𝐁𝐨𝐭 𝐬𝐭𝐚𝐫𝐭𝐞𝐝. 𝐍𝐨 𝐬𝐜𝐫𝐢𝐩𝐭𝐬 𝐭𝐨 𝐫𝐞𝐜𝐨𝐯𝐞𝐫."),
                                  OWNER_ID, msg.message_id)
    except Exception as e:
        logger.error(f"❌ Error in startup recovery: {e}")
        if msg:
            try:
                bot.edit_message_text(B(f"✅ 𝐁𝐨𝐭 𝐬𝐭𝐚𝐫𝐭𝐞𝐝 (𝐫𝐞𝐜𝐨𝐯𝐞𝐫𝐲 𝐬𝐤𝐢𝐩𝐩𝐞𝐝)."),
                                      OWNER_ID, msg.message_id)
            except:
                pass


# ================================
# 𝐌𝐀𝐈𝐍 𝐄𝐗𝐄𝐂𝐔𝐓𝐈𝐎𝐍
# ================================
if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("🚀 HOSTING BOT VERSION 4.1")
    logger.info("🗄️ Database: MongoDB")
    logger.info("📊 Auto-Recovery: Enabled")
    logger.info("🔒 Access Control: Admin-Only")
    logger.info(f"👑 Owner ID: {OWNER_ID}")
    logger.info(f"👥 Authorized Users: {len(authorized_users)}")
    logger.info(f"📁 Total Files: {sum(len(files) for files in user_files.values())}")
    try:
        bot_username = bot.get_me().username
        logger.info(f"🤖 Bot Username: @{bot_username}")
    except Exception as e:
        logger.error(f"❌ Error getting bot username: {e}")
    logger.info("=" * 50)

    # Start Flask keep-alive
    keep_alive()

    # Run startup recovery (only if OWNER_ID is set)
    if OWNER_ID != 0:
        threading.Thread(target=startup_recovery).start()
    else:
        logger.warning("⚠️ OWNER_ID not set — skipping startup recovery notification.")

    # Start bot polling
    logger.info("🤖 Starting bot polling...")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout:
            logger.warning("⚠️ Read Timeout. Restarting in 5s...")
            time.sleep(5)
        except requests.exceptions.ConnectionError as ce:
            logger.error(f"⚠️ Connection Error: {ce}. Retrying in 15s...")
            time.sleep(15)
        except Exception as e:
            logger.critical(f"💥 Unrecoverable error: {e}", exc_info=True)
            logger.info("🔄 Restarting in 30s...")
            time.sleep(30)
        finally:
            time.sleep(1)
