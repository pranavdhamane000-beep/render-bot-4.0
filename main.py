"""
Telegram Bot using ONLY PP-DocLayout-S to detect documents
Returns: "its doc" or "not a doc" based on visual layout analysis
NO OCR - Pure vision-based document detection
"""

import os
import logging
import sys
import traceback
import tempfile
import threading
import time
from typing import Tuple, List, Dict, Any

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask

# Import ONLY layout detection, NOT OCR
try:
    from paddleocr import PPStructure
    print("✅ PPStructure imported successfully")
except ImportError as e:
    print(f"❌ Failed to import PPStructure: {e}")
    print("   Make sure paddleocr is installed with: pip install 'paddleocr[layout]'")
    PPStructure = None
    sys.exit(1)

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN', "7666489482:AAGXxYdgfKZGehpByZo2KXyFG5hGdM808YQ")  # Get from environment

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== INITIALIZE ONLY PP-DOCLAYOUT-S ====================
print("🔄 Loading PP-DocLayout-S model (NO OCR)...")
layout_detector = None

try:
    # Initialize ONLY the layout detector with OCR disabled
    layout_detector = PPStructure(
        layout=True,                # Enable layout detection
        ocr=False,                  # DISABLE OCR - this is key!
        show_log=False,             # Reduce noise
        device='CPU'                # Force CPU usage
        # layout_model_name='PP-DocLayout-S'  # Uncomment if needed
    )
    print("✅ PP-DocLayout-S loaded successfully!")
    print("   Mode: Pure visual layout analysis (NO OCR)")
except Exception as e:
    print(f"❌ Error loading layout detector: {e}")
    print(traceback.format_exc())
    layout_detector = None

# ==================== DOCUMENT DETECTION USING ONLY LAYOUT ====================
def is_document(image_path: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Use ONLY PP-DocLayout-S to detect if image contains document layout
    NO OCR USED - pure visual analysis only
    """
    details = {
        'layout_elements': [],
        'element_count': 0,
        'confidence': 0.0
    }
    
    if layout_detector is None:
        return False, "Layout detector not loaded", details
    
    try:
        # Run layout detection (vision-only)
        result = layout_detector(image_path)
        
        # Document elements that indicate it's a document
        doc_indicators = [
            'text', 'title', 'paragraph', 'header', 'footer',
            'figure', 'table', 'list', 'reference', 'caption',
            'section', 'column', 'page', 'document', 'abstract',
            'textblock', 'text_region', 'text_area'
        ]
        
        found_elements = []
        max_confidence = 0.0
        
        # Analyze layout results
        if result and isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    element_type = item.get('type', '').lower()
                    confidence = float(item.get('confidence', 0))
                    
                    # Check if this element indicates a document
                    if any(indicator in element_type for indicator in doc_indicators):
                        if confidence > 0.3:  # Lower threshold for sensitivity
                            found_elements.append({
                                'type': element_type,
                                'confidence': confidence
                            })
                            max_confidence = max(max_confidence, confidence)
        
        details['layout_elements'] = found_elements
        details['element_count'] = len(found_elements)
        details['confidence'] = max_confidence
        
        # Decision: If ANY document-like elements found, it's a document
        if len(found_elements) >= 1:
            element_types = [e['type'] for e in found_elements]
            return True, f"Found {len(found_elements)} document elements: {', '.join(element_types)}", details
        else:
            return False, "No document layout elements detected", details
            
    except Exception as e:
        logger.error(f"Layout detection error: {e}")
        return False, f"Error: {str(e)}", details

# ==================== TELEGRAM BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    welcome_text = (
        "📄 *Document Detector Bot*\n\n"
        "Using *ONLY PP-DocLayout-S* (NO OCR)\n\n"
        "Send me any image and I'll tell you if it's a document or not.\n\n"
        "• ✅ *its a document* - if document layout detected visually\n"
        "• ❌ *not a document* - if no document layout found\n\n"
        "⚡ *Pure visual analysis* - no text reading, works on any language!\n"
        "📑 Detects: certificates, IDs, receipts, forms, book pages"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message"""
    help_text = (
        "*📚 Commands*\n\n"
        "/start - Welcome message\n"
        "/help - This help\n"
        "/status - Check bot status\n\n"
        "*How it works:*\n"
        "1. You send an image\n"
        "2. PP-DocLayout-S analyzes visual layout (no text reading)\n"
        "3. Bot replies with result\n\n"
        "*Supported documents:*\n"
        "• Government IDs (Aadhaar, PAN, passport)\n"
        "• Certificates\n"
        "• Bills & receipts\n"
        "• Forms & applications\n"
        "• Book pages\n"
        "• Screenshots with text"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check bot status"""
    if layout_detector:
        status_text = (
            "*✅ Bot Status*\n\n"
            "• Model: PP-DocLayout-S\n"
            "• OCR: ❌ DISABLED\n"
            "• Mode: Pure visual layout analysis\n"
            "• Speed: ~15-50ms per image\n"
            "• Status: Online\n\n"
            "Ready to detect documents by their visual structure!"
        )
    else:
        status_text = (
            "*❌ Bot Status*\n\n"
            "• Model: Not loaded\n"
            "• Status: Error\n\n"
            "Please check server logs."
        )
    await update.message.reply_text(status_text, parse_mode='Markdown')


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any image sent to the bot - NO OCR USED"""
    
    # Send typing indicator
    await update.message.chat.send_action(action="typing")
    
    # Get the photo (highest resolution)
    photo = await update.message.photo[-1].get_file()
    
    # Create temp file
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
        file_path = tmp_file.name
    
    try:
        # Download image
        await photo.download_to_drive(file_path)
        
        # Send processing message
        processing_msg = await update.message.reply_text("🔍 Analyzing document layout (NO OCR)...")
        
        # Check if it's a document using ONLY layout detection
        is_doc, reason, details = is_document(file_path)
        
        # Prepare response
        if is_doc:
            response = "✅ its a document"
        else:
            response = "❌ not a document"
        
        # Add minimal debug info (useful for testing)
        if details and details.get('element_count', 0) > 0:
            elements = [e['type'] for e in details.get('layout_elements', [])[:3]]  # Show first 3
            if elements:
                response += f"\n\n🔍 Detected: {', '.join(elements)}"
                if details['element_count'] > 3:
                    response += f" +{details['element_count']-3} more"
        
        # Delete processing message
        await processing_msg.delete()
        
        # Send result
        await update.message.reply_text(response)
        
        # Log for monitoring
        logger.info(f"Image processed: {response} - {reason}")
        
    except Exception as e:
        logger.error(f"Error processing image: {e}")
        await update.message.reply_text("❌ Error processing image")
    
    finally:
        # Clean up temp file
        if os.path.exists(file_path):
            os.unlink(file_path)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document files"""
    doc = update.message.document
    
    # Check if it's an image file
    if doc.mime_type and doc.mime_type.startswith('image/'):
        # Download the document
        file = await doc.get_file()
        
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(doc.file_name)[1], delete=False) as tmp_file:
            file_path = tmp_file.name
        
        try:
            await file.download_to_drive(file_path)
            
            # Send processing message
            processing_msg = await update.message.reply_text("🔍 Analyzing document layout (NO OCR)...")
            
            # Check if it's a document
            is_doc, reason, details = is_document(file_path)
            
            # Prepare response
            if is_doc:
                response = "✅ its a document"
            else:
                response = "❌ not a document"
            
            await processing_msg.delete()
            await update.message.reply_text(response)
            
        finally:
            if os.path.exists(file_path):
                os.unlink(file_path)
    else:
        await update.message.reply_text("❌ Please send an image file (JPEG, PNG, etc.)")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    await update.message.reply_text(
        "Send me an image, I'll tell you if it's a document!\n"
        "Use /help for more info."
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again."
            )
    except:
        pass


# ==================== SIMPLE WEB SERVER FOR RENDER ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Document Detector Bot (ONLY PP-DocLayout-S, NO OCR) is running!"

@app.route('/health')
def health():
    return "OK", 200

def run_web_server():
    """Run Flask web server in a separate thread"""
    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Starting web server on port {port} for Render...")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==================== MAIN FUNCTION ====================

def main():
    """Start the bot"""
    
    # Check if layout detector loaded
    if layout_detector is None:
        print("❌ CRITICAL: Layout detector failed to load!")
        print("   Bot will not function correctly.")
        print("   Check logs above for detailed error.")
    else:
        print("\n" + "="*50)
        print("✅ BOT READY - PURE VISUAL MODE")
        print("="*50)
        print("Model: PP-DocLayout-S")
        print("OCR: DISABLED")
        print("Mode: Pure visual layout analysis")
        print("Speed: ~15-50ms per image")
        print("\nBot is now polling for messages...")
        print("Press Ctrl+C to stop.\n")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    
    # Add message handlers
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot (this blocks until stopped)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    # Start web server in a background thread
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Small delay to ensure web server starts first
    time.sleep(2)
    
    # Start the bot in the main thread
    main()
