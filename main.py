import asyncio
import json
import logging
import os
import sys
import time
import ssl
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
import threading
import pg8000
from contextlib import asynccontextmanager
import urllib.parse
import functools

# ================= HEALTH SERVER FOR RENDER =================
from flask import Flask, render_template_string, jsonify, request
import threading

app = Flask(__name__)

# Global variables for web dashboard
start_time = time.time()
bot_username = "xoticcroissant_bot"
db_initialized = False
application = None 
# ===========================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    JobQueue
)

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

# Channel usernames (without @)
CHANNEL_1 = os.environ.get("CHANNEL_1", "A_Knight_of_the_Seven_Kingdoms_t").replace("@", "")
CHANNEL_2 = os.environ.get("CHANNEL_2", "your_movies_web").replace("@", "")

# ============ 🔥 RENDER POSTGRESQL ============
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL is not set!")
    raise ValueError("DATABASE_URL environment variable is required!")

# ============ POLLING MODE (NO WEBHOOK) ============
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip('/')
PORT = int(os.environ.get('PORT', 10000))

DELETE_AFTER = 600  # 10 minutes
MAX_STORED_FILES = 10000
AUTO_CLEANUP_DAYS = 0

# Playable formats
PLAYABLE_EXTS = {"mp4", "mov", "m4v", "mpeg", "mpg"}
ALL_VIDEO_EXTS = {
    "mp4", "mkv", "mov", "avi", "webm", "flv", "m4v", 
    "3gp", "wmv", "mpg", "mpeg"
}

# =========================================

# Simple logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

# ================= DATABASE WITH BETTER ERROR HANDLING =================

class Database:
    def __init__(self, db_url: str = DATABASE_URL):
        self.db_url = db_url
        self.connection = None
        self.connection_lock = threading.Lock()
        self.connection_params = self.parse_db_url(db_url)
        self.initialized = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        log.info(f"📀 Database config - Host: {self.connection_params['host']}, DB: {self.connection_params['database']}")
    
    def parse_db_url(self, db_url):
        """Parse DATABASE_URL and return connection parameters"""
        try:
            db_string = db_url.replace("postgresql://", "").replace("postgres://", "")
            user_pass, host_port_db = db_string.split("@", 1)
            user, password = user_pass.split(":", 1)
            
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
            
            password = urllib.parse.unquote(password)
            
            return {
                'user': user,
                'password': password,
                'host': host,
                'port': port,
                'database': database
            }
        except Exception as e:
            log.error(f"Failed to parse DATABASE_URL: {e}")
            raise
    
    def create_ssl_context(self):
        """Create SSL context that accepts self-signed certificates"""
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context
    
    def connect_to_db_sync(self, database=None):
        """Synchronous connection to database"""
        params = self.connection_params.copy()
        if database:
            params['database'] = database
        
        ssl_context = self.create_ssl_context()
        
        log.info(f"🔌 Connecting to {params['host']}:{params['port']}/{params['database']}")
        
        return pg8000.connect(
            user=params['user'],
            password=params['password'],
            host=params['host'],
            port=params['port'],
            database=params['database'],
            ssl_context=ssl_context,
            timeout=10  # Shorter timeout for faster failure
        )
    
    async def connect_to_db(self, database=None):
        """Async wrapper for database connection"""
        return await asyncio.to_thread(self.connect_to_db_sync, database)
    
    async def get_connection(self):
        """Get or create database connection with retry logic"""
        with self.connection_lock:
            if self.connection is None:
                try:
                    # Try to connect directly
                    self.connection = await self.connect_to_db()
                    log.info("✅ Database connection established")
                    
                    # Initialize tables
                    await self.init_db()
                    
                    count = await self.get_file_count()
                    log.info(f"📊 Database ready with {count} files")
                    self.initialized = True
                    self.reconnect_attempts = 0
                    
                except Exception as e:
                    log.error(f"❌ Database connection failed: {e}")
                    self.reconnect_attempts += 1
                    
                    # Don't raise - let bot continue without DB if needed
                    log.warning("⚠️ Bot will run with limited functionality until DB connects")
                    return None
            
            return self.connection
    
    async def execute(self, query: str, params: tuple = None):
        """Execute a query and return cursor"""
        conn = await self.get_connection()
        if conn is None:
            return None
        
        def _execute():
            try:
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                return cursor
            except Exception as e:
                log.error(f"Error executing query: {e}")
                # Try to reconnect on next attempt
                self.connection = None
                self.initialized = False
                raise
        
        return await asyncio.to_thread(_execute)
    
    async def fetchrow(self, query: str, params: tuple = None):
        """Fetch one row"""
        cursor = await self.execute(query, params)
        if cursor is None:
            return None
        return await asyncio.to_thread(cursor.fetchone)
    
    async def fetchall(self, query: str, params: tuple = None):
        """Fetch all rows"""
        cursor = await self.execute(query, params)
        if cursor is None:
            return []
        return await asyncio.to_thread(cursor.fetchall)
    
    async def execute_and_commit(self, query: str, params: tuple = None):
        """Execute query and commit"""
        cursor = await self.execute(query, params)
        if cursor is None:
            return 0
        
        conn = await self.get_connection()
        
        def _commit():
            conn.commit()
            return cursor.rowcount
        
        return await asyncio.to_thread(_commit)
    
    async def init_db(self):
        """Initialize database with required tables"""
        try:
            # Files table
            await self.execute_and_commit('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT,
                    is_video INTEGER DEFAULT 0,
                    file_size BIGINT DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 0
                )
            ''')
            
            # Membership cache
            await self.execute_and_commit('''
                CREATE TABLE IF NOT EXISTS membership_cache (
                    user_id BIGINT,
                    channel TEXT,
                    is_member INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, channel)
                )
            ''')
            
            # Scheduled deletions
            await self.execute_and_commit('''
                CREATE TABLE IF NOT EXISTS scheduled_deletions (
                    chat_id BIGINT NOT NULL,
                    message_id INTEGER NOT NULL,
                    scheduled_time TIMESTAMP NOT NULL,
                    delete_after INTEGER DEFAULT 600,
                    PRIMARY KEY (chat_id, message_id)
                )
            ''')
            
            # Users table
            await self.execute_and_commit('''
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
            
            # Indexes
            await self.execute_and_commit('CREATE INDEX IF NOT EXISTS idx_files_timestamp ON files(timestamp)')
            await self.execute_and_commit('CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active)')
            
        except Exception as e:
            log.error(f"Error initializing database: {e}")
            raise
    
    async def save_file(self, file_id: str, file_info: dict) -> str:
        """Save file info and return generated ID"""
        try:
            result = await self.fetchrow('''
                INSERT INTO files
                (file_id, file_name, mime_type, is_video, file_size, access_count)
                VALUES ($1, $2, $3, $4, $5, 0)
                RETURNING id
            ''', (
                file_id,
                file_info.get('file_name', ''),
                file_info.get('mime_type', ''),
                1 if file_info.get('is_video', False) else 0,
                file_info.get('size', 0)
            ))
            if result:
                conn = await self.get_connection()
                await asyncio.to_thread(conn.commit)
                return str(result[0])
            return None
        except Exception as e:
            log.error(f"Error saving file: {e}")
            return None

    async def get_file(self, file_id: str) -> Optional[dict]:
        """Get file info by ID"""
        try:
            file_id_int = int(file_id)
        except ValueError:
            return None
        
        try:
            result = await self.fetchrow('''
                UPDATE files
                SET access_count = access_count + 1
                WHERE id = $1
                RETURNING file_id, file_name, mime_type, is_video, file_size, 
                          TO_CHAR(timestamp, 'YYYY-MM-DD HH24:MI:SS') as timestamp, 
                          access_count
            ''', (file_id_int,))
            
            if result:
                conn = await self.get_connection()
                await asyncio.to_thread(conn.commit)
                return {
                    'file_id': result[0],
                    'file_name': result[1],
                    'mime_type': result[2],
                    'is_video': bool(result[3]),
                    'size': result[4],
                    'timestamp': result[5],
                    'access_count': result[6]
                }
        except Exception as e:
            log.error(f"Error getting file: {e}")
        return None
    
    async def get_file_count(self) -> int:
        """Get total number of files"""
        try:
            result = await self.fetchrow("SELECT COUNT(*) FROM files")
            return result[0] if result else 0
        except:
            return 0
    
    async def cache_membership(self, user_id: int, channel: str, is_member: bool):
        """Cache membership check result"""
        try:
            await self.execute_and_commit('''
                INSERT INTO membership_cache (user_id, channel, is_member, timestamp)
                VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, channel) DO UPDATE
                SET is_member = EXCLUDED.is_member,
                    timestamp = EXCLUDED.timestamp
            ''', (user_id, channel, 1 if is_member else 0))
        except:
            pass
    
    async def get_cached_membership(self, user_id: int, channel: str) -> Optional[bool]:
        """Get cached membership result"""
        try:
            result = await self.fetchrow('''
                SELECT is_member FROM membership_cache 
                WHERE user_id = $1 AND channel = $2 
                AND timestamp > CURRENT_TIMESTAMP - INTERVAL '5 minutes'
            ''', (user_id, channel))
            return bool(result[0]) if result else None
        except:
            return None

    async def clear_membership_cache(self, user_id: Optional[int] = None):
        """Clear membership cache"""
        try:
            if user_id:
                await self.execute_and_commit("DELETE FROM membership_cache WHERE user_id = $1", (user_id,))
            else:
                await self.execute_and_commit("DELETE FROM membership_cache")
        except:
            pass

    async def delete_file(self, file_id: str) -> bool:
        """Manually delete a file from database"""
        try:
            file_id_int = int(file_id)
            rowcount = await self.execute_and_commit("DELETE FROM files WHERE id = $1", (file_id_int,))
            return rowcount > 0
        except:
            return False

    async def get_all_files(self) -> list:
        """Get all files for admin view"""
        try:
            rows = await self.fetchall('''
                SELECT id, file_name, is_video, file_size, 
                       TO_CHAR(timestamp, 'YYYY-MM-DD HH24:MI:SS') as timestamp, 
                       access_count 
                FROM files 
                ORDER BY timestamp DESC
            ''')
            return [(row[0], row[1], row[2], row[3], row[4], row[5]) for row in rows]
        except:
            return []
    
    async def schedule_message_deletion(self, chat_id: int, message_id: int):
        """Schedule a message for deletion"""
        try:
            scheduled_time = datetime.now() + timedelta(seconds=DELETE_AFTER)
            await self.execute_and_commit('''
                INSERT INTO scheduled_deletions (chat_id, message_id, scheduled_time, delete_after)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (chat_id, message_id) DO UPDATE
                SET scheduled_time = EXCLUDED.scheduled_time,
                    delete_after = EXCLUDED.delete_after
            ''', (chat_id, message_id, scheduled_time, DELETE_AFTER))
        except:
            pass
    
    async def get_due_messages(self):
        """Get messages that are due for deletion"""
        try:
            rows = await self.fetchall('''
                SELECT chat_id, message_id FROM scheduled_deletions 
                WHERE scheduled_time <= CURRENT_TIMESTAMP
            ''')
            return [(row[0], row[1]) for row in rows]
        except:
            return []
    
    async def remove_scheduled_message(self, chat_id: int, message_id: int):
        """Remove message from scheduled deletions"""
        try:
            await self.execute_and_commit(
                'DELETE FROM scheduled_deletions WHERE chat_id = $1 AND message_id = $2',
                (chat_id, message_id)
            )
        except:
            pass

    async def update_user_interaction(self, user_id: int, username: str = None, 
                                    first_name: str = None, last_name: str = None,
                                    file_accessed: bool = False):
        """Update user interaction"""
        try:
            exists = await self.fetchrow("SELECT 1 FROM users WHERE user_id = $1", (user_id,))
            
            if exists:
                await self.execute_and_commit('''
                    UPDATE users 
                    SET last_active = CURRENT_TIMESTAMP,
                        total_interactions = total_interactions + 1,
                        username = COALESCE($1, username),
                        first_name = COALESCE($2, first_name),
                        last_name = COALESCE($3, last_name)
                    WHERE user_id = $4
                ''', (username, first_name, last_name, user_id))
                
                if file_accessed:
                    await self.execute_and_commit('''
                        UPDATE users 
                        SET total_files_accessed = total_files_accessed + 1,
                            last_file_accessed = CURRENT_TIMESTAMP
                        WHERE user_id = $1
                    ''', (user_id,))
            else:
                await self.execute_and_commit('''
                    INSERT INTO users 
                    (user_id, username, first_name, last_name, first_seen, last_active, total_interactions)
                    VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
                ''', (user_id, username, first_name, last_name))
        except Exception as e:
            log.error(f"Error updating user: {e}")
    
    async def get_user_count(self) -> int:
        """Get total number of users"""
        try:
            result = await self.fetchrow("SELECT COUNT(*) FROM users")
            return result[0] if result else 0
        except:
            return 0
    
    async def get_all_user_ids(self, exclude_admin: bool = True) -> List[int]:
        """Get all user IDs for broadcasting"""
        try:
            if exclude_admin:
                rows = await self.fetchall("SELECT user_id FROM users WHERE user_id != $1", (ADMIN_ID,))
            else:
                rows = await self.fetchall("SELECT user_id FROM users")
            return [row[0] for row in rows] if rows else []
        except:
            return []
    
    def get_sync_connection(self):
        """Get a synchronous connection for Flask routes"""
        try:
            if not self.initialized:
                return None
            return self.connect_to_db_sync()
        except:
            return None
    
    async def close(self):
        """Close database connection"""
        if self.connection:
            await asyncio.to_thread(self.connection.close)
            self.connection = None

# Initialize database
db = Database()

# ============ MESSAGE DELETION SYSTEM ============
async def delete_message_job(context):
    """Delete message after timer"""
    try:
        job = context.job
        chat_id = job.chat_id
        message_id = job.data
        
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            await db.remove_scheduled_message(chat_id, message_id)
        except:
            await db.remove_scheduled_message(chat_id, message_id)
    except Exception as e:
        log.error(f"Error in delete_message_job: {e}")

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Schedule a message for deletion"""
    try:
        await db.schedule_message_deletion(chat_id, message_id)
        if context.job_queue:
            context.job_queue.run_once(
                delete_message_job,
                DELETE_AFTER,
                data=message_id,
                chat_id=chat_id,
                name=f"delete_msg_{chat_id}_{message_id}"
            )
    except Exception as e:
        log.error(f"Failed to schedule deletion: {e}")

# ============ MEMBERSHIP CHECK ============
async def check_user_in_channel(bot, channel: str, user_id: int, force_check: bool = False) -> bool:
    """Check if user is in channel"""
    if not channel:
        return True
        
    if not force_check:
        cached = await db.get_cached_membership(user_id, channel)
        if cached is not None:
            return cached
    
    try:
        channel_username = f"@{channel}" if not channel.startswith("@") else channel
        member = await bot.get_chat_member(chat_id=channel_username, user_id=user_id)
        is_member = member.status in ["member", "administrator", "creator"]
        await db.cache_membership(user_id, channel, is_member)
        return is_member
    except Exception as e:
        error_msg = str(e).lower()
        if "user not found" in error_msg or "user not participant" in error_msg:
            await db.cache_membership(user_id, channel, False)
            return False
        return True  # Allow access if bot can't check

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE, force_check: bool = False) -> Dict[str, Any]:
    """Check if user is member of both channels"""
    result = {
        "channel1": False,
        "channel2": False,
        "all_joined": False,
        "missing_channels": []
    }
    
    if CHANNEL_1:
        result["channel1"] = await check_user_in_channel(context.bot, CHANNEL_1, user_id, force_check)
        if not result["channel1"]:
            result["missing_channels"].append(f"@{CHANNEL_1}")
    
    if CHANNEL_2:
        result["channel2"] = await check_user_in_channel(context.bot, CHANNEL_2, user_id, force_check)
        if not result["channel2"]:
            result["missing_channels"].append(f"@{CHANNEL_2}")
    
    result["all_joined"] = (not CHANNEL_1 or result["channel1"]) and (not CHANNEL_2 or result["channel2"])
    return result

# ============ FLASK DASHBOARD ============

def get_db_stats_sync():
    """Get database stats synchronously for Flask routes"""
    try:
        conn = db.get_sync_connection()
        if not conn:
            return 0, 0
        
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files")
        file_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        conn.close()
        return file_count, user_count
    except:
        return 0, 0

@app.route('/')
def home():
    uptime_seconds = time.time() - start_time
    uptime_str = str(timedelta(seconds=int(uptime_seconds)))
    file_count, user_count = get_db_stats_sync()
    
    html_content = """
    <!DOCTYPE html>
<html>
<head>
    <title>🤖 Telegram File Bot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
            margin: 0; 
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            min-height: 100vh;
        }
        .container { 
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
        }
        h1 { color: white; margin-top: 0; }
        .status { 
            background: rgba(0, 255, 0, 0.2); 
            padding: 15px; 
            border-radius: 8px; 
            margin: 10px 0;
            border-left: 4px solid #00ff00;
        }
        .info { 
            background: rgba(255, 255, 255, 0.1);
            padding: 15px;
            border-radius: 8px;
            margin: 10px 0;
        }
        .btn {
            display: inline-block;
            background: #4CAF50;
            color: white;
            padding: 10px 20px;
            border-radius: 6px;
            text-decoration: none;
            margin: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Telegram File Bot</h1>
        <div class="status">
            <h3>✅ Status: <strong>ACTIVE (Polling Mode)</strong></h3>
            <p>Bot is running on Render with PostgreSQL</p>
            <p>Uptime: {{ uptime }}</p>
            <p>Files in DB: {{ file_count }}</p>
            <p>Users in DB: {{ user_count }}</p>
        </div>
        
        <div class="info">
            <h3>📊 Bot Information</h3>
            <ul>
                <li>Bot: <strong>@{{ bot_username }}</strong></li>
                <li>Mode: <strong>Polling (No Webhook)</strong></li>
                <li>Message Auto-delete: <strong>{{ delete_minutes }} minutes</strong></li>
            </ul>
        </div>
        
        <div class="info">
            <h3>📞 Start Bot</h3>
            <p><a href="https://t.me/{{ bot_username }}" target="_blank" class="btn">Start @{{ bot_username }}</a></p>
        </div>
    </div>
</body>
</html>
    """
    
    return render_template_string(html_content, 
                                  bot_username=bot_username,
                                  uptime=uptime_str,
                                  file_count=file_count,
                                  user_count=user_count,
                                  delete_minutes=DELETE_AFTER//60)

@app.route('/health')
def health():
    file_count, user_count = get_db_stats_sync()
    return jsonify({
        "status": "OK",
        "mode": "polling",
        "uptime": str(timedelta(seconds=int(time.time() - start_time))),
        "file_count": file_count,
        "user_count": user_count,
        "db_initialized": db.initialized
    })

@app.route('/ping')
def ping():
    return "pong"

def run_flask_thread():
    """Run Flask server in a thread"""
    port = int(os.environ.get('FLASK_PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)

# ============ COMMAND HANDLERS ============
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Error handler"""
    log.error(f"Error: {context.error}", exc_info=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    try:
        user_id = update.effective_user.id
        user = update.effective_user
        
        # Update user interaction (don't await if DB fails)
        try:
            await db.update_user_interaction(
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name
            )
        except:
            pass

        args = context.args
        
        # Welcome message
        if not args:
            keyboard = []
            if CHANNEL_1:
                keyboard.append([InlineKeyboardButton("📢 Join Channel 1", url=f"https://t.me/{CHANNEL_1}")])
            if CHANNEL_2:
                keyboard.append([InlineKeyboardButton("📢 Join Channel 2", url=f"https://t.me/{CHANNEL_2}")])
            keyboard.append([InlineKeyboardButton("🔄 Check Membership", callback_data="check_membership")])

            sent_msg = await update.message.reply_text(
                "🤖 *Welcome to File Sharing Bot*\n\n"
                "🔗 Use admin-provided links to access files\n"
                f"⚠️ Messages auto-delete after {DELETE_AFTER//60} minutes",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
            await schedule_message_deletion(context, sent_msg.chat_id, sent_msg.message_id)
            return

        # File access
        key = args[0]
        file_info = await db.get_file(key)
        
        if not file_info:
            sent_msg = await update.message.reply_text("❌ File not found")
            await schedule_message_deletion(context, sent_msg.chat_id, sent_msg.message_id)
            return

        # Check membership
        result = await check_membership(user_id, context)
        
        if not result["all_joined"]:
            keyboard = []
            for channel in result["missing_channels"]:
                keyboard.append([InlineKeyboardButton(f"📥 Join {channel}", url=f"https://t.me/{channel.replace('@', '')}")])
            keyboard.append([InlineKeyboardButton("✅ Check Again", callback_data=f"check|{key}")])
            
            sent_msg = await update.message.reply_text(
                "🔒 *Join required channels to access this file*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await schedule_message_deletion(context, sent_msg.chat_id, sent_msg.message_id)
            return

        # Send file
        try:
            await db.update_user_interaction(user_id=user_id, file_accessed=True)
        except:
            pass
            
        filename = file_info['file_name']
        ext = filename.lower().split('.')[-1] if '.' in filename else ""
        
        caption = f"📁 *{filename}*\n📥 Views: {file_info['access_count']}"
        
        if file_info['is_video'] and ext in PLAYABLE_EXTS:
            sent = await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=file_info["file_id"],
                caption=caption,
                parse_mode="Markdown",
                supports_streaming=True
            )
        else:
            sent = await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_info["file_id"],
                caption=caption,
                parse_mode="Markdown"
            )
        
        await schedule_message_deletion(context, sent.chat_id, sent.message_id)
        
    except Exception as e:
        log.error(f"Start error: {e}")

async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        # Update user
        user = query.from_user
        try:
            await db.update_user_interaction(
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name
            )
        except:
            pass
        
        if data == "check_membership":
            result = await check_membership(user_id, context, force_check=True)
            
            if result["all_joined"]:
                await query.edit_message_text(
                    "✅ *You've joined all channels!*\n\nUse file links from admin.",
                    parse_mode="Markdown"
                )
            else:
                keyboard = []
                for channel in result["missing_channels"]:
                    keyboard.append([InlineKeyboardButton(f"📥 Join {channel}", url=f"https://t.me/{channel.replace('@', '')}")])
                keyboard.append([InlineKeyboardButton("🔄 Check Again", callback_data="check_membership")])
                
                await query.edit_message_text(
                    "❌ *Missing channels:*\n" + "\n".join(result["missing_channels"]),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            return
        
        if data.startswith("check|"):
            _, key = data.split("|")
            file_info = await db.get_file(key)
            
            if not file_info:
                await query.edit_message_text("❌ File not found")
                return
            
            result = await check_membership(user_id, context, force_check=True)
            
            if not result['all_joined']:
                keyboard = []
                for channel in result["missing_channels"]:
                    keyboard.append([InlineKeyboardButton(f"📥 Join {channel}", url=f"https://t.me/{channel.replace('@', '')}")])
                keyboard.append([InlineKeyboardButton("✅ Check Again", callback_data=f"check|{key}")])
                
                await query.edit_message_text(
                    "🔒 *Join required channels*",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            # Send file
            try:
                await db.update_user_interaction(user_id=user_id, file_accessed=True)
            except:
                pass
                
            filename = file_info['file_name']
            ext = filename.lower().split('.')[-1] if '.' in filename else ""
            caption = f"📁 *{filename}*\n📥 Views: {file_info['access_count']}"
            
            if file_info['is_video'] and ext in PLAYABLE_EXTS:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=file_info["file_id"],
                    caption=caption,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=file_info["file_id"],
                    caption=caption,
                    parse_mode="Markdown"
                )
            
            await query.edit_message_text("✅ File sent below!")
        
    except Exception as e:
        log.error(f"Callback error: {e}")

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Upload file handler (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        return

    try:
        msg = update.message
        video = msg.video
        document = msg.document

        if video:
            file_info = {
                "file_name": video.file_name or f"video_{int(time.time())}.mp4",
                "mime_type": video.mime_type or "video/mp4",
                "is_video": True,
                "size": video.file_size or 0
            }
            file_id = video.file_id
        elif document:
            filename = document.file_name or f"doc_{int(time.time())}"
            ext = filename.lower().split('.')[-1] if '.' in filename else ""
            file_info = {
                "file_name": filename,
                "mime_type": document.mime_type or "",
                "is_video": ext in ALL_VIDEO_EXTS,
                "size": document.file_size or 0
            }
            file_id = document.file_id
        else:
            await msg.reply_text("❌ Send a video or document")
            return

        key = await db.save_file(file_id, file_info)
        if not key:
            await msg.reply_text("❌ Failed to save file")
            return
            
        link = f"https://t.me/{bot_username}?start={key}"

        await msg.reply_text(
            f"✅ *Upload Successful*\n\n"
            f"📁 *Name:* `{file_info['file_name']}`\n"
            f"🔑 *Key:* `{key}`\n"
            f"🔗 *Link:* `{link}`",
            parse_mode="Markdown"
        )

    except Exception as e:
        log.exception("Upload error")
        await update.message.reply_text(f"❌ Upload failed")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stats command (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        return

    uptime = str(timedelta(seconds=int(time.time() - start_time)))
    file_count = await db.get_file_count()
    user_count = await db.get_user_count()

    await update.message.reply_text(
        f"📊 *Bot Statistics*\n\n"
        f"⏱ Uptime: {uptime}\n"
        f"📁 Files: {file_count}\n"
        f"👥 Users: {user_count}\n"
        f"⏰ Auto-delete: {DELETE_AFTER//60} minutes\n"
        f"📡 Mode: Polling",
        parse_mode="Markdown"
    )

async def listfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List files (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    files = await db.get_all_files()
    
    if not files:
        await update.message.reply_text("📁 No files stored")
        return
    
    msg = f"📁 *Total Files: {len(files)}*\n\n"
    for file in files[:10]:
        file_id, name, is_video, size, ts, access = file
        size_mb = size / (1024*1024) if size else 0
        msg += f"🔑 `{file_id}` - {name[:20]}... ({size_mb:.1f}MB) - 👥 {access}\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def deletefile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete file (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /deletefile <key>")
        return
    
    key = context.args[0]
    if await db.delete_file(key):
        await update.message.reply_text(f"✅ Deleted file {key}")
    else:
        await update.message.reply_text(f"❌ File {key} not found")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast to users (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text("❌ Usage: /broadcast <message>")
        return
    
    if update.message.reply_to_message:
        message_text = update.message.reply_to_message.text or update.message.reply_to_message.caption
    else:
        message_text = " ".join(context.args)
    
    user_ids = await db.get_all_user_ids(exclude_admin=True)
    
    status_msg = await update.message.reply_text(f"🔄 Broadcasting to {len(user_ids)} users...")
    
    successful = 0
    for user_id in user_ids[:50]:  # Limit to 50
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 *Broadcast*\n\n{message_text}",
                parse_mode="Markdown"
            )
            successful += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await status_msg.edit_text(f"✅ Broadcast complete\n✅ Sent: {successful}")

async def clearcache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear membership cache (admin only)"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    await db.clear_membership_cache()
    await update.message.reply_text("✅ Cache cleared")

# ============ BOT STARTUP WITH POLLING ============
async def start_bot():
    """Start the bot with polling"""
    global db_initialized, bot_username, application

    if not BOT_TOKEN or not ADMIN_ID:
        log.error("Missing BOT_TOKEN or ADMIN_ID")
        return

    # Try to initialize database (don't fail if it doesn't work)
    try:
        log.info("🔄 Attempting database connection...")
        await db.get_connection()
        if db.initialized:
            log.info("✅ Database connected successfully")
        else:
            log.warning("⚠️ Database not connected - some features may be limited")
    except Exception as e:
        log.error(f"❌ Database connection failed: {e}")
        log.warning("⚠️ Bot will continue with limited functionality")

    # Create application
    application = Application.builder().token(BOT_TOKEN).build()

    # Get bot info
    bot_info = await application.bot.get_me()
    bot_username = bot_info.username
    log.info(f"✅ Bot username: @{bot_username}")

    # Add handlers
    application.add_error_handler(error_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("listfiles", listfiles))
    application.add_handler(CommandHandler("deletefile", deletefile))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("clearcache", clearcache))
    application.add_handler(CallbackQueryHandler(check_join, pattern="^check"))

    upload_filter = filters.VIDEO | filters.Document.ALL
    application.add_handler(
        MessageHandler(upload_filter & filters.User(ADMIN_ID) & filters.ChatType.PRIVATE, upload)
    )

    log.info("🤖 Starting bot in POLLING mode...")
    
    # Start polling
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    log.info("✅ Bot is running! Press Ctrl+C to stop")
    
    # Keep running
    while True:
        await asyncio.sleep(3600)

def main():
    """Main function - runs both Flask and Bot"""
    print("\n" + "=" * 60)
    print("🤖 TELEGRAM FILE BOT - POLLING MODE")
    print("=" * 60)
    print(f"✅ Bot: @{bot_username}")
    print(f"✅ Flask Port: 5000 (dashboard)")
    print("✅ Mode: Polling (No Webhook)")
    print("=" * 60 + "\n")
    
    # Start Flask dashboard
    flask_thread = threading.Thread(target=run_flask_thread, daemon=True)
    flask_thread.start()
    log.info(f"✅ Flask dashboard started on port 5000")
    
    # Start bot with polling
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped")
    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
    finally:
        try:
            asyncio.run(db.close())
        except:
            pass

if __name__ == "__main__":
    main()
