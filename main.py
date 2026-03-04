"""
Telegram Bot using PP-DocLayout-S + PaddleOCR to detect documents
Returns: "its doc" or "not a doc" for every image
"""

import sys
import traceback
import logging

# Force logging to print immediately
logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

try:
    # Your existing imports go here
    print("Starting import of telegram...", file=sys.stderr)
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    print("Telegram imports successful", file=sys.stderr)
    
    print("Importing paddleocr...", file=sys.stderr)
    from paddleocr import PaddleOCR, PPStructure
    print("PaddleOCR imports successful", file=sys.stderr)
    
    # ... rest of your imports
    
except Exception as e:
    print("="*50, file=sys.stderr)
    print("FATAL ERROR DURING STARTUP:", file=sys.stderr)
    print(str(e), file=sys.stderr)
    print(traceback.format_exc(), file=sys.stderr)
    print("="*50, file=sys.stderr)
    sys.exit(1)
    
import os
import logging
from pathlib import Path
from datetime import datetime
import tempfile
import threading
import time
from typing import Tuple

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from paddleocr import PaddleOCR, PPStructure
from PIL import Image
import numpy as np
from flask import Flask

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get('7666489482:AAGXxYdgfKZGehpByZo2KXyFG5hGdM808YQ', "YOUR_BOT_TOKEN_HERE")  # Get from environment

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== INITIALIZE MODELS ====================
print("🔄 Loading models...")

# Initialize layout detector using PPStructure (updated API)
layout_detector = None
try:
    from paddleocr import PPStructure
    layout_detector = PPStructure(
        layout_model_name='PP-DocLayout-S',
        use_angle_cls=False,
        lang='en',
        show_log=False
    )
    print("✅ Layout detector loaded successfully!")
except Exception as e:
    print(f"❌ Error loading layout detector: {e}")
    layout_detector = None

# Initialize OCR with correct parameters (updated for new version)
ocr_reader = None
try:
    ocr_reader = PaddleOCR(
        use_angle_cls=False,           # Changed from deprecated parameter
        lang='en',
        show_log=False,
        use_gpu=False,                   # Keep as is
        enable_mkldnn=True
    )
    print("✅ PaddleOCR loaded successfully!")
except Exception as e:
    print(f"❌ Error loading OCR: {e}")
    ocr_reader = None

# ==================== ENHANCED DOCUMENT DETECTION ====================
def is_document_enhanced(image_path: str) -> Tuple[bool, str, dict]:
    """
    Use BOTH layout detection and OCR to detect if image contains a document
    """
    details = {
        'layout_elements': [],
        'has_text': False,
        'text_lines': 0,
        'confidence': 0
    }
    
    # METHOD 1: Layout Detection (PPStructure with PP-DocLayout-S)
    layout_score = 0
    layout_reason = ""
    
    if layout_detector:
        try:
            result = layout_detector(image_path)
            
            if result and len(result) > 0:
                layout_elements = []
                for item in result:
                    if 'type' in item:
                        layout_elements.append(item['type'])
                
                details['layout_elements'] = layout_elements
                
                if len(layout_elements) >= 2:
                    layout_score = 0.9
                    layout_reason = f"Found {len(layout_elements)} layout elements"
                elif len(layout_elements) == 1:
                    layout_score = 0.6
                    layout_reason = "Found one layout element"
            
        except Exception as e:
            logger.error(f"Layout detection error: {e}")
    
    # METHOD 2: OCR Text Detection
    ocr_score = 0
    ocr_reason = ""
    
    if ocr_reader:
        try:
            ocr_result = ocr_reader.ocr(image_path, cls=False)
            
            text_lines = 0
            
            if ocr_result and ocr_result[0]:
                for line in ocr_result[0]:
                    if line and len(line) >= 2:
                        text = line[1][0]
                        if text and len(text.strip()) > 1:
                            text_lines += 1
            
            details['has_text'] = text_lines > 0
            details['text_lines'] = text_lines
            
            if text_lines >= 3:
                ocr_score = 0.9
                ocr_reason = f"Found {text_lines} text lines"
            elif text_lines >= 1:
                ocr_score = 0.5
                ocr_reason = "Found some text"
            
        except Exception as e:
            logger.error(f"OCR error: {e}")
    
    # Combine scores
    if layout_score >= 0.6 or ocr_score >= 0.6:
        details['final_score'] = max(layout_score, ocr_score)
        details['decision'] = 'document'
        return True, f"Document detected: {layout_reason} | {ocr_reason}", details
    
    elif layout_score >= 0.3 and ocr_score >= 0.3:
        combined = (layout_score * 0.5) + (ocr_score * 0.5)
        if combined > 0.4:
            details['final_score'] = combined
            details['decision'] = 'document'
            return True, f"Combined detection: {layout_reason} + {ocr_reason}", details
    
    details['final_score'] = max(layout_score, ocr_score)
    details['decision'] = 'not_document'
    return False, "No document signals detected", details

# ==================== TELEGRAM BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    welcome_text = (
        "📄 *Enhanced Document Detector Bot*\n\n"
        "Using *PP-DocLayout-S + PaddleOCR* together!\n\n"
        "Send me any image and I'll tell you if it's a document or not.\n\n"
        "• ✅ *its a document* - if document layout OR text detected\n"
        "• ❌ *its not document* - if no document signals found"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check bot status"""
    status_text = (
        "*✅ Bot Status*\n\n"
        f"• Layout Detector: {'✅ Loaded' if layout_detector else '❌ Failed'}\n"
        f"• OCR Engine: {'✅ Loaded' if ocr_reader else '❌ Failed'}\n"
        "• Mode: Hybrid (Layout + Text)\n"
        "• Ready to detect documents!"
    )
    await update.message.reply_text(status_text, parse_mode='Markdown')


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any image sent to the bot"""
    
    # Send typing indicator
    await update.message.chat.send_action(action="typing")
    
    # Get the photo
    photo = await update.message.photo[-1].get_file()
    
    # Create temp file
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
        file_path = tmp_file.name
    
    try:
        # Download image
        await photo.download_to_drive(file_path)
        
        # Send processing message
        processing_msg = await update.message.reply_text("🔍 Analyzing with Layout Detection + OCR...")
        
        # Check if it's a document using enhanced method
        is_doc, reason, details = is_document_enhanced(file_path)
        
        # Prepare response
        if is_doc:
            response = "✅ its a document"
        else:
            response = "❌ not a document"
        
        # Add minimal debug (optional)
        debug_info = f"\n\n🔍 Details: {reason}"
        response += debug_info
        
        # Delete processing message
        await processing_msg.delete()
        
        # Send result
        await update.message.reply_text(response)
        
        logger.info(f"Result: {'doc' if is_doc else 'not doc'} - {reason}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Error processing image")
    
    finally:
        # Clean up
        if os.path.exists(file_path):
            os.unlink(file_path)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document files"""
    doc = update.message.document
    
    if doc.mime_type and doc.mime_type.startswith('image/'):
        await handle_image(update, context)
    else:
        await update.message.reply_text("❌ Please send an image file")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    await update.message.reply_text("Send me an image, I'll tell you if it's a document!")


# ==================== WEB SERVER FOR RENDER ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Enhanced Document Detector Bot is running!"

@app.route('/health')
def health():
    return "OK", 200

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Starting web server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==================== MAIN FUNCTION ====================

def main():
    """Start the bot"""
    
    # Check models
    if layout_detector is None:
        print("⚠️ Layout detector not loaded - will rely on OCR only")
    if ocr_reader is None:
        print("⚠️ OCR not loaded - will rely on layout only")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("\n" + "="*50)
    print("🤖 Enhanced Document Detector Bot Started!")
    print("="*50)
    print("Models: PP-DocLayout-S + PaddleOCR")
    print("Mode: Hybrid detection (layout + text)")
    print("\nBot is now polling for messages...")
    print("Press Ctrl+C to stop.\n")
    
    # Start polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    # Start web server in background thread
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Small delay to ensure web server starts first
    time.sleep(2)
    
    # Start the bot
    main()
