import os
import asyncio
import threading
import uuid
import logging
import sys
from flask import Flask, jsonify
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import numpy as np

# Suppress excessive logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Suppress PaddleOCR debug logs
os.environ['GLOG_minloglevel'] = '2'
os.environ['FLAGS_log_level'] = '2'

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

flask_app = Flask(__name__)
bot = None
dp = None
layout_engine = None

# Flask routes
@flask_app.route('/')
def index():
    return jsonify({
        "status": "online",
        "message": "Telegram Document Layout Analysis Bot is running!",
        "bot_status": "active",
        "endpoints": {
            "/": "This information",
            "/health": "Health check endpoint"
        }
    })

@flask_app.route('/health')
def health():
    return jsonify({
        "status": "healthy" if layout_engine else "loading",
        "bot_initialized": bot is not None,
        "layout_engine_loaded": layout_engine is not None,
        "python_version": sys.version,
        "download_folder_exists": os.path.exists(DOWNLOAD_FOLDER)
    })

def load_models():
    """Load PP-DocLayout model"""
    global layout_engine
    try:
        logger.info("Loading PP-DocLayout-S model...")
        from paddleocr import PPStructure
        
        layout_engine = PPStructure(
            layout_model_dir=None,
            table=False,
            ocr=True,
            show_log=False,
            device="cpu",
            det_db_thresh=0.3,
            det_db_box_thresh=0.5,
            det_db_unclip_ratio=1.6,
            use_dilation=False,
            det_db_score_mode="fast",
            det_db_thresh_side=0.5
        )
        logger.info("✅ PP-DocLayout-S model loaded successfully!")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to load model: {e}")
        layout_engine = None
        return False

async def handle_start(message: types.Message):
    """Handle /start command"""
    welcome_text = (
        "🤖 **Document Layout Analysis Bot**\n\n"
        "Send me any document (PDF, image, photo) and I'll analyze its layout structure!\n\n"
        "**Supported formats:**\n"
        "• PDF documents\n"
        "• Images (JPG, PNG, etc.)\n"
        "• Photos from camera\n\n"
        "I'll identify elements like titles, paragraphs, tables, figures, and more!"
    )
    await message.answer(welcome_text)

async def handle_document(message: types.Message):
    """Handle document and photo uploads"""
    processing_msg = await message.answer("🧠 **Analyzing document layout...**\n\nThis may take a few seconds...")
    
    file_path = None
    try:
        # Check if layout engine is loaded
        if layout_engine is None:
            await processing_msg.delete()
            await message.answer("❌ **System is initializing**\n\nPlease wait a moment and try again.")
            return
        
        unique_id = uuid.uuid4().hex[:8]
        
        # Download the file
        if message.document:
            file = await bot.get_file(message.document.file_id)
            file_ext = os.path.splitext(message.document.file_name or 'document.pdf')[1]
            file_path = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}{file_ext}")
            await bot.download_file(file.file_path, file_path)
            logger.info(f"Downloaded document: {message.document.file_name}")
            
        elif message.photo:
            file = await bot.get_file(message.photo[-1].file_id)
            file_path = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}.jpg")
            await bot.download_file(file.file_path, file_path)
            logger.info(f"Downloaded photo")
        else:
            await processing_msg.delete()
            await message.answer("❌ Please send a document or photo")
            return
        
        # Analyze layout
        await processing_msg.edit_text("🔍 **Processing document...**\n\nAnalyzing layout structure...")
        
        result = layout_engine(file_path)
        
        # Process results
        if not result:
            await processing_msg.delete()
            await message.answer("❌ **No layout elements detected**\n\nThe document might be empty or corrupted.")
            os.remove(file_path)
            return
        
        # Count different layout elements
        summary = {}
        element_details = []
        
        for region in result:
            label = region.get('type', 'unknown')
            summary[label] = summary.get(label, 0) + 1
            
            # Get confidence if available
            bbox = region.get('bbox', [])
            score = region.get('score', 1.0)
            element_details.append(f"• {label.upper()} (confidence: {score:.2%})")
        
        # Create response message
        doc_type = "📄 **DOCUMENT DETECTED**" if any(k in summary for k in ['title', 'paragraph', 'table']) else "🖼️ **IMAGE / NON-DOCUMENT**"
        
        response = f"{doc_type}\n\n"
        response += "**📊 Layout Analysis Results:**\n"
        
        # Map to readable names
        readable_names = {
            'title': 'Titles',
            'paragraph': 'Paragraphs',
            'table': 'Tables',
            'figure': 'Figures',
            'header': 'Headers',
            'footer': 'Footers',
            'caption': 'Captions',
            'reference': 'References'
        }
        
        for label, count in sorted(summary.items(), key=lambda x: x[1], reverse=True):
            display_name = readable_names.get(label, label.capitalize())
            response += f"• **{display_name}**: {count}\n"
        
        # Add some analysis insights
        response += "\n**📈 Analysis Insights:**\n"
        if 'title' in summary:
            response += "✓ Document contains titles\n"
        if 'table' in summary:
            response += "✓ Tables detected - contains structured data\n"
        if 'figure' in summary:
            response += "✓ Figures/Images detected\n"
        if summary.get('paragraph', 0) > 10:
            response += "✓ Multiple paragraphs - likely text-heavy document\n"
        
        await processing_msg.delete()
        await message.answer(response)
        
        # Clean up
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up file: {file_path}")
        
    except Exception as e:
        logger.error(f"Error processing document: {e}", exc_info=True)
        await processing_msg.delete()
        await message.answer(f"❌ **Error processing document**\n\n{str(e)[:200]}")
        
        # Clean up on error
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass

async def setup_bot():
    """Initialize and start the bot"""
    global bot, dp
    
    # Load models
    if not load_models():
        logger.error("Failed to load models. Bot will continue but document analysis will fail.")
    
    # Initialize bot
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # Register handlers
    dp.message.register(handle_start, Command("start"))
    dp.message.register(handle_document, lambda m: m.document is not None or m.photo is not None)
    
    # Get bot info
    bot_info = await bot.get_me()
    logger.info(f"🤖 Bot started successfully: @{bot_info.username}")
    logger.info(f"📁 Download folder: {DOWNLOAD_FOLDER}")
    
    # Start polling
    await dp.start_polling(bot)

def run_flask():
    """Run Flask app in separate thread"""
    flask_app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)

if __name__ == "__main__":
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("🌐 Flask server started on port 10000")
    
    # Run the bot
    try:
        asyncio.run(setup_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}", exc_info=True)
