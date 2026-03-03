import os
import logging
import threading
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from paddleocr import PaddleOCR, LayoutDetection
import numpy as np
from PIL import Image
import io

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Create Flask app for web server
app = Flask(__name__)

# Initialize models (global so both threads can access)
print("Loading PaddleOCR (text recognition)...")
ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)

print("Loading PP-DocLayout-S (layout detection)...")
layout_model = LayoutDetection(model_name="PP-DocLayout-S")

# Telegram bot application (will be initialized in main)
telegram_app = None

# Flask route for health checks
@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "message": "Document Analysis Bot is running!",
        "bot_ready": telegram_app is not None
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message when /start is issued."""
    await update.message.reply_text(
        '👋 Hi! Send me any image, and I\'ll analyze its layout and text!\n\n'
        'I can detect document structure, text regions, tables, seals, and more.\n\n'
        '**How to use:** Just send me a photo!'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when /help is issued."""
    await update.message.reply_text(
        'Just send me an image (photo, screenshot, document scan)\n'
        'I\'ll analyze the layout and tell you what elements I find!\n\n'
        '**Supported:** ID cards, documents, forms, receipts, screenshots'
    )

async def analyze_layout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the image for layout detection and OCR."""
    await update.message.reply_text("📸 Analyzing image... please wait a moment.")
    
    try:
        # Download the image
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        
        # Convert to image format
        image = Image.open(io.BytesIO(image_bytes))
        image_np = np.array(image)
        
        # STEP 1: Run PP-DocLayout-S for layout detection
        layout_results = layout_model.predict(image_np, batch_size=1, layout_nms=True)
        
        # STEP 2: Run OCR for text extraction
        ocr_results = ocr.ocr(image_np, cls=True)
        
        # Process layout results
        layout_elements = []
        doc_indicators = 0
        doc_keywords = ['text', 'title', 'table', 'seal', 'header', 'footer', 'formula']
        
        for res in layout_results:
            for box in res.boxes:
                label = box.get('label', '')
                confidence = box.get('score', 0)
                layout_elements.append(f"{label} ({confidence:.2f})")
                
                if any(keyword in label.lower() for keyword in doc_keywords):
                    doc_indicators += 1
        
        # Process OCR results
        text_found = []
        if ocr_results and ocr_results[0]:
            for line in ocr_results[0]:
                text_found.append(line[1][0])
        
        # Prepare response
        response = f"✅ **Analysis Complete**\n\n"
        response += f"📐 **Layout Elements Found:** {len(layout_elements)}\n"
        
        if layout_elements:
            response += f"Top elements:\n"
            for elem in layout_elements[:5]:
                response += f"• {elem}\n"
        
        response += f"\n📝 **Text Lines Found:** {len(text_found)}\n"
        
        if doc_indicators >= 2:
            response += f"\n📄 **This appears to be a DOCUMENT** (layout score: {doc_indicators})\n"
        elif doc_indicators == 1:
            response += f"\n📄 **Might be a document** (low confidence)\n"
        else:
            response += f"\n🖼️ **This may NOT be a formal document**\n"
        
        if text_found:
            response += f"\n📋 First few lines:\n"
            for line in text_found[:3]:
                # Truncate long lines
                short_line = line[:50] + "..." if len(line) > 50 else line
                response += f"• {short_line}\n"
        
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"Error processing image: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    logger.error(f"Update {update} caused error {context.error}")

def run_bot():
    """Run the Telegram bot in a separate thread."""
    global telegram_app
    
    token = os.environ.get('7666489482:AAGXxYdgfKZGehpByZo2KXyFG5hGdM808YQ')
    if not token:
        logger.error("No token found! Set TELEGRAM_BOT_TOKEN environment variable.")
        return
    
    # Create the Application
    telegram_app = Application.builder().token(token).build()

    # Register handlers
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, analyze_layout))
    telegram_app.add_error_handler(error_handler)

    logger.info("🤖 Telegram bot starting...")
    telegram_app.run_polling()

def run_flask():
    """Run the Flask web server."""
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Web server starting on port {port}...")
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    # Start Telegram bot in a background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logger.info("✅ Bot thread started")
    
    # Run Flask in the main thread (this blocks)
    run_flask()
