#!/usr/bin/env python3
"""
Telegram File Bot — Complete version with all features
Compatible with Python 3.14.3, python-telegram-bot >=21.7, Flask >=2.3.3, pg8000 >=1.30.5
"""

import os
import sys
import time
import ssl
import logging
import threading
import asyncio
import urllib.parse
import signal
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from contextlib import suppress
from collections import defaultdict

import pg8000
from flask import Flask, request, jsonify, render_template_string

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------- Config & Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("telegram-file-bot")
log.setLevel(logging.INFO)

# Environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_PATH = "/telegram-webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else ""
PORT = int(os.environ.get("PORT", "5000"))
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")

# Channel settings
CHANNEL_1 = os.environ.get("CHANNEL_1", "").strip().replace("@", "")
CHANNEL_2 = os.environ.get("CHANNEL_2", "").strip().replace("@", "")

DELETE_AFTER = int(os.environ.get("DELETE_AFTER", "600"))  # seconds (10 minutes)
PLAYABLE_EXTS = {"mp4", "mov", "m4v", "mpeg", "mpg", "webm", "mkv", "avi"}
ALL_VIDEO_EXTS = {
    "mp4", "mkv", "mov", "avi", "webm", "flv", "m4v", "3gp", "wmv", "mpg", "mpeg"
}

# Validate required env vars
if not (BOT_TOKEN and ADMIN_ID and DATABASE_URL):
    log.error("Missing one of required env vars: BOT_TOKEN, ADMIN_ID, DATABASE_URL")
    sys.exit(1)

# ---------- Flask app ----------
app = Flask(__name__)
start_time = time.time()

# ---------- Database helper ----------
class Database:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.conn = None
        self._lock = threading.Lock()
        self.initialized = False
        self.params = self._parse_db_url(db_url)
        log.info(f"Parsed DB params: {self.params}")

    def _parse_db_url(self, db_url: str) -> Dict[str, Any]:
        """Parse PostgreSQL connection URL"""
        s = db_url.replace("postgresql://", "").replace("postgres://", "")
        user_pass, host_port_db = s.split("@", 1)
        user, password = user_pass.split(":", 1)
        password = urllib.parse.unquote(password)
        
        if "/" in host_port_db:
            host_port, database = host_port_db.split("/", 1)
        else:
            host_port = host_port_db
            database = "postgres"
            
        if ":" in host_port:
            host, port = host_port.split(":", 1)
            port = int(port)
        else:
            host = host_port
            port = 5432
            
        return {
            "user": user, 
            "password": password, 
            "host": host, 
            "port": port, 
            "database": database
        }

    def _ssl_ctx(self):
        """Create SSL context for database connection"""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def connect_sync(self, database: Optional[str] = None):
        """Synchronous database connection"""
        params = self.params.copy()
        if database:
            params["database"] = database
            
        return pg8000.connect(
            user=params["user"],
            password=params["password"],
            host=params["host"],
            port=params["port"],
            database=params["database"],
            ssl_context=self._ssl_ctx(),
            timeout=30,
        )

    async def ensure_database(self):
        """Ensure database exists"""
        def _ensure():
            target = self.params["database"]
            try:
                conn = self.connect_sync(target)
                conn.close()
                return True
            except Exception:
                try:
                    conn = self.connect_sync("postgres")
                    cur = conn.cursor()
                    conn.autocommit = True
                    cur.execute(f'CREATE DATABASE "{target}"')
                    conn.close()
                    return True
                except Exception as e:
                    log.error(f"Failed to create database: {e}")
                    raise
        return await asyncio.to_thread(_ensure)

    async def get_connection(self):
        """Get or create database connection"""
        with self._lock:
            if self.conn is None:
                self.conn = await asyncio.to_thread(self.connect_sync, self.params.get("database"))
        return self.conn

    async def init_db(self):
        """Initialize database tables"""
        conn = await self.get_connection()
        
        def _init():
            cur = conn.cursor()
            
            # Files table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT,
                    is_video INTEGER DEFAULT 0,
                    file_size BIGINT DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 0,
                    last_accessed TIMESTAMP
                )
            ''')
            
            # Membership cache table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS membership_cache (
                    user_id BIGINT,
                    channel TEXT,
                    is_member INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, channel)
                )
            ''')
            
            # Scheduled deletions table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_deletions (
                    chat_id BIGINT NOT NULL,
                    message_id INTEGER NOT NULL,
                    scheduled_time TIMESTAMP NOT NULL,
                    delete_after INTEGER DEFAULT 600,
                    PRIMARY KEY (chat_id, message_id)
                )
            ''')
            
            # Users table with enhanced tracking
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    total_interactions INTEGER DEFAULT 1,
                    total_files_accessed INTEGER DEFAULT 0,
                    last_file_accessed TIMESTAMP
                )
            ''')
            
            # Create indexes for better performance
            cur.execute('CREATE INDEX IF NOT EXISTS idx_files_access_count ON files(access_count)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_files_timestamp ON files(timestamp)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_deletions_time ON scheduled_deletions(scheduled_time)')
            
            conn.commit()
            
        await asyncio.to_thread(_init)
        self.initialized = True
        log.info("Database initialized successfully")

    async def save_file(self, file_id: str, file_info: dict) -> str:
        """Save file information to database"""
        conn = await self.get_connection()
        
        def _save():
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO files 
                   (file_id, file_name, mime_type, is_video, file_size, access_count) 
                   VALUES (%s,%s,%s,%s,%s,0) RETURNING id""",
                (
                    file_id, 
                    file_info.get("file_name", ""), 
                    file_info.get("mime_type", ""), 
                    1 if file_info.get("is_video") else 0, 
                    int(file_info.get("size", 0))
                )
            )
            nid = cur.fetchone()[0]
            conn.commit()
            return str(nid)
            
        return await asyncio.to_thread(_save)

    async def get_file(self, key: str) -> Optional[dict]:
        """Get file information by ID and increment access count"""
        try:
            kid = int(key)
        except ValueError:
            return None
            
        conn = await self.get_connection()
        
        def _get():
            cur = conn.cursor()
            cur.execute(
                """UPDATE files 
                   SET access_count = access_count + 1, last_accessed = CURRENT_TIMESTAMP 
                   WHERE id = %s 
                   RETURNING file_id, file_name, mime_type, is_video, file_size, 
                             TO_CHAR(timestamp, 'YYYY-MM-DD HH24:MI:SS'), access_count""",
                (kid,)
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return {
                    "file_id": row[0], 
                    "file_name": row[1], 
                    "mime_type": row[2], 
                    "is_video": bool(row[3]), 
                    "size": row[4], 
                    "timestamp": row[5], 
                    "access_count": row[6]
                }
            return None
            
        return await asyncio.to_thread(_get)

    async def delete_file(self, key: str) -> bool:
        """Delete file by ID"""
        try:
            kid = int(key)
        except ValueError:
            return False
            
        conn = await self.get_connection()
        
        def _del():
            cur = conn.cursor()
            cur.execute("DELETE FROM files WHERE id = %s", (kid,))
            rc = cur.rowcount
            conn.commit()
            return rc > 0
            
        return await asyncio.to_thread(_del)

    async def get_file_count(self) -> int:
        """Get total number of files"""
        conn = await self.get_connection()
        
        def _c():
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM files")
            return cur.fetchone()[0]
            
        return await asyncio.to_thread(_c)

    async def get_all_files(self, limit: int = 100) -> List[tuple]:
        """Get all files with details"""
        conn = await self.get_connection()
        
        def _g():
            cur = conn.cursor()
            cur.execute(
                """SELECT id, file_name, file_size, access_count, 
                          TO_CHAR(timestamp, 'YYYY-MM-DD HH24:MI:SS') 
                   FROM files ORDER BY timestamp DESC LIMIT %s""",
                (limit,)
            )
            return cur.fetchall()
            
        return await asyncio.to_thread(_g)

    async def get_user_count(self) -> int:
        """Get total number of users"""
        conn = await self.get_connection()
        
        def _c():
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            return cur.fetchone()[0]
            
        return await asyncio.to_thread(_c)

    async def get_active_users(self, days: int = 7) -> int:
        """Get number of active users in last X days"""
        conn = await self.get_connection()
        
        def _c():
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE last_active >= CURRENT_TIMESTAMP - INTERVAL '%s days'",
                (days,)
            )
            return cur.fetchone()[0]
            
        return await asyncio.to_thread(_c)

    async def get_new_users(self, days: int = 7) -> int:
        """Get number of new users in last X days"""
        conn = await self.get_connection()
        
        def _c():
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE first_seen >= CURRENT_TIMESTAMP - INTERVAL '%s days'",
                (days,)
            )
            return cur.fetchone()[0]
            
        return await asyncio.to_thread(_c)

    async def get_top_users(self, limit: int = 10) -> List[tuple]:
        """Get users with most file accesses"""
        conn = await self.get_connection()
        
        def _g():
            cur = conn.cursor()
            cur.execute(
                """SELECT user_id, username, first_name, total_files_accessed, 
                          TO_CHAR(last_active, 'YYYY-MM-DD HH24:MI:SS') 
                   FROM users 
                   WHERE total_files_accessed > 0 
                   ORDER BY total_files_accessed DESC 
                   LIMIT %s""",
                (limit,)
            )
            return cur.fetchall()
            
        return await asyncio.to_thread(_g)

    async def schedule_deletion(self, chat_id: int, message_id: int):
        """Schedule a message for deletion"""
        conn = await self.get_connection()
        st = datetime.now() + timedelta(seconds=DELETE_AFTER)
        
        def _s():
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO scheduled_deletions (chat_id, message_id, scheduled_time, delete_after) 
                   VALUES (%s,%s,%s,%s) 
                   ON CONFLICT (chat_id, message_id) 
                   DO UPDATE SET scheduled_time = EXCLUDED.scheduled_time""",
                (chat_id, message_id, st, DELETE_AFTER)
            )
            conn.commit()
            
        return await asyncio.to_thread(_s)

    async def get_due_messages(self) -> List[Tuple[int, int]]:
        """Get messages due for deletion"""
        conn = await self.get_connection()
        
        def _g():
            cur = conn.cursor()
            cur.execute(
                "SELECT chat_id, message_id FROM scheduled_deletions WHERE scheduled_time <= CURRENT_TIMESTAMP"
            )
            return cur.fetchall()
            
        return await asyncio.to_thread(_g)

    async def remove_scheduled(self, chat_id: int, message_id: int):
        """Remove scheduled deletion"""
        conn = await self.get_connection()
        
        def _r():
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM scheduled_deletions WHERE chat_id = %s AND message_id = %s",
                (chat_id, message_id)
            )
            conn.commit()
            
        return await asyncio.to_thread(_r)

    async def update_user_interaction(
        self, 
        user_id: int, 
        username: Optional[str] = None, 
        first_name: Optional[str] = None, 
        last_name: Optional[str] = None, 
        file_accessed: bool = False
    ):
        """Update user interaction statistics"""
        conn = await self.get_connection()
        
        def _u():
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM users WHERE user_id = %s", (user_id,))
            
            if cur.fetchone():
                cur.execute(
                    """UPDATE users 
                       SET last_active = CURRENT_TIMESTAMP, 
                           total_interactions = total_interactions + 1,
                           username = COALESCE(%s, username),
                           first_name = COALESCE(%s, first_name),
                           last_name = COALESCE(%s, last_name)
                       WHERE user_id = %s""",
                    (username, first_name, last_name, user_id)
                )
                
                if file_accessed:
                    cur.execute(
                        """UPDATE users 
                           SET total_files_accessed = total_files_accessed + 1, 
                               last_file_accessed = CURRENT_TIMESTAMP 
                           WHERE user_id = %s""",
                        (user_id,)
                    )
            else:
                cur.execute(
                    """INSERT INTO users 
                       (user_id, username, first_name, last_name, total_files_accessed) 
                       VALUES (%s, %s, %s, %s, %s)""",
                    (user_id, username, first_name, last_name, 1 if file_accessed else 0)
                )
                
            conn.commit()
            
        return await asyncio.to_thread(_u)

    async def get_all_user_ids(self, exclude_admin: bool = True) -> List[int]:
        """Get all user IDs"""
        conn = await self.get_connection()
        
        def _g():
            cur = conn.cursor()
            if exclude_admin:
                cur.execute("SELECT user_id FROM users WHERE user_id != %s", (ADMIN_ID,))
            else:
                cur.execute("SELECT user_id FROM users")
            return [r[0] for r in cur.fetchall()]
            
        return await asyncio.to_thread(_g)

    async def update_membership_cache(self, user_id: int, channel: str, is_member: bool):
        """Update membership cache"""
        conn = await self.get_connection()
        
        def _u():
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO membership_cache (user_id, channel, is_member) 
                   VALUES (%s, %s, %s) 
                   ON CONFLICT (user_id, channel) 
                   DO UPDATE SET is_member = %s, timestamp = CURRENT_TIMESTAMP""",
                (user_id, channel, 1 if is_member else 0, 1 if is_member else 0)
            )
            conn.commit()
            
        return await asyncio.to_thread(_u)

    async def check_membership_cache(self, user_id: int, channel: str) -> Optional[bool]:
        """Check cached membership status"""
        conn = await self.get_connection()
        
        def _c():
            cur = conn.cursor()
            cur.execute(
                """SELECT is_member, timestamp 
                   FROM membership_cache 
                   WHERE user_id = %s AND channel = %s 
                   AND timestamp > CURRENT_TIMESTAMP - INTERVAL '1 hour'""",
                (user_id, channel)
            )
            row = cur.fetchone()
            if row:
                return bool(row[0])
            return None
            
        return await asyncio.to_thread(_c)

    async def cleanup_old_cache(self, hours: int = 24):
        """Clean up old cache entries"""
        conn = await self.get_connection()
        
        def _c():
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM membership_cache WHERE timestamp < CURRENT_TIMESTAMP - INTERVAL '%s hours'",
                (hours,)
            )
            conn.commit()
            return cur.rowcount
            
        return await asyncio.to_thread(_c)

    def get_sync_connection(self):
        """Get synchronous connection for Flask routes"""
        try:
            if not self.initialized:
                return None
            return self.connect_sync(self.params.get("database"))
        except Exception as e:
            log.error(f"Failed to create sync connection: {e}")
            return None


db = Database(DATABASE_URL)

# ---------- Bot & handlers ----------
application: Optional[Application] = None
bot_loop: Optional[asyncio.AbstractEventLoop] = None

async def _delete_job(context: ContextTypes.DEFAULT_TYPE):
    """Job to delete messages"""
    job = context.job
    chat_id = job.chat_id
    message_id = job.data
    
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        await db.remove_scheduled(chat_id, message_id)
    except Exception as e:
        log.warning(f"Failed to delete message {message_id}: {e}")
        await db.remove_scheduled(chat_id, message_id)

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Schedule message deletion"""
    try:
        await db.schedule_deletion(chat_id, message_id)
        if context.job_queue:
            context.job_queue.run_once(
                _delete_job, 
                DELETE_AFTER, 
                data=message_id, 
                chat_id=chat_id,
                name=f"delete_{chat_id}_{message_id}"
            )
    except Exception as e:
        log.warning(f"schedule_message_deletion failed: {e}")

async def check_user_in_channel(bot, channel: str, user_id: int) -> bool:
    """Check if user is member of a channel"""
    if not channel:
        return True
        
    ch = channel if channel.startswith("@") else f"@{channel}"
    
    cached = await db.check_membership_cache(user_id, channel)
    if cached is not None:
        return cached
    
    try:
        mem = await bot.get_chat_member(chat_id=ch, user_id=user_id)
        is_member = mem.status in ("member", "administrator", "creator")
        await db.update_membership_cache(user_id, channel, is_member)
        return is_member
    except Exception as e:
        log.warning(f"Membership check failed for {ch}: {e}")
        return True

async def check_channels_membership(bot, user_id: int) -> Tuple[bool, List[str]]:
    """Check if user is member of both channels"""
    if not CHANNEL_1 and not CHANNEL_2:
        return True, []
    
    missing_channels = []
    
    if CHANNEL_1:
        is_member = await check_user_in_channel(bot, CHANNEL_1, user_id)
        if not is_member:
            missing_channels.append(CHANNEL_1)
    
    if CHANNEL_2:
        is_member = await check_user_in_channel(bot, CHANNEL_2, user_id)
        if not is_member:
            missing_channels.append(CHANNEL_2)
    
    return len(missing_channels) == 0, missing_channels

def get_membership_keyboard(missing_channels: List[str], file_key: str = None) -> InlineKeyboardMarkup:
    """Create keyboard for missing channels - with proper buttons"""
    keyboard = []
    
    # Add join buttons for each missing channel
    for channel in missing_channels:
        clean_channel = channel.replace("@", "")
        keyboard.append([
            InlineKeyboardButton(
                f"📢 Join @{clean_channel}", 
                url=f"https://t.me/{clean_channel}"
            )
        ])
    
    # Add check button
    if file_key:
        keyboard.append([
            InlineKeyboardButton("✅ Check Again", callback_data=f"check_{file_key}")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("✅ Check Again", callback_data="check_membership")
        ])
    
    return InlineKeyboardMarkup(keyboard)

# Handlers
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    if not update.message or not update.effective_user:
        return
    
    user = update.effective_user
    await db.update_user_interaction(
        user.id, 
        user.username, 
        user.first_name, 
        user.last_name
    )
    
    args = context.args
    
    # If there are channels configured, always show channel join first
    if CHANNEL_1 or CHANNEL_2:
        is_member, missing = await check_channels_membership(context.bot, user.id)
        
        if not is_member:
            # If user has a file key, pass it to the callback
            file_key = args[0] if args else None
            keyboard = get_membership_keyboard(missing, file_key)
            
            sent = await update.message.reply_text(
                "🔒 **Access Restricted**\n\n"
                "To access files, please join our channels:",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
            await schedule_message_deletion(context, sent.chat_id, sent.message_id)
            return
    
    # If no channels or user is already a member, handle file access or welcome
    if not args:
        welcome_text = (
            f"👋 Welcome {user.first_name}!\n\n"
            "This bot helps you access files shared by the admin.\n\n"
            "📁 To access a file, use the special link provided by the admin.\n"
            f"⏳ All messages auto-delete after {DELETE_AFTER//60} minutes."
        )
        
        sent = await update.message.reply_text(welcome_text)
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    
    # Handle file access with key
    key = args[0]
    info = await db.get_file(key)
    
    if not info:
        sent = await update.message.reply_text("❌ File not found or has been deleted.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    
    # Double-check membership (in case they joined after seeing the channel message)
    if CHANNEL_1 or CHANNEL_2:
        is_member, missing = await check_channels_membership(context.bot, user.id)
        
        if not is_member:
            keyboard = get_membership_keyboard(missing, key)
            sent = await update.message.reply_text(
                "🔒 Please join our channels to access this file:",
                reply_markup=keyboard
            )
            await schedule_message_deletion(context, sent.chat_id, sent.message_id)
            return
    
    await db.update_user_interaction(
        user.id, 
        user.username, 
        user.first_name, 
        user.last_name,
        file_accessed=True
    )
    
    try:
        fname = info["file_name"]
        ext = fname.lower().split(".")[-1] if "." in fname else ""
        
        size_mb = info["size"] / (1024 * 1024)
        caption = (
            f"📄 {fname}\n"
            f"📊 Size: {size_mb:.2f} MB\n"
            f"👀 Views: {info['access_count']}\n"
            f"⏳ Auto-deletes in {DELETE_AFTER//60} minutes"
        )
        
        if info["is_video"] and ext in PLAYABLE_EXTS:
            sent = await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=info["file_id"],
                caption=caption,
                supports_streaming=True
            )
        else:
            sent = await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=info["file_id"],
                caption=caption
            )
            
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        
    except Exception as e:
        log.exception("Failed to send file")
        sent = await update.message.reply_text("❌ Failed to send file. Please try again later.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    msg = update.message
    if not msg:
        return
    
    video = msg.video
    doc = msg.document
    
    if not video and not doc:
        sent = await msg.reply_text("❌ Please send a video or document to upload.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    
    if video:
        fid = video.file_id
        fname = video.file_name or f"video_{int(time.time())}.mp4"
        mime = video.mime_type or "video/mp4"
        size = video.file_size or 0
        is_video = True
    else:  # document
        fid = doc.file_id
        fname = doc.file_name or f"file_{int(time.time())}"
        mime = doc.mime_type or ""
        size = doc.file_size or 0
        ext = fname.lower().split(".")[-1] if "." in fname else ""
        is_video = ext in ALL_VIDEO_EXTS
    
    file_info = {
        "file_name": fname, 
        "mime_type": mime, 
        "is_video": is_video, 
        "size": int(size)
    }
    
    key = await db.save_file(fid, file_info)
    
    # Get bot username if not set
    global BOT_USERNAME
    if not BOT_USERNAME and context.bot:
        bot_info = await context.bot.get_me()
        BOT_USERNAME = bot_info.username
    
    # Create the shareable link
    if BOT_USERNAME:
        link = f"https://t.me/{BOT_USERNAME}?start={key}"
    else:
        link = f"Use this key: {key}"
    
    size_mb = size / (1024 * 1024)
    
    response = (
        f"✅ **File saved successfully!**\n\n"
        f"📄 **Name:** `{fname}`\n"
        f"📊 **Size:** {size_mb:.2f} MB\n"
        f"🔑 **File ID:** `{key}`\n"
        f"🔗 **Share Link:**\n`{link}`\n\n"
        f"**Commands:**\n"
        f"• /listfiles - View all files\n"
        f"• /deletefile {key} - Delete this file\n"
        f"• /stats - Bot statistics\n"
        f"• /users - User list\n"
        f"• /broadcast - Send message to all users"
    )
    
    sent = await msg.reply_text(response, parse_mode=ParseMode.MARKDOWN)
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def listfiles_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all files (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    rows = await db.get_all_files(limit=100)
    
    if not rows:
        sent = await update.message.reply_text("📁 No files stored yet.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    
    msg = "📁 **Stored Files:**\n\n"
    
    for i, row in enumerate(rows, 1):
        fid, name, size, access, ts = row
        size_kb = size // 1024
        size_mb = size / (1024 * 1024)
        
        if size_mb >= 1:
            size_str = f"{size_mb:.2f} MB"
        else:
            size_str = f"{size_kb} KB"
        
        msg += f"{i}. `{fid}` - {name[:40]}\n"
        msg += f"   📊 {size_str} | 👀 {access} views | 📅 {ts}\n\n"
        
        if len(msg) > 3500:
            msg += "... (truncated)"
            break
    
    sent = await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def deletefile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a file (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    if not context.args:
        sent = await update.message.reply_text("❌ Usage: /deletefile <file_id>")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    
    key = context.args[0]
    ok = await db.delete_file(key)
    
    if ok:
        sent = await update.message.reply_text(f"✅ File {key} deleted successfully.")
    else:
        sent = await update.message.reply_text(f"❌ File {key} not found.")
    
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    uptime = str(timedelta(seconds=int(time.time() - start_time)))
    files = await db.get_file_count()
    users = await db.get_user_count()
    active_7d = await db.get_active_users(7)
    active_30d = await db.get_active_users(30)
    new_7d = await db.get_new_users(7)
    new_30d = await db.get_new_users(30)
    
    top_users = await db.get_top_users(5)
    
    stats_msg = (
        f"📊 **Bot Statistics**\n\n"
        f"⏱️ Uptime: {uptime}\n"
        f"📁 Total Files: {files}\n"
        f"👥 Total Users: {users}\n\n"
        f"📈 **Activity**\n"
        f"• Active (7d): {active_7d}\n"
        f"• Active (30d): {active_30d}\n"
        f"• New (7d): {new_7d}\n"
        f"• New (30d): {new_30d}\n\n"
    )
    
    if top_users:
        stats_msg += "🏆 **Top Users**\n"
        for i, (uid, username, first_name, accesses, last_active) in enumerate(top_users, 1):
            name = username or first_name or str(uid)
            stats_msg += f"{i}. {name[:20]} - {accesses} files\n"
    
    sent = await update.message.reply_text(stats_msg, parse_mode=ParseMode.MARKDOWN)
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast message to all users (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    if not context.args and not update.message.reply_to_message:
        sent = await update.message.reply_text(
            "❌ Usage: /broadcast <message> or reply to a message with /broadcast"
        )
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    
    if update.message.reply_to_message:
        text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
    else:
        text = " ".join(context.args)
    
    if not text:
        sent = await update.message.reply_text("❌ Message cannot be empty.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    
    user_ids = await db.get_all_user_ids(exclude_admin=True)
    
    if not user_ids:
        sent = await update.message.reply_text("❌ No users to broadcast to.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    
    status_msg = await update.message.reply_text(
        f"📢 Broadcasting to {len(user_ids)} users...\n"
        f"This may take a few moments."
    )
    
    success = 0
    failed = 0
    
    for i, uid in enumerate(user_ids, 1):
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 **Broadcast Message**\n\n{text}",
                parse_mode=ParseMode.MARKDOWN
            )
            success += 1
            
            if i % 10 == 0:
                await status_msg.edit_text(
                    f"📢 Broadcasting...\n"
                    f"Progress: {i}/{len(user_ids)}\n"
                    f"✅ Sent: {success}\n"
                    f"❌ Failed: {failed}"
                )
            
            await asyncio.sleep(0.05)
            
        except Exception as e:
            failed += 1
            log.warning(f"Failed to broadcast to {uid}: {e}")
    
    result_msg = (
        f"✅ **Broadcast Complete**\n\n"
        f"📊 **Results:**\n"
        f"• Total users: {len(user_ids)}\n"
        f"• ✅ Sent: {success}\n"
        f"• ❌ Failed: {failed}\n"
    )
    
    await status_msg.edit_text(result_msg, parse_mode=ParseMode.MARKDOWN)
    await schedule_message_deletion(context, status_msg.chat_id, status_msg.message_id)

async def users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user list (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    total_users = await db.get_user_count()
    active_7d = await db.get_active_users(7)
    active_30d = await db.get_active_users(30)
    new_7d = await db.get_new_users(7)
    
    top_users = await db.get_top_users(10)
    
    msg = (
        f"👥 **User Statistics**\n\n"
        f"📊 **Overview:**\n"
        f"• Total Users: {total_users}\n"
        f"• Active (7d): {active_7d}\n"
        f"• Active (30d): {active_30d}\n"
        f"• New (7d): {new_7d}\n\n"
    )
    
    if top_users:
        msg += "🏆 **Top 10 Users by File Access:**\n\n"
        for i, (uid, username, first_name, accesses, last_active) in enumerate(top_users, 1):
            name = username or first_name or f"User {uid}"
            msg += f"{i}. {name[:30]}\n"
            msg += f"   📁 {accesses} files | Last: {last_active}\n\n"
    
    sent = await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries"""
    query = update.callback_query
    if not query:
        return
    
    await query.answer()
    
    data = query.data
    log.info(f"Callback received: {data}")
    
    if data == "check_membership":
        user = query.from_user
        
        if not CHANNEL_1 and not CHANNEL_2:
            await query.edit_message_text("✅ No channels to check!")
            return
        
        is_member, missing = await check_channels_membership(context.bot, user.id)
        
        if is_member:
            await query.edit_message_text(
                "✅ **Success!**\n\nYou now have access to all files.\n\nSend /start to continue.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            keyboard = get_membership_keyboard(missing)
            await query.edit_message_text(
                "❌ **Not a member yet**\n\nPlease join these channels first:",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
    
    elif data.startswith("check_"):
        file_key = data.replace("check_", "")
        user = query.from_user
        
        info = await db.get_file(file_key)
        if not info:
            await query.edit_message_text("❌ File not found.")
            return
        
        is_member, missing = await check_channels_membership(context.bot, user.id)
        
        if not is_member:
            keyboard = get_membership_keyboard(missing, file_key)
            await query.edit_message_text(
                "❌ **Not a member yet**\n\nPlease join these channels first:",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        await db.update_user_interaction(
            user.id, 
            user.username, 
            user.first_name, 
            user.last_name,
            file_accessed=True
        )
        
        try:
            fname = info["file_name"]
            ext = fname.lower().split(".")[-1] if "." in fname else ""
            
            size_mb = info["size"] / (1024 * 1024)
            caption = (
                f"📄 {fname}\n"
                f"📊 Size: {size_mb:.2f} MB\n"
                f"👀 Views: {info['access_count']}\n"
                f"⏳ Auto-deletes in {DELETE_AFTER//60} minutes"
            )
            
            if info["is_video"] and ext in PLAYABLE_EXTS:
                sent = await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=info["file_id"],
                    caption=caption,
                    supports_streaming=True
                )
            else:
                sent = await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=info["file_id"],
                    caption=caption
                )
            
            await query.edit_message_text("✅ File sent below!")
            await schedule_message_deletion(context, sent.chat_id, sent.message_id)
            
        except Exception as e:
            log.exception("Failed to send file")
            await query.edit_message_text("❌ Failed to send file.")
    else:
        log.warning(f"Unknown callback data: {data}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    log.error(f"Update {update} caused error {context.error}")

# ---------- Flask routes ----------
@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    """Handle Telegram webhook"""
    global application
    
    if application is None:
        return "Bot not ready", 503
    
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.process_update(update))
        loop.close()
        
        return "ok", 200
        
    except Exception as e:
        log.exception(f"Webhook error: {e}")
        return "error", 500

@app.route("/", methods=["GET"])
def home():
    """Web dashboard"""
    uptime = str(timedelta(seconds=int(time.time() - start_time)))
    file_count = 0
    user_count = 0
    
    try:
        if db.initialized:
            conn = db.get_sync_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM files")
                file_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM users")
                user_count = cursor.fetchone()[0]
                conn.close()
    except Exception as e:
        log.error(f"Error getting stats: {e}")
    
    # Auto-detect external URL from request if not set
    global RENDER_EXTERNAL_URL, WEBHOOK_URL
    if not RENDER_EXTERNAL_URL and request.headers.get('Host'):
        scheme = 'https' if request.headers.get('X-Forwarded-Proto', 'http') == 'https' else 'http'
        RENDER_EXTERNAL_URL = f"{scheme}://{request.headers['Host']}"
        WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Telegram File Bot</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                margin: 0;
                padding: 20px;
                min-height: 100vh;
                color: #333;
            }
            .container {
                max-width: 800px;
                margin: 0 auto;
            }
            .card {
                background: white;
                border-radius: 15px;
                padding: 30px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                margin-bottom: 20px;
            }
            h1 {
                margin: 0 0 20px 0;
                color: #333;
                font-size: 2em;
            }
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin: 30px 0;
            }
            .stat-item {
                background: #f8f9fa;
                border-radius: 10px;
                padding: 20px;
                text-align: center;
            }
            .stat-value {
                font-size: 2.5em;
                font-weight: bold;
                color: #667eea;
                margin-bottom: 5px;
            }
            .stat-label {
                color: #666;
                font-size: 0.9em;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            .info-row {
                display: flex;
                justify-content: space-between;
                padding: 10px 0;
                border-bottom: 1px solid #eee;
            }
            .info-label {
                color: #666;
                font-weight: 500;
            }
            .info-value {
                color: #333;
                font-family: monospace;
            }
            .badge {
                display: inline-block;
                padding: 5px 10px;
                border-radius: 5px;
                font-size: 0.8em;
                font-weight: bold;
            }
            .badge-success {
                background: #d4edda;
                color: #155724;
            }
            .footer {
                text-align: center;
                color: white;
                margin-top: 20px;
            }
            .footer a {
                color: white;
                text-decoration: none;
                opacity: 0.8;
            }
            .footer a:hover {
                opacity: 1;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h1>🤖 Telegram File Bot</h1>
                
                <div class="stats-grid">
                    <div class="stat-item">
                        <div class="stat-value">{{ files }}</div>
                        <div class="stat-label">Total Files</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{{ users }}</div>
                        <div class="stat-label">Total Users</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{{ uptime.split()[0] }}</div>
                        <div class="stat-label">Uptime (days)</div>
                    </div>
                </div>
                
                <div class="info-row">
                    <span class="info-label">Bot Status</span>
                    <span class="info-value"><span class="badge badge-success">✅ Active</span></span>
                </div>
                
                <div class="info-row">
                    <span class="info-label">Webhook URL</span>
                    <span class="info-value"><code>{{ webhook }}</code></span>
                </div>
                
                <div class="info-row">
                    <span class="info-label">Database</span>
                    <span class="info-value"><span class="badge badge-success">✅ Connected</span></span>
                </div>
                
                <div style="margin-top: 30px; text-align: center;">
                    <a href="https://t.me/{{ bot_username }}" target="_blank" style="background: #667eea; color: white; padding: 10px 20px; border-radius: 5px; text-decoration: none;">Open Bot</a>
                </div>
            </div>
            
            <div class="footer">
                <p>Made with ❤️ for Telegram</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(
        html, 
        uptime=uptime, 
        files=file_count, 
        users=user_count, 
        webhook=WEBHOOK_URL,
        bot_username=BOT_USERNAME
    )

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    file_count = 0
    user_count = 0
    
    try:
        if db.initialized:
            conn = db.get_sync_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM files")
                file_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM users")
                user_count = cursor.fetchone()[0]
                conn.close()
    except Exception:
        pass
    
    return jsonify({
        "status": "ok",
        "uptime_seconds": int(time.time() - start_time),
        "webhook": WEBHOOK_URL,
        "db_initialized": db.initialized,
        "file_count": file_count,
        "user_count": user_count,
        "channels": {
            "channel1": bool(CHANNEL_1),
            "channel2": bool(CHANNEL_2)
        }
    }), 200

@app.route("/ping", methods=["GET"])
def ping():
    """Ping endpoint"""
    return "pong", 200

# ---------- Bot startup ----------
async def start_bot():
    """Start the bot"""
    global application, db, BOT_USERNAME, WEBHOOK_URL, RENDER_EXTERNAL_URL
    
    log.info("Starting bot initialization...")
    
    await db.ensure_database()
    await db.init_db()
    log.info("Database ready")
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    bot_info = await application.bot.get_me()
    BOT_USERNAME = bot_info.username
    log.info(f"Bot username: @{BOT_USERNAME}")
    
    # Add all handlers
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("upload", upload_handler))
    application.add_handler(CommandHandler("listfiles", listfiles_handler))
    application.add_handler(CommandHandler("deletefile", deletefile_handler))
    application.add_handler(CommandHandler("stats", stats_handler))
    application.add_handler(CommandHandler("broadcast", broadcast_handler))
    application.add_handler(CommandHandler("users", users_handler))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_error_handler(error_handler)
    
    # File upload handler for admin
    upload_filter = filters.VIDEO | filters.Document.ALL
    application.add_handler(
        MessageHandler(upload_filter & filters.User(ADMIN_ID) & filters.ChatType.PRIVATE, upload_handler)
    )
    
    await application.initialize()
    await application.start()
    
    # Try to set webhook if URL is available
    if RENDER_EXTERNAL_URL:
        await application.bot.delete_webhook(drop_pending_updates=True)
        await application.bot.set_webhook(url=WEBHOOK_URL)
        log.info(f"Webhook set: {WEBHOOK_URL}")
    else:
        log.warning("RENDER_EXTERNAL_URL not set, webhook not configured")
        # Start polling as fallback
        log.info("Starting polling...")
        await application.updater.start_polling()
    
    log.info("Bot is running")
    
    while True:
        await asyncio.sleep(3600)

def run_flask():
    """Run Flask in a separate thread"""
    log.info(f"Starting Flask on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def main():
    """Main function"""
    log.info("=" * 50)
    log.info("Starting Telegram File Bot")
    log.info(f"Bot Token: {BOT_TOKEN[:10]}...")
    log.info(f"Admin ID: {ADMIN_ID}")
    log.info(f"Database: {DATABASE_URL[:20]}...")
    log.info(f"Webhook URL: {WEBHOOK_URL or 'Not set (will use polling)'}")
    log.info(f"Channel 1: {CHANNEL_1 or 'Not set'}")
    log.info(f"Channel 2: {CHANNEL_2 or 'Not set'}")
    log.info("=" * 50)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
    finally:
        log.info("Shutting down...")

if __name__ == "__main__":
    main()
