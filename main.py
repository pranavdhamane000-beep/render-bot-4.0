import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from paddleocr import PaddleOCR, LayoutDetection
import cv2
import numpy as np
from PIL import Image
import io

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize models
print("Loading PaddleOCR (text recognition)...")
ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)

print("Loading PP-DocLayout-S (layout detection)...")
layout_model = LayoutDetection(model_name="PP-DocLayout-S")  # 👈 This is the key!

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message when /start is issued."""
    await update.message.reply_text(
        '👋 Hi! Send me any image, and I\'ll analyze its layout and text!\n\n'
        'I can detect document structure, text regions, tables, seals, and more.'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when /help is issued."""
    await update.message.reply_text(
        'Just send me an image (photo, screenshot, document scan)\n'
        'I\'ll analyze the layout and tell you what elements I find!'
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
        else:
            response += f"\n🖼️ **This may NOT be a formal document**\n"
        
        if text_found:
            response += f"\n📋 First few lines:\n"
            for line in text_found[:3]:
                response += f"• {line[:50]}\n"
        
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"Error processing image: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:100]}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """Start the bot."""
    token = os.environ.get('7666489482:AAGXxYdgfKZGehpByZo2KXyFG5hGdM808YQ')
    if not token:
        logger.error("No token found! Set TELEGRAM_BOT_TOKEN environment variable.")
        return
    
    # Create the Application
    application = Application.builder().token(token).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.PHOTO, analyze_layout))
    
    application.add_error_handler(error_handler)

    print("🤖 Bot is running... Send it an image on Telegram!")
    application.run_polling()

if __name__ == '__main__':
    main()
