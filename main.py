import asyncio
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
import threading
import urllib.parse

import pg8000
from pg8000 import connect as pg_connect
from pg8000.native import literal, identifier

from flask import Flask, render_template_string, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

CHANNEL_1 = os.environ.get("CHANNEL_1", "A_Knight_of_the_Seven_Kingdoms_t").replace("@", "")
CHANNEL_2 = os.environ.get("CHANNEL_2", "your_movies_web").replace("@", "")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

DELETE_AFTER = 600               # 10 minutes
MAX_STORED_FILES = 10000
AUTO_CLEANUP_DAYS = 0           # 0 = never auto‑clean

PLAYABLE_EXTS = {"mp4", "mov", "m4v", "mpeg", "mpg"}
ALL_VIDEO_EXTS = {
    "mp4", "mkv", "mov", "avi", "webm", "flv", "m4v",
    "3gp", "wmv", "mpg", "mpeg"
}

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("pg8000").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# ================= FLASK DASHBOARD (sync pg8000) =================
app = Flask(__name__)
start_time = time.time()
bot_username = "xiomovies_bot"   # updated later

def parse_db_url(url):
    """Parse DATABASE_URL and return connection parameters."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    parsed = urllib.parse.urlparse(url)
    return {
        "user": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip('/'),
        "ssl_context": False  # CRITICAL FIX: Disable SSL certificate verification
    }

def get_sync_conn():
    """Create a synchronous pg8000 connection from DATABASE_URL."""
    params = parse_db_url(DATABASE_URL)
    conn = pg_connect(**params)
    return conn

def get_db_stats_sync():
    """Synchronous stats for Flask – simple counts."""
    try:
        conn = get_sync_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM files")
        file_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        user_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return file_count, user_count
    except Exception as e:
        log.error(f"Flask DB stats error: {e}")
        return 0, 0

@app.route('/')
def home():
    uptime_seconds = time.time() - start_time
    uptime_str = str(timedelta(seconds=int(uptime_seconds)))
    file_count, user_count = get_db_stats_sync()
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 Telegram File Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; min-height: 100vh; }
            .container { background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); padding: 20px; border-radius: 10px; box-shadow: 0 4px 16px rgba(0,0,0,0.2); }
            h1 { color: white; margin-top: 0; font-size: 1.5rem; }
            .status { background: rgba(0,255,0,0.2); padding: 10px; border-radius: 8px; margin: 10px 0; border-left: 4px solid #00ff00; }
            .info { background: rgba(255,255,255,0.1); padding: 10px; border-radius: 8px; margin: 10px 0; }
            a { color: #FFD700; text-decoration: none; }
            .btn { display: inline-block; background: #4CAF50; color: white; padding: 8px 16px; border-radius: 6px; margin: 5px; font-size: 0.9rem; }
            .warning { background: rgba(255,165,0,0.2); border-left: 4px solid #ffa500; padding: 10px; border-radius: 8px; margin: 10px 0; font-size: 0.9rem; }
            code { background: rgba(0,0,0,0.3); padding: 2px 4px; border-radius: 3px; font-family: monospace; }
            ul { padding-left: 20px; }
            li { margin: 5px 0; }
            footer { margin-top: 20px; border-top: 1px solid rgba(255,255,255,0.2); padding-top: 10px; font-size: 0.8rem; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Telegram File Bot</h1>
            <div class="status">
                <h3>✅ Status: <strong>ACTIVE</strong></h3>
                <p>Bot is running on Render with PostgreSQL (pg8000)</p>
                <p>Uptime: {{ uptime }}</p>
                <p>Files in DB: {{ file_count }}</p>
                <p>Users in DB: {{ user_count }}</p>
                <p>📁 Storage: PostgreSQL (persistent)</p>
            </div>
            <div class="info">
                <h3>📊 Bot Information</h3>
                <ul>
                    <li>Service: <strong>Render Web Service</strong></li>
                    <li>Bot: <strong>@{{ bot_username }}</strong></li>
                    <li>Database: <strong>PostgreSQL (pg8000)</strong></li>
                    <li>File Storage: <strong>PERMANENT</strong> (no auto‑cleanup)</li>
                    <li>Message Auto‑delete: <strong>{{ delete_minutes }} minutes</strong></li>
                </ul>
            </div>
            <div class="warning">
                <h3>⚠️ Important Notes</h3>
                <ul>
                    <li>Files are stored in <strong>PostgreSQL</strong> – survives restarts!</li>
                    <li>Only chat messages auto‑delete after {{ delete_minutes }} minutes</li>
                    <li>Admin must manually delete files if needed</li>
                </ul>
            </div>
            <div class="info">
                <h3>📞 Start Bot</h3>
                <p><a href="https://t.me/{{ bot_username }}" target="_blank" class="btn">Start @{{ bot_username }}</a></p>
            </div>
            <footer>
                <small>Render • {{ current_time }} • v2.1 • pg8000 • SSL verification OFF</small>
            </footer>
        </div>
    </body>
    </html>
    """
    return render_template_string(
        html,
        bot_username=bot_username,
        uptime=uptime_str,
        current_time=datetime.now().strftime("%H:%M:%S"),
        file_count=file_count,
        user_count=user_count,
        delete_minutes=DELETE_AFTER // 60
    )

@app.route('/health')
def health():
    file_count, user_count = get_db_stats_sync()
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now().isoformat(),
        "service": "telegram-file-bot",
        "uptime": str(timedelta(seconds=int(time.time() - start_time))),
        "database": "postgresql (pg8000)",
        "file_count": file_count,
        "user_count": user_count
    }), 200

@app.route('/ping')
def ping():
    return "pong", 200

def run_flask():
    """Run Flask in a separate thread (silent)."""
    port = int(os.environ.get('PORT', 10000))
    import warnings
    warnings.filterwarnings("ignore")
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)

# ================= ASYNCHRONOUS PG8000 DATABASE POOL =================
class AsyncPG8000Pool:
    """Simple async connection pool for pg8000 with SSL verification disabled."""
    def __init__(self, dsn, min_size=1, max_size=10):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool = asyncio.Queue()
        self._size = 0
        self._closed = False
        self._conn_params = parse_db_url(dsn)  # CRITICAL FIX: SSL disabled

    async def _create_conn(self):
        """Create a new async connection with SSL verification disabled."""
        conn = await pg8000.connect(**self._conn_params)
        return conn

    async def init(self):
        """Create initial connections."""
        for _ in range(self.min_size):
            conn = await self._create_conn()
            await self._pool.put(conn)
            self._size += 1

    async def acquire(self):
        """Get a connection from the pool."""
        if self._closed:
            raise RuntimeError("Pool is closed")
        try:
            conn = self._pool.get_nowait()
        except asyncio.QueueEmpty:
            if self._size < self.max_size:
                conn = await self._create_conn()
                self._size += 1
            else:
                conn = await self._pool.get()
        return conn

    async def release(self, conn):
        """Return connection to pool."""
        if self._closed:
            await conn.close()
        else:
            await self._pool.put(conn)

    async def close(self):
        """Close all connections."""
        self._closed = True
        while not self._pool.empty():
            conn = await self._pool.get()
            await conn.close()

class Database:
    def __init__(self, pool):
        self.pool = pool

    async def init_tables(self):
        """Create tables if they don't exist."""
        conn = await self.pool.acquire()
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT,
                    is_video INTEGER DEFAULT 0,
                    file_size INTEGER DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS membership_cache (
                    user_id BIGINT,
                    channel TEXT,
                    is_member INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, channel)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_deletions (
                    chat_id BIGINT NOT NULL,
                    message_id INTEGER NOT NULL,
                    scheduled_time TIMESTAMP NOT NULL,
                    delete_after INTEGER DEFAULT 600,
                    PRIMARY KEY (chat_id, message_id)
                )
            """)
            await conn.execute("""
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
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_files_timestamp ON files(timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_timestamp ON membership_cache(timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_deletions_time ON scheduled_deletions(scheduled_time)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_first_seen ON users(first_seen)")
        finally:
            await self.pool.release(conn)

    async def save_file(self, file_id: str, file_info: dict) -> str:
        conn = await self.pool.acquire()
        try:
            result = await conn.execute("""
                INSERT INTO files (file_id, file_name, mime_type, is_video, file_size, access_count)
                VALUES ($1, $2, $3, $4, $5, 0)
                RETURNING id
            """, file_id,
                file_info.get('file_name', ''),
                file_info.get('mime_type', ''),
                1 if file_info.get('is_video', False) else 0,
                file_info.get('size', 0)
            )
            new_id = str(result[0][0])
            log.info(f"💾 Saved file {new_id}: {file_info.get('file_name', '')}")
            return new_id
        finally:
            await self.pool.release(conn)

    async def get_file(self, file_id: str) -> Optional[dict]:
        conn = await self.pool.acquire()
        try:
            result = await conn.execute("""
                SELECT file_id, file_name, mime_type, is_video, file_size, timestamp, access_count
                FROM files WHERE id = $1
            """, int(file_id))
            if result:
                row = result[0]
                await conn.execute("UPDATE files SET access_count = access_count + 1 WHERE id = $1", int(file_id))
                return {
                    'file_id': row[0],
                    'file_name': row[1],
                    'mime_type': row[2],
                    'is_video': bool(row[3]),
                    'size': row[4],
                    'timestamp': row[5],
                    'access_count': row[6] + 1
                }
            return None
        finally:
            await self.pool.release(conn)

    async def cleanup_old_files(self):
        if AUTO_CLEANUP_DAYS <= 0:
            log.info("Auto-cleanup DISABLED. Files kept forever.")
            return
        conn = await self.pool.acquire()
        try:
            await conn.execute("""
                DELETE FROM files
                WHERE timestamp < NOW() - $1::INTERVAL
            """, f'{AUTO_CLEANUP_DAYS} days')
            await conn.execute("""
                DELETE FROM files
                WHERE id NOT IN (
                    SELECT id FROM files ORDER BY timestamp DESC LIMIT $1
                )
            """, MAX_STORED_FILES)
            log.info(f"Auto-cleanup performed")
        finally:
            await self.pool.release(conn)

    async def get_file_count(self) -> int:
        conn = await self.pool.acquire()
        try:
            result = await conn.execute("SELECT COUNT(*) FROM files")
            return result[0][0]
        finally:
            await self.pool.release(conn)

    async def cache_membership(self, user_id: int, channel: str, is_member: bool):
        conn = await self.pool.acquire()
        try:
            await conn.execute("""
                INSERT INTO membership_cache (user_id, channel, is_member, timestamp)
                VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, channel) DO UPDATE SET
                    is_member = EXCLUDED.is_member,
                    timestamp = CURRENT_TIMESTAMP
            """, user_id, channel, 1 if is_member else 0)
        finally:
            await self.pool.release(conn)

    async def get_cached_membership(self, user_id: int, channel: str) -> Optional[bool]:
        conn = await self.pool.acquire()
        try:
            result = await conn.execute("""
                SELECT is_member FROM membership_cache
                WHERE user_id = $1 AND channel = $2
                AND timestamp > NOW() - INTERVAL '5 minutes'
            """, user_id, channel)
            if result:
                return bool(result[0][0])
            return None
        finally:
            await self.pool.release(conn)

    async def clear_membership_cache(self, user_id: Optional[int] = None):
        conn = await self.pool.acquire()
        try:
            if user_id:
                await conn.execute("DELETE FROM membership_cache WHERE user_id = $1", user_id)
                log.info(f"Cleared cache for user {user_id}")
            else:
                await conn.execute("DELETE FROM membership_cache")
                log.info("Cleared all membership cache")
        finally:
            await self.pool.release(conn)

    async def delete_file(self, file_id: str) -> bool:
        conn = await self.pool.acquire()
        try:
            await conn.execute("DELETE FROM files WHERE id = $1", int(file_id))
            deleted = conn.rowcount > 0
            if deleted:
                log.info(f"🗑️ Deleted file {file_id}")
            return deleted
        finally:
            await self.pool.release(conn)

    async def get_all_files(self) -> list:
        conn = await self.pool.acquire()
        try:
            rows = await conn.execute("""
                SELECT id, file_name, is_video, file_size, timestamp, access_count
                FROM files ORDER BY timestamp DESC
            """)
            return [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]
        finally:
            await self.pool.release(conn)

    async def schedule_message_deletion(self, chat_id: int, message_id: int):
        scheduled_time = datetime.now() + timedelta(seconds=DELETE_AFTER)
        conn = await self.pool.acquire()
        try:
            await conn.execute("""
                INSERT INTO scheduled_deletions (chat_id, message_id, scheduled_time, delete_after)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (chat_id, message_id) DO UPDATE SET
                    scheduled_time = EXCLUDED.scheduled_time,
                    delete_after = EXCLUDED.delete_after
            """, chat_id, message_id, scheduled_time, DELETE_AFTER)
            log.info(f"Scheduled deletion for message {message_id} in chat {chat_id}")
        finally:
            await self.pool.release(conn)

    async def get_due_messages(self):
        conn = await self.pool.acquire()
        try:
            rows = await conn.execute("""
                SELECT chat_id, message_id FROM scheduled_deletions
                WHERE scheduled_time <= NOW()
            """)
            return [(r[0], r[1]) for r in rows]
        finally:
            await self.pool.release(conn)

    async def remove_scheduled_message(self, chat_id: int, message_id: int):
        conn = await self.pool.acquire()
        try:
            await conn.execute("DELETE FROM scheduled_deletions WHERE chat_id = $1 AND message_id = $2",
                               chat_id, message_id)
            log.info(f"Removed scheduled deletion for message {message_id}")
        finally:
            await self.pool.release(conn)

    async def update_user_interaction(self, user_id: int, username: str = None,
                                      first_name: str = None, last_name: str = None,
                                      file_accessed: bool = False):
        conn = await self.pool.acquire()
        try:
            await conn.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_active, total_interactions)
                VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
                ON CONFLICT (user_id) DO UPDATE SET
                    last_active = CURRENT_TIMESTAMP,
                    total_interactions = users.total_interactions + 1,
                    username = COALESCE(EXCLUDED.username, users.username),
                    first_name = COALESCE(EXCLUDED.first_name, users.first_name),
                    last_name = COALESCE(EXCLUDED.last_name, users.last_name)
            """, user_id, username, first_name, last_name)
            if file_accessed:
                await conn.execute("""
                    UPDATE users SET
                        total_files_accessed = total_files_accessed + 1,
                        last_file_accessed = CURRENT_TIMESTAMP
                    WHERE user_id = $1
                """, user_id)
        finally:
            await self.pool.release(conn)

    async def get_user_stats(self) -> Dict[str, Any]:
        conn = await self.pool.acquire()
        try:
            total_users = (await conn.execute("SELECT COUNT(*) FROM users"))[0][0]
            active_7d = (await conn.execute("SELECT COUNT(*) FROM users WHERE last_active > NOW() - INTERVAL '7 days'"))[0][0]
            active_30d = (await conn.execute("SELECT COUNT(*) FROM users WHERE last_active > NOW() - INTERVAL '30 days'"))[0][0]
            new_today = (await conn.execute("SELECT COUNT(*) FROM users WHERE DATE(first_seen) = CURRENT_DATE"))[0][0]
            new_week = (await conn.execute("SELECT COUNT(*) FROM users WHERE first_seen > NOW() - INTERVAL '7 days'"))[0][0]
            users_with_files = (await conn.execute("SELECT COUNT(DISTINCT user_id) FROM users WHERE total_files_accessed > 0"))[0][0]

            top_rows = await conn.execute("""
                SELECT user_id, username, first_name, last_name,
                       total_interactions, total_files_accessed,
                       last_active, first_seen
                FROM users
                ORDER BY total_interactions DESC
                LIMIT 10
            """)
            top_users = [
                (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7])
                for r in top_rows
            ]

            growth_rows = await conn.execute("""
                SELECT TO_CHAR(first_seen, 'YYYY-MM-DD') as date, COUNT(*) as new_users
                FROM users
                WHERE first_seen > NOW() - INTERVAL '30 days'
                GROUP BY date
                ORDER BY date DESC
                LIMIT 15
            """)
            growth_data = [(r[0], r[1]) for r in growth_rows]

            return {
                'total_users': total_users,
                'active_users_7d': active_7d,
                'active_users_30d': active_30d,
                'new_users_today': new_today,
                'new_users_week': new_week,
                'top_users': top_users,
                'users_with_files': users_with_files,
                'growth_data': growth_data
            }
        finally:
            await self.pool.release(conn)

    async def get_all_user_ids(self, exclude_admin: bool = True) -> List[int]:
        conn = await self.pool.acquire()
        try:
            if exclude_admin:
                rows = await conn.execute("SELECT user_id FROM users WHERE user_id != $1", ADMIN_ID)
            else:
                rows = await conn.execute("SELECT user_id FROM users")
            return [r[0] for r in rows]
        finally:
            await self.pool.release(conn)

    async def get_user_count(self) -> int:
        conn = await self.pool.acquire()
        try:
            return (await conn.execute("SELECT COUNT(*) FROM users"))[0][0]
        finally:
            await self.pool.release(conn)

# ================= BOT MESSAGE DELETION =================
async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        job = context.job
        chat_id = job.chat_id
        message_id = job.data
        if not chat_id or not message_id:
            return
        log.info(f"🗑️ Attempting to delete message {message_id} from chat {chat_id}")
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            log.info(f"✅ Deleted message {message_id}")
            await db.remove_scheduled_message(chat_id, message_id)
        except Exception as e:
            err = str(e).lower()
            if "message to delete not found" in err:
                await db.remove_scheduled_message(chat_id, message_id)
            elif "message can't be deleted" in err:
                log.warning(f"Can't delete message {message_id} – no permission")
            elif "chat not found" in err:
                await db.remove_scheduled_message(chat_id, message_id)
            else:
                log.error(f"Failed to delete message {message_id}: {e}")
    except Exception as e:
        log.error(f"Error in delete_message_job: {e}", exc_info=True)

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await db.schedule_message_deletion(chat_id, message_id)
        if context.job_queue:
            context.job_queue.run_once(
                delete_message_job,
                DELETE_AFTER,
                data=message_id,
                chat_id=chat_id,
                name=f"del_{chat_id}_{message_id}_{int(time.time())}"
            )
        log.info(f"Scheduled deletion of message {message_id} in {DELETE_AFTER}s")
    except Exception as e:
        log.error(f"Failed to schedule deletion: {e}")

async def cleanup_overdue_messages(context: ContextTypes.DEFAULT_TYPE):
    try:
        due = await db.get_due_messages()
        if not due:
            return
        log.info(f"Found {len(due)} overdue messages to clean up")
        for chat_id, message_id in due:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                log.info(f"✅ Cleanup deleted {message_id} from {chat_id}")
                await db.remove_scheduled_message(chat_id, message_id)
            except Exception as e:
                err = str(e).lower()
                if "message to delete not found" in err:
                    await db.remove_scheduled_message(chat_id, message_id)
                elif "message can't be deleted" in err:
                    log.warning(f"Cleanup: no permission for {message_id}")
                else:
                    log.error(f"Cleanup error: {e}")
    except Exception as e:
        log.error(f"Error in cleanup_overdue_messages: {e}")

# ================= MEMBERSHIP CHECK =================
async def check_user_in_channel(bot, channel: str, user_id: int, force_check: bool = False) -> bool:
    if not force_check:
        cached = await db.get_cached_membership(user_id, channel)
        if cached is not None:
            return cached
    try:
        chan = f"@{channel}" if not channel.startswith("@") else channel
        member = await bot.get_chat_member(chat_id=chan, user_id=user_id)
        is_member = member.status in ["member", "administrator", "creator"]
        await db.cache_membership(user_id, channel.replace("@", ""), is_member)
        return is_member
    except Exception as e:
        err = str(e).lower()
        if "user not found" in err or "user not participant" in err:
            await db.cache_membership(user_id, channel.replace("@", ""), False)
            return False
        if "chat not found" in err or "forbidden" in err:
            log.error(f"Bot cannot access @{channel} – check bot is admin")
            return True
        return True

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE, force_check: bool = False) -> Dict[str, Any]:
    if force_check:
        await db.clear_membership_cache(user_id)

    ch1 = await check_user_in_channel(context.bot, CHANNEL_1, user_id, force_check)
    ch2 = await check_user_in_channel(context.bot, CHANNEL_2, user_id, force_check)
    missing = []
    if not ch1:
        missing.append(f"@{CHANNEL_1}")
    if not ch2:
        missing.append(f"@{CHANNEL_2}")
    return {
        "channel1": ch1,
        "channel2": ch2,
        "all_joined": ch1 and ch2,
        "missing_channels": missing
    }

# ================= COMMAND HANDLERS =================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error(f"Error: {context.error}", exc_info=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user = update.effective_user
    chat_id = update.effective_chat.id
    args = context.args

    await db.update_user_interaction(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        file_accessed=False
    )

    if not args:
        kb = [
            [InlineKeyboardButton("📢 Join Channel 1", url=f"https://t.me/{CHANNEL_1}")],
            [InlineKeyboardButton("📢 Join Channel 2", url=f"https://t.me/{CHANNEL_2}")],
            [InlineKeyboardButton("🔄 Check Membership", callback_data="check_membership")]
        ]
        sent = await update.message.reply_text(
            "🤖 *Welcome to File Sharing Bot*\n\n"
            "1️⃣ Use admin‑provided links\n"
            "2️⃣ Join both channels\n"
            "3️⃣ Click 'Check Membership'\n\n"
            f"⚠️ Messages auto‑delete in {DELETE_AFTER//60} minutes\n"
            "💾 Files stored permanently in PostgreSQL",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return

    key = args[0]
    file_info = await db.get_file(key)
    if not file_info:
        sent = await update.message.reply_text("❌ File not found (may have been deleted).")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return

    result = await check_membership(user.id, context, force_check=True)
    if not result["all_joined"]:
        missing = result["missing_channels"]
        if len(missing) == 2:
            text = "⚠️ *You need to join both channels:*"
            kb = [
                [InlineKeyboardButton("📥 Join Channel 1", url=f"https://t.me/{CHANNEL_1}")],
                [InlineKeyboardButton("📥 Join Channel 2", url=f"https://t.me/{CHANNEL_2}")],
                [InlineKeyboardButton("✅ Check Again", callback_data=f"check|{key}")]
            ]
        else:
            chan = missing[0].replace("@", "")
            name = "Channel 1" if CHANNEL_1 in chan else "Channel 2"
            text = f"⚠️ *You need to join {name}:*"
            kb = [
                [InlineKeyboardButton(f"📥 Join {name}", url=f"https://t.me/{chan}")],
                [InlineKeyboardButton("✅ Check Again", callback_data=f"check|{key}")]
            ]
        sent = await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return

    await db.update_user_interaction(user_id=user.id, file_accessed=True)
    filename = file_info['file_name']
    ext = filename.split('.')[-1].lower() if '.' in filename else ""
    caption = f"🎬 *{filename}*" if file_info['is_video'] else f"📁 *{filename}*"
    caption += f"\n📥 Accessed {file_info['access_count']} times"
    caption += f"\n\n⚠️ *Auto‑delete in {DELETE_AFTER//60} minutes*"
    try:
        if file_info['is_video'] and ext in PLAYABLE_EXTS:
            sent = await context.bot.send_video(
                chat_id=chat_id,
                video=file_info["file_id"],
                caption=caption,
                parse_mode="Markdown",
                supports_streaming=True
            )
        else:
            sent = await context.bot.send_document(
                chat_id=chat_id,
                document=file_info["file_id"],
                caption=caption,
                parse_mode="Markdown"
            )
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
    except Exception as e:
        log.error(f"File send failed: {e}")
        sent = await update.message.reply_text("❌ Failed to send file. Try again later.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    await db.update_user_interaction(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )

    data = query.data
    if data == "check_membership":
        res = await check_membership(user.id, context, force_check=True)
        if res["all_joined"]:
            await query.edit_message_text(
                "✅ *Great! You've joined both channels!*\n\nNow you can use file links.",
                parse_mode="Markdown"
            )
        else:
            missing = res["missing_channels"]
            if len(missing) == 2:
                text = "❌ *Not a member of either channel.*"
                kb = [
                    [InlineKeyboardButton("📥 Join Channel 1", url=f"https://t.me/{CHANNEL_1}")],
                    [InlineKeyboardButton("📥 Join Channel 2", url=f"https://t.me/{CHANNEL_2}")],
                    [InlineKeyboardButton("🔄 Check Again", callback_data="check_membership")]
                ]
            else:
                chan = missing[0].replace("@", "")
                name = "Channel 1" if CHANNEL_1 in chan else "Channel 2"
                text = f"❌ *Missing {name}.*"
                kb = [
                    [InlineKeyboardButton(f"📥 Join {name}", url=f"https://t.me/{chan}")],
                    [InlineKeyboardButton("🔄 Check Again", callback_data="check_membership")]
                ]
            await query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        return

    if data.startswith("check|"):
        _, key = data.split("|")
        file_info = await db.get_file(key)
        if not file_info:
            await query.edit_message_text("❌ File not found.")
            return
        res = await check_membership(user.id, context, force_check=True)
        if not res["all_joined"]:
            missing = res["missing_channels"]
            if len(missing) == 2:
                text = "❌ *Still not joined both channels.*"
                kb = [
                    [InlineKeyboardButton("📥 Join Channel 1", url=f"https://t.me/{CHANNEL_1}")],
                    [InlineKeyboardButton("📥 Join Channel 2", url=f"https://t.me/{CHANNEL_2}")],
                    [InlineKeyboardButton("✅ Check Again", callback_data=f"check|{key}")]
                ]
            else:
                chan = missing[0].replace("@", "")
                name = "Channel 1" if CHANNEL_1 in chan else "Channel 2"
                text = f"❌ *Still not joined {name}.*"
                kb = [
                    [InlineKeyboardButton(f"📥 Join {name}", url=f"https://t.me/{chan}")],
                    [InlineKeyboardButton("✅ Check Again", callback_data=f"check|{key}")]
                ]
            await query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return

        await db.update_user_interaction(user_id=user.id, file_accessed=True)
        filename = file_info['file_name']
        ext = filename.split('.')[-1].lower() if '.' in filename else ""
        caption = f"🎬 *{filename}*" if file_info['is_video'] else f"📁 *{filename}*"
        caption += f"\n📥 Accessed {file_info['access_count']} times"
        caption += f"\n\n⚠️ *Auto‑delete in {DELETE_AFTER//60} minutes*"
        try:
            if file_info['is_video'] and ext in PLAYABLE_EXTS:
                sent = await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=file_info["file_id"],
                    caption=caption,
                    parse_mode="Markdown",
                    supports_streaming=True
                )
            else:
                sent = await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=file_info["file_id"],
                    caption=caption,
                    parse_mode="Markdown"
                )
            await query.edit_message_text("✅ *Access granted! File sent below.*", parse_mode="Markdown")
            await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        except Exception as e:
            log.error(f"Callback file send failed: {e}")
            await query.edit_message_text("❌ Failed to send file. Try again later.")

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    msg = update.message
    video = msg.video
    doc = msg.document
    if not video and not doc:
        sent = await msg.reply_text("❌ Send a video or document.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    file_id = None
    filename = None
    mime = None
    size = 0
    is_video = False
    if video:
        file_id = video.file_id
        filename = video.file_name or f"video_{int(time.time())}.mp4"
        mime = video.mime_type or "video/mp4"
        size = video.file_size or 0
        is_video = True
    else:
        filename = doc.file_name or f"doc_{int(time.time())}"
        file_id = doc.file_id
        mime = doc.mime_type or ""
        size = doc.file_size or 0
        ext = filename.split('.')[-1].lower() if '.' in filename else ''
        is_video = ext in ALL_VIDEO_EXTS
    info = {
        "file_name": filename,
        "mime_type": mime,
        "is_video": is_video,
        "size": size
    }
    key = await db.save_file(file_id, info)
    link = f"https://t.me/{bot_username}?start={key}"
    sent = await msg.reply_text(
        f"✅ *Upload Successful*\n\n"
        f"📁 *Name:* `{filename}`\n"
        f"🎬 *Type:* {'Video' if is_video else 'Document'}\n"
        f"📦 *Size:* {size/1024/1024:.1f} MB\n"
        f"🔑 *Key:* `{key}`\n"
        f"⏰ Auto‑delete: {DELETE_AFTER//60} min\n"
        f"💾 Storage: PostgreSQL (permanent)\n\n"
        f"🔗 *Link:*\n`{link}`",
        parse_mode="Markdown"
    )
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    uptime = str(timedelta(seconds=int(time.time() - start_time)))
    file_cnt = await db.get_file_count()
    user_cnt = await db.get_user_count()
    files = await db.get_all_files()
    total_access = sum(f[5] for f in files) if files else 0
    text = (
        f"📊 *Bot Statistics*\n\n"
        f"🤖 Bot: @{bot_username}\n"
        f"⏱ Uptime: {uptime}\n"
        f"📁 Files: {file_cnt}\n"
        f"👥 Users: {user_cnt}\n"
        f"👥 Accesses: {total_access}\n"
        f"🗄 Database: PostgreSQL (pg8000)\n"
        f"🧹 Auto‑cleanup: DISABLED\n"
        f"⏰ Message auto‑delete: {DELETE_AFTER//60} min\n\n"
        f"📢 Channels:\n1. @{CHANNEL_1}\n2. @{CHANNEL_2}\n\n"
        f"⚙️ Admin commands:\n/listfiles\n/deletefile <key>\n/users\n/broadcast\n/clearcache\n/testchannel"
    )
    sent = await update.message.reply_text(text, parse_mode="Markdown")
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def listfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    files = await db.get_all_files()
    if not files:
        sent = await update.message.reply_text("📁 No files stored.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    total_size = sum(f[3] for f in files if f[3]) / (1024*1024*1024)
    total_access = sum(f[5] for f in files)
    lines = []
    for f in files[:50]:
        fid, name, is_vid, size, ts, acc = f
        size_mb = size/(1024*1024) if size else 0
        date = ts.strftime("%b %d, %Y") if ts else "?"
        lines.append(
            f"🔑 `{fid}`\n📁 {name[:30]}{'…' if len(name)>30 else ''}\n"
            f"🎬 {'Video' if is_vid else 'Doc'} • {size_mb:.1f}MB • 📅 {date} • 👥 {acc}x\n"
        )
    summary = (
        f"📊 *Database Summary*\n"
        f"• Total files: {len(files)}\n"
        f"• Total size: {total_size:.2f} GB\n"
        f"• Total accesses: {total_access}\n"
        f"• Storage: PostgreSQL (permanent)\n\n"
        f"📋 *Files (first 50):*\n"
    )
    full = summary + "\n".join(lines)
    if len(full) > 4000:
        sent1 = await update.message.reply_text(full[:4000], parse_mode="Markdown")
        sent2 = await update.message.reply_text(full[4000:], parse_mode="Markdown")
        await schedule_message_deletion(context, sent1.chat_id, sent1.message_id)
        await schedule_message_deletion(context, sent2.chat_id, sent2.message_id)
    else:
        sent = await update.message.reply_text(full, parse_mode="Markdown")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def deletefile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        sent = await update.message.reply_text("Usage: /deletefile <key>")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    key = context.args[0]
    file_info = await db.get_file(key)
    if not file_info:
        sent = await update.message.reply_text(f"❌ File {key} not found.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    if await db.delete_file(key):
        sent = await update.message.reply_text(f"✅ File {key} deleted.")
    else:
        sent = await update.message.reply_text(f"❌ Failed to delete {key}.")
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    stats_data = await db.get_user_stats()
    top_text = ""
    for i, u in enumerate(stats_data['top_users'], 1):
        uid, uname, fn, ln, inter, files_acc, last, first = u
        name = f"{fn} {ln}".strip() or (f"@{uname}" if uname else f"User {uid}")
        last_str = last.strftime("%b %d") if last else "?"
        top_text += f"{i}. {name[:20]}{'…' if len(name)>20 else ''}\n"
        top_text += f"   👤 ID: {uid} | 🔢 {inter} int | 📁 {files_acc} files\n"
        top_text += f"   🕐 Last: {last_str}\n"
    growth_text = ""
    for date_str, cnt in stats_data['growth_data'][:7]:
        growth_text += f"📅 {date_str}: +{cnt} users\n"
    msg = (
        f"📊 *USER STATISTICS*\n\n"
        f"👥 Total Users: {stats_data['total_users']}\n"
        f"🟢 Active (7d): {stats_data['active_users_7d']}\n"
        f"🟡 Active (30d): {stats_data['active_users_30d']}\n"
        f"📈 New Today: {stats_data['new_users_today']}\n"
        f"📈 New Week: {stats_data['new_users_week']}\n"
        f"📁 Users with files: {stats_data['users_with_files']}\n\n"
        f"🏆 *TOP 10 USERS*\n{top_text}\n"
        f"📈 *RECENT GROWTH*\n{growth_text}"
    )
    sent = await update.message.reply_text(msg, parse_mode="Markdown")
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args and not (update.message.reply_to_message and update.message.reply_to_message.text):
        sent = await update.message.reply_text(
            "📢 *Broadcast*\nUsage:\n`/broadcast your message`\nor reply to a message with `/broadcast`\n\nOptions:\n`-silent` no notification\n`-test` send to yourself only",
            parse_mode="Markdown"
        )
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    args = context.args or []
    silent = False
    test = False
    if args and args[0] in ('-silent', '-s'):
        silent = True
        args = args[1:]
    if args and args[0] in ('-test', '-t'):
        test = True
        args = args[1:]
    if update.message.reply_to_message:
        text = update.message.reply_to_message.text or update.message.reply_to_message.caption
        if not text:
            sent = await update.message.reply_text("❌ Replied message has no text.")
            await schedule_message_deletion(context, sent.chat_id, sent.message_id)
            return
    else:
        text = " ".join(args)
    if not text.strip():
        sent = await update.message.reply_text("❌ Message cannot be empty.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        return
    if not silent:
        text = f"📢 *Broadcast from @{bot_username}*\n\n{text}"
    if test:
        targets = [update.effective_user.id]
        status = await update.message.reply_text("🔄 *TEST MODE* – sending to yourself...", parse_mode="Markdown")
    else:
        targets = await db.get_all_user_ids(exclude_admin=True)
        status = await update.message.reply_text(
            f"🔄 *BROADCAST STARTED*\n\n📤 Sending to {len(targets)} users...",
            parse_mode="Markdown"
        )
    success = 0
    failed = 0
    blocked = 0
    for i, uid in enumerate(targets):
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode="Markdown" if not silent else None,
                disable_notification=silent
            )
            success += 1
        except Exception as e:
            err = str(e).lower()
            if 'blocked' in err or 'forbidden' in err:
                blocked += 1
            else:
                failed += 1
        if (i+1) % 20 == 0 and not test:
            await status.edit_text(
                f"🔄 *PROGRESS*\n✅ {success} | ❌ {failed} | 🚫 {blocked} | 📤 {i+1}/{len(targets)}",
                parse_mode="Markdown"
            )
        await asyncio.sleep(0.1)
    report = (
        f"✅ *BROADCAST COMPLETED*\n\n"
        f"✅ Success: {success}\n❌ Failed: {failed}\n🚫 Blocked: {blocked}\n📤 Total: {len(targets)}\n\n"
        f"📝 *Preview:*\n{text[:200]}{'…' if len(text)>200 else ''}"
    )
    await status.edit_text(report, parse_mode="Markdown")
    await schedule_message_deletion(context, status.chat_id, status.message_id)

async def clearcache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if context.args:
        try:
            uid = int(context.args[0])
            await db.clear_membership_cache(uid)
            sent = await update.message.reply_text(f"✅ Cleared cache for user {uid}")
        except ValueError:
            sent = await update.message.reply_text("Usage: /clearcache [user_id]")
    else:
        await db.clear_membership_cache()
        sent = await update.message.reply_text("✅ Cleared all membership cache")
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def testchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    uid = update.effective_user.id
    try:
        m1 = await context.bot.get_chat_member(f"@{CHANNEL_1}", uid)
        s1 = f"✅ {m1.status}"
    except Exception as e:
        s1 = f"❌ {str(e)[:50]}"
    try:
        m2 = await context.bot.get_chat_member(f"@{CHANNEL_2}", uid)
        s2 = f"✅ {m2.status}"
    except Exception as e:
        s2 = f"❌ {str(e)[:50]}"
    sent = await update.message.reply_text(f"🔍 *Channel Access Test*\n\n@{CHANNEL_1}: {s1}\n\n@{CHANNEL_2}: {s2}", parse_mode="Markdown")
    await schedule_message_deletion(context, sent.chat_id, sent.message_id)

async def cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    days = 30
    if context.args:
        try:
            days = int(context.args[0])
            if days <= 0:
                sent = await update.message.reply_text("✅ Cleanup cancelled.")
                await schedule_message_deletion(context, sent.chat_id, sent.message_id)
                return
        except:
            sent = await update.message.reply_text("Usage: /cleanup [days]")
            await schedule_message_deletion(context, sent.chat_id, sent.message_id)
            return
    conn = await db.pool.acquire()
    try:
        await conn.execute("DELETE FROM files WHERE timestamp < NOW() - $1::INTERVAL", f'{days} days')
        deleted = conn.rowcount
        sent = await update.message.reply_text(f"🧹 Manual cleanup: removed {deleted} files older than {days} days.")
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
    finally:
        await db.pool.release(conn)

# ================= BOT INITIALISATION =================
db = None

async def init_application():
    global bot_username
    app_bot = Application.builder().token(BOT_TOKEN).build()
    me = await app_bot.bot.get_me()
    bot_username = me.username
    app_bot.add_error_handler(error_handler)
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("cleanup", cleanup))
    app_bot.add_handler(CommandHandler("stats", stats))
    app_bot.add_handler(CommandHandler("clearcache", clearcache))
    app_bot.add_handler(CommandHandler("testchannel", testchannel))
    app_bot.add_handler(CommandHandler("listfiles", listfiles))
    app_bot.add_handler(CommandHandler("deletefile", deletefile))
    app_bot.add_handler(CommandHandler("users", users))
    app_bot.add_handler(CommandHandler("broadcast", broadcast))
    app_bot.add_handler(CallbackQueryHandler(check_join, pattern=r"^check_membership$"))
    app_bot.add_handler(CallbackQueryHandler(check_join, pattern=r"^check\|"))
    app_bot.add_handler(MessageHandler(
        filters.VIDEO | filters.Document.ALL & filters.User(ADMIN_ID) & filters.ChatType.PRIVATE,
        upload
    ))
    if app_bot.job_queue:
        app_bot.job_queue.run_repeating(cleanup_overdue_messages, interval=300, first=10)
    return app_bot

async def main():
    global db
    print("🚀 Starting bot with PostgreSQL (pg8000) - SSL verification disabled...")
    
    # CRITICAL FIX: Create pool with SSL verification disabled
    pool = AsyncPG8000Pool(DATABASE_URL, min_size=1, max_size=10)
    await pool.init()
    
    db = Database(pool)
    await db.init_tables()
    
    file_cnt = await db.get_file_count()
    user_cnt = await db.get_user_count()
    log.info(f"📊 Files: {file_cnt}, Users: {user_cnt}")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    log.info(f"🟢 Flask running on port {os.environ.get('PORT', 10000)}")

    application = await init_application()
    log.info("🟢 Bot application built")

    await application.initialize()
    await application.updater.start_polling()
    await application.start()
    log.info("🟢 Bot is polling")

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        await application.stop()
        await application.shutdown()
        await pool.close()

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN not set")
        sys.exit(1)
    if not ADMIN_ID:
        print("❌ ERROR: ADMIN_ID not set")
        sys.exit(1)
    asyncio.run(main())
