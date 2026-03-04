"""
Telegram Bot using PP-DocLayout-S + PaddleOCR to detect documents
Returns: "its doc" or "not a doc" for every image
"""

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
from paddleocr import LayoutDetection, PaddleOCR
from PIL import Image
import numpy as np
from flask import Flask

# ==================== CONFIGURATION ====================
BOT_TOKEN = "7666489482:AAGXxYdgfKZGehpByZo2KXyFG5hGdM808YQ"  # Replace with your actual bot token

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== INITIALIZE BOTH MODELS ====================
print("🔄 Loading PP-DocLayout-S model...")
try:
    # Initialize layout detector
    layout_detector = LayoutDetection(
        model_name="PP-DocLayout-S",
        device='CPU'
    )
    print("✅ PP-DocLayout-S loaded successfully!")
except Exception as e:
    print(f"❌ Error loading layout detector: {e}")
    layout_detector = None

print("🔄 Loading PaddleOCR model...")
try:
    # Initialize OCR for text extraction
    ocr_reader = PaddleOCR(
        use_angle_cls=True,      # Handle rotated text
        lang='en',                # English (works for Aadhaar)
        show_log=False,           # Reduce noise
        use_gpu=False
    )
    print("✅ PaddleOCR loaded successfully!")
except Exception as e:
    print(f"❌ Error loading OCR: {e}")
    ocr_reader = None

# ==================== ENHANCED DOCUMENT DETECTION ====================
def is_document_enhanced(image_path: str) -> Tuple[bool, str, dict]:
    """
    Use BOTH PP-DocLayout-S and PaddleOCR to detect if image contains a document
    Returns: (is_document, reason, details)
    """
    details = {
        'layout_elements': [],
        'has_text': False,
        'text_lines': 0,
        'confidence': 0
    }
    
    # METHOD 1: Layout Detection (PP-DocLayout-S)
    layout_score = 0
    layout_reason = ""
    
    if layout_detector:
        try:
            result = layout_detector.predict(image_path)
            
            # Document elements that indicate it's a document
            doc_indicators = [
                'text', 'paragraph_title', 'document_title', 
                'table', 'figure', 'list', 'header', 'footer',
                'text_block', 'paragraph', 'abstract'
            ]
            
            layout_elements = []
            for detection in result:
                if detection['boxes'] and len(detection['boxes']) > 0:
                    for box in detection['boxes']:
                        element_type = box.get('label', 'unknown')
                        confidence = box.get('score', 0)
                        
                        if element_type in doc_indicators and confidence > 0.5:
                            layout_elements.append({
                                'type': element_type,
                                'confidence': confidence
                            })
            
            details['layout_elements'] = layout_elements
            
            # Score based on layout elements
            if len(layout_elements) >= 2:
                layout_score = 0.9
                layout_reason = f"Found {len(layout_elements)} document elements"
            elif len(layout_elements) == 1:
                if layout_elements[0]['confidence'] > 0.8:
                    layout_score = 0.7
                    layout_reason = f"Found high-confidence {layout_elements[0]['type']}"
                else:
                    layout_score = 0.3
                    layout_reason = "Found weak document element"
            
        except Exception as e:
            logger.error(f"Layout detection error: {e}")
    
    # METHOD 2: OCR Text Detection (PaddleOCR)
    ocr_score = 0
    ocr_reason = ""
    
    if ocr_reader:
        try:
            ocr_result = ocr_reader.ocr(image_path, cls=True)
            
            text_lines = 0
            total_confidence = 0
            
            if ocr_result and ocr_result[0]:
                for line in ocr_result[0]:
                    if line and len(line) >= 2:
                        text = line[1][0]
                        confidence = line[1][1]
                        
                        # Only count if it's real text (not just symbols)
                        if text and len(text.strip()) > 1 and confidence > 0.5:
                            text_lines += 1
                            total_confidence += confidence
            
            details['has_text'] = text_lines > 0
            details['text_lines'] = text_lines
            
            # Score based on text
            if text_lines >= 3:
                ocr_score = 0.9
                ocr_reason = f"Found {text_lines} text lines"
            elif text_lines >= 1:
                avg_conf = total_confidence / text_lines if text_lines > 0 else 0
                if avg_conf > 0.7:
                    ocr_score = 0.6
                    ocr_reason = f"Found text with good confidence"
                else:
                    ocr_score = 0.3
                    ocr_reason = "Found weak text"
            
        except Exception as e:
            logger.error(f"OCR error: {e}")
    
    # METHOD 3: Combine Scores (Weighted Average)
    # Layout detection is more reliable, but OCR is good backup
    
    if layout_score >= 0.7 or ocr_score >= 0.7:
        # Strong signal from either method
        final_score = max(layout_score, ocr_score)
        reason = f"Strong detection: {layout_reason} | {ocr_reason}"
        details['final_score'] = final_score
        details['decision'] = 'document'
        return True, reason, details
    
    elif layout_score >= 0.3 and ocr_score >= 0.3:
        # Weak signals from both - combine
        combined_score = (layout_score * 0.6) + (ocr_score * 0.4)
        if combined_score > 0.5:
            reason = f"Combined detection: {layout_reason} + {ocr_reason}"
            details['final_score'] = combined_score
            details['decision'] = 'document'
            return True, reason, details
    
    # Not a document
    details['final_score'] = max(layout_score, ocr_score)
    details['decision'] = 'not_document'
    return False, "No strong document signals detected", details

# ==================== TELEGRAM BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    welcome_text = (
        "📄 *Enhanced Document Detector Bot*\n\n"
        "Using *PP-DocLayout-S + PaddleOCR* together!\n\n"
        "Send me any image and I'll tell you if it's a document or not.\n\n"
        "• ✅ *its a document* - if document layout OR text detected\n"
        "• ❌ *its not document* - if no document signals found\n\n"
        "Works great on Indian documents like Aadhaar, PAN, certificates!"
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
        processing_msg = await update.message.reply_text(
            "🔍 Analyzing with Layout Detection + OCR..."
        )
        
        # Check if it's a document using enhanced method
        is_doc, reason, details = is_document_enhanced(file_path)
        
        # Prepare response
        if is_doc:
            response = "✅ its a document"
        else:
            response = "❌ not a document"
        
        # Add debug info
        debug_info = f"\n\n🔍 Debug:\n"
        debug_info += f"• Layout elements: {len(details.get('layout_elements', []))}\n"
        debug_info += f"• Text lines found: {details.get('text_lines', 0)}\n"
        debug_info += f"• Confidence: {details.get('final_score', 0):.2f}\n"
        debug_info += f"• Reason: {reason}"
        
        response += debug_info
        
        # Delete processing message
        await processing_msg.delete()
        
        # Send result
        await update.message.reply_text(response)
        
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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    await update.message.reply_text(
        "Send me an image, I'll tell you if it's a document!\n"
        "Use /status to check bot health."
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ An error occurred")
    except:
        pass


# ==================== SIMPLE WEB SERVER FOR RENDER ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤍 Enhanced Document Detector Bot is running!"

@app.route('/health')
def health():
    return "OK", 200

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Starting web server on port {port}...")
    app.run(host='0.0.0.0', port=port)

# ==================== MAIN FUNCTION ====================

def main():
    """Start the bot"""
    
    # Check models
    if layout_detector is None:
        print("❌ WARNING: Layout detector failed to load!")
    if ocr_reader is None:
        print("❌ WARNING: OCR engine failed to load!")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(error_handler)
    
    print("\n" + "="*50)
    print("🤍 Enhanced Document Detector Bot Started!")
    print("="*50)
    print("Models: PP-DocLayout-S + PaddleOCR")
    print("Mode: Hybrid detection (layout + text)")
    print("\nBot is now polling for messages...")
    print("Press Ctrl+C to stop.\n")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    # Start web server in background thread
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Start the bot
    main()
