import os
import asyncio
import threading
from flask import Flask, jsonify
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
from paddleocr import LayoutDetection
import uuid
import logging

# ============= LOGGING =============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============= CONFIGURATION =============
# Get bot token from environment variable (SECURE!)
BOT_TOKEN = os.environ.get("7666489482:AAGXxYdgfKZGehpByZo2KXyFG5hGdM808YQ")
if not BOT_TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables!")

DOWNLOAD_FOLDER = "downloads"
OUTPUT_FOLDER = "output"

# Create folders if they don't exist
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Flask app for health checks
flask_app = Flask(__name__)

# Global bot objects
bot = None
dp = None
layout_model = None

# ============= FLASK HEALTH CHECK ENDPOINTS =============
@flask_app.route('/')
@flask_app.route('/health')
def health_check():
    return jsonify({
        "status": "healthy",
        "bot": "running",
        "model": "PP-DocLayout-S",
        "ready": layout_model is not None
    })

# ============= BOT HANDLERS =============
async def start_command(message: types.Message):
    await message.answer(
        "📄 **Document Layout Analyzer Bot**\n\n"
        "Send me any document (PDF or image), and I'll analyze its layout structure!\n\n"
        "**I can detect 23 elements including:**\n"
        "• 📝 Text blocks & paragraphs\n"
        "• 📌 Titles & headings\n"
        "• 📊 Tables & figures\n"
        "• 🧮 Formulas & algorithms\n"
        "• 📄 Headers, footers & page numbers\n"
        "• 🔖 References & footnotes\n"
        "• 🖼️ Images with captions\n\n"
        "Powered by **PP-DocLayout-S** (lightweight, 4.8MB model)\n"
        "⚡ Fast CPU inference: ~10ms per page",
        parse_mode="Markdown"
    )

async def help_command(message: types.Message):
    await message.answer(
        "📖 **How to use:**\n\n"
        "1. Send me a **PDF file** or **image** (JPG, PNG)\n"
        "2. I'll analyze the document layout\n"
        "3. I'll send back a summary of detected elements\n"
        "4. You'll also get a visualization image\n\n"
        "**Commands:**\n"
        "/start - Introduction\n"
        "/help - This help message\n"
        "/status - Check bot status",
        parse_mode="Markdown"
    )

async def status_command(message: types.Message):
    await message.answer(
        "🟢 **Bot Status:** Online\n\n"
        "**Model:** PP-DocLayout-S\n"
        "**Model Size:** 4.8 MB\n"
        "**Supported Languages:** Chinese & English\n"
        "**Detection Categories:** 23 document elements",
        parse_mode="Markdown"
    )

async def handle_document(message: types.Message):
    # Send initial processing message
    processing_msg = await message.answer("🔄 **Analyzing document layout...**", parse_mode="Markdown")
    
    try:
        # Generate unique filename to avoid conflicts
        unique_id = uuid.uuid4().hex[:8]
        
        # Download the file
        if message.document:
            file = await bot.get_file(message.document.file_id)
            file_extension = message.document.file_name.split('.')[-1] if '.' in message.document.file_name else 'pdf'
            file_path = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}.{file_extension}")
            await bot.download_file(file.file_path, file_path)
            file_name = message.document.file_name
        else:  # Photo
            file = await bot.get_file(message.photo[-1].file_id)
            file_path = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}.jpg")
            await bot.download_file(file.file_path, file_path)
            file_name = "image.jpg"
        
        logger.info(f"Processing file: {file_name}")
        
        # Run layout detection
        output = layout_model.predict(file_path, batch_size=1, layout_nms=True)
        
        # Parse and count detected elements
        detected_elements = []
        element_counts = {}
        
        for res in output:
            for box in res.get("boxes", []):
                label = box["label"]
                score = box["score"]
                bbox = box["coordinate"]
                
                detected_elements.append({
                    "label": label,
                    "score": score,
                    "bbox": bbox
                })
                
                # Count elements
                element_counts[label] = element_counts.get(label, 0) + 1
        
        # Build response message
        response = f"📊 **Layout Analysis Complete**\n"
        response += f"📄 File: `{file_name}`\n\n"
        response += f"**Detected Elements:**\n"
        
        # Element name mapping for better readability
        element_names = {
            "text": "📝 Text blocks",
            "paragraph_title": "📌 Paragraph titles",
            "document_title": "📑 Document title",
            "title": "📌 Title",
            "table": "📊 Tables",
            "table_caption": "📋 Table captions",
            "figure": "🖼️ Figures",
            "figure_caption": "🏷️ Figure captions",
            "image": "🖼️ Images",
            "formula": "🧮 Formulas",
            "formula_number": "🔢 Formula numbers",
            "algorithm": "⚙️ Algorithms",
            "page_number": "🔢 Page numbers",
            "header": "📄 Headers",
            "footer": "📄 Footers",
            "abstract": "📋 Abstracts",
            "references": "📚 References",
            "footnote": "📎 Footnotes",
            "seal": "🔒 Seals"
        }
        
        for label, count in sorted(element_counts.items(), key=lambda x: x[1], reverse=True):
            display_name = element_names.get(label, label)
            response += f"• {display_name}: **{count}**\n"
        
        response += f"\n⚡ Total detected regions: **{len(detected_elements)}**"
        
        # Delete processing message
        await processing_msg.delete()
        
        # Send text summary
        await message.answer(response, parse_mode="Markdown")
        
        # Save and send visualization if available
        for idx, res in enumerate(output):
            if hasattr(res, 'save_to_img'):
                vis_path = os.path.join(OUTPUT_FOLDER, f"layout_{unique_id}_page_{idx}.jpg")
                res.save_to_img(save_path=vis_path)
                
                if os.path.exists(vis_path):
                    # Send visualization image
                    photo = FSInputFile(vis_path)
                    caption = f"📸 Layout visualization - Page {idx + 1}" if len(output) > 1 else "📸 Layout visualization"
                    await message.answer_photo(photo, caption=caption)
                    
                    # Clean up visualization file
                    os.remove(vis_path)
        
        # Clean up original file
        os.remove(file_path)
        
        logger.info(f"Successfully processed: {file_name}")
        
    except Exception as e:
        logger.error(f"Error processing document: {str(e)}")
        await processing_msg.delete()
        await message.answer(f"❌ **Error processing document:**\n`{str(e)}`", parse_mode="Markdown")

# ============= SETUP BOT =============
async def setup_bot():
    global bot, dp, layout_model
    
    logger.info("Loading PP-DocLayout-S model...")
    layout_model = LayoutDetection(model_name="PP-DocLayout-S")
    logger.info("✅ Model loaded successfully!")
    
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # Register handlers
    dp.message.register(start_command, Command("start"))
    dp.message.register(help_command, Command("help"))
    dp.message.register(status_command, Command("status"))
    dp.message.register(handle_document, lambda message: message.document or message.photo)
    
    logger.info("Bot handlers registered")
    
    # Start polling
    await dp.start_polling(bot)

# ============= MAIN FUNCTION =============
def run_flask():
    """Run Flask app for health checks"""
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False)

def run_bot():
    """Run the bot in asyncio event loop"""
    asyncio.run(setup_bot())

if __name__ == "__main__":
    # Run Flask in a separate thread for health checks
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Flask health check server started")
    
    # Run the bot (this blocks)
    run_bot()
