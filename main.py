"""
Telegram Bot using PP-DocLayout-S to detect documents
Returns: "its doc" or "not a doc" for every image
"""

import os
import logging
from pathlib import Path
from datetime import datetime
import tempfile
from typing import Tuple

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from paddleocr import LayoutDetection

# ==================== CONFIGURATION ====================
BOT_TOKEN = "7666489482:AAGXxYdgfKZGehpByZo2KXyFG5hGdM808YQ"  # Replace with your actual bot token

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== INITIALIZE PP-DOCLAYOUT-S ====================
print("🔄 Loading PP-DocLayout-S model...")
try:
    detector = LayoutDetection(
        model_name="PP-DocLayout-S",
        device='CPU'
    )
    print("✅ PP-DocLayout-S loaded successfully!")
except Exception as e:
    print(f"❌ Error loading model: {e}")
    detector = None

# ==================== DOCUMENT DETECTION ====================
def is_document(image_path: str) -> Tuple[bool, str]:
    """
    Returns: (True/False, reason)
    """
    try:
        if detector is None:
            return False, "Model not loaded"
        
        # Run layout detection
        result = detector.predict(image_path)
        
        # Document elements that indicate it's a document
        doc_indicators = [
            'text', 'paragraph_title', 'document_title', 
            'table', 'figure', 'list', 'header', 'footer',
            'text_block', 'paragraph', 'abstract'
        ]
        
        # Count document elements found
        doc_elements = []
        for detection in result:
            if detection['boxes'] and len(detection['boxes']) > 0:
                for box in detection['boxes']:
                    element_type = box.get('label', 'unknown')
                    confidence = box.get('score', 0)
                    
                    if element_type in doc_indicators and confidence > 0.5:
                        doc_elements.append(element_type)
        
        # Decision: if 2+ elements OR 1 high-confidence element
        if len(doc_elements) >= 2:
            return True, f"Found {len(doc_elements)} document elements"
        elif len(doc_elements) == 1 and confidence > 0.8:
            return True, "Found high-confidence document element"
        else:
            return False, "No document layout detected"
            
    except Exception as e:
        logger.error(f"Detection error: {e}")
        return False, f"Error: {str(e)}"

# ==================== TELEGRAM HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message"""
    welcome_text = (
        "📄 *Document Detector Bot*\n\n"
        "Send me any image and I'll tell you:\n"
        "• ✅ *its doc* - if it's a document\n"
        "• ❌ *not a doc* - if it's not a document\n\n"
        "Using PP-DocLayout-S for fast detection!"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process images"""
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
        processing = await update.message.reply_text("🔍 Analyzing...")
        
        # Detect if document
        is_doc, reason = is_document(file_path)
        
        # Send result
        if is_doc:
            await update.message.reply_text("✅ its doc")
        else:
            await update.message.reply_text("❌ not a doc")
        
        # Delete processing message
        await processing.delete()
        
        # Log
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

# ==================== MAIN ====================
def main():
    """Start the bot"""
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Start bot
    print("\n" + "="*50)
    print("🤖 Document Detector Bot Started!")
    print("="*50)
    print("Model: PP-DocLayout-S")
    print("Send images to your bot to test!")
    print("Press Ctrl+C to stop.\n")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
