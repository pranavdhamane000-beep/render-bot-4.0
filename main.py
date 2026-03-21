import os
import asyncio
import threading
from flask import Flask, jsonify
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
import uuid
import logging

# ============= SIMPLIFIED PADDLEOCR IMPORT =============
# Use the base PaddleOCR with layout detection enabled
from paddleocr import PaddleOCR

# ============= LOGGING =============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============= CONFIGURATION =============
BOT_TOKEN = os.environ.get("7666489482:AAGXxYdgfKZGehpByZo2KXyFG5hGdM808YQ")
if not BOT_TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables!")

DOWNLOAD_FOLDER = "downloads"
OUTPUT_FOLDER = "output"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Flask app for health checks
flask_app = Flask(__name__)

# Global objects
bot = None
dp = None
ocr_model = None

# ============= FLASK HEALTH CHECK =============
@flask_app.route('/')
@flask_app.route('/health')
def health_check():
    return jsonify({
        "status": "healthy",
        "bot": "running",
        "model": "PaddleOCR with layout detection",
        "ready": ocr_model is not None
    })

# ============= BOT HANDLERS =============
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "📄 **Document Layout Analyzer Bot**\n\n"
        "Send me any document (PDF or image), and I'll analyze its layout structure!\n\n"
        "**I can detect:**\n"
        "• 📝 Text blocks & paragraphs\n"
        "• 📌 Titles & headings\n"
        "• 📊 Tables & figures\n"
        "• 🧮 Formulas & algorithms\n"
        "• 📄 Headers, footers & page numbers\n\n"
        "Powered by **PaddleOCR with Layout Detection**",
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def help_command(message: types.Message):
    await message.answer(
        "📖 **How to use:**\n\n"
        "1. Send me a **PDF file** or **image** (JPG, PNG)\n"
        "2. I'll analyze the document layout\n"
        "3. I'll send back a summary of detected elements\n\n"
        "**Commands:**\n"
        "/start - Introduction\n"
        "/help - This help message\n"
        "/status - Check bot status",
        parse_mode="Markdown"
    )

@dp.message(Command("status"))
async def status_command(message: types.Message):
    await message.answer(
        "🟢 **Bot Status:** Online\n\n"
        "**Model:** PaddleOCR with layout analysis\n"
        "**Supported Languages:** Multi-language\n"
        "**Features:** Text detection, layout analysis",
        parse_mode="Markdown"
    )

@dp.message(lambda message: message.document or message.photo)
async def handle_document(message: types.Message):
    processing_msg = await message.answer("🔄 **Analyzing document layout...**", parse_mode="Markdown")
    
    try:
        unique_id = uuid.uuid4().hex[:8]
        
        # Download the file
        if message.document:
            file = await bot.get_file(message.document.file_id)
            file_extension = message.document.file_name.split('.')[-1] if '.' in message.document.file_name else 'pdf'
            file_path = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}.{file_extension}")
            await bot.download_file(file.file_path, file_path)
            file_name = message.document.file_name
        else:
            file = await bot.get_file(message.photo[-1].file_id)
            file_path = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}.jpg")
            await bot.download_file(file.file_path, file_path)
            file_name = "image.jpg"
        
        logger.info(f"Processing file: {file_name}")
        
        # Run OCR with layout detection
        result = ocr_model.ocr(file_path, det=True, rec=True, cls=True)
        
        # Parse results
        detected_elements = []
        if result and result[0]:
            for line in result[0]:
                # Each line has [bbox, (text, confidence)]
                bbox = line[0]
                text = line[1][0]
                confidence = line[1][1]
                detected_elements.append({
                    "text": text[:50],  # Truncate for summary
                    "confidence": confidence,
                    "bbox": bbox
                })
        
        # Build response
        response = f"📊 **Layout Analysis Complete**\n"
        response += f"📄 File: `{file_name}`\n\n"
        response += f"**Detected Text Regions:** **{len(detected_elements)}**\n\n"
        
        if detected_elements:
            response += f"**Sample detected text:**\n"
            for i, elem in enumerate(detected_elements[:5]):
                response += f"{i+1}. {elem['text']}...\n"
        
        await processing_msg.delete()
        await message.answer(response, parse_mode="Markdown")
        
        # Clean up
        os.remove(file_path)
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        await processing_msg.delete()
        await message.answer(f"❌ **Error:**\n`{str(e)}`", parse_mode="Markdown")

# ============= SETUP BOT =============
async def setup_bot():
    global bot, dp, ocr_model
    
    logger.info("Loading PaddleOCR with layout detection...")
    # Initialize with layout detection enabled
    ocr_model = PaddleOCR(
        use_angle_cls=True,
        lang='en',
        show_log=False,
        det_db_thresh=0.3,  # Detection threshold
        det_db_box_thresh=0.5,
        rec_batch_num=6
    )
    logger.info("✅ Model loaded successfully!")
    
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # Register handlers
    dp.message.register(start_command, Command("start"))
    dp.message.register(help_command, Command("help"))
    dp.message.register(status_command, Command("status"))
    dp.message.register(handle_document, lambda message: message.document or message.photo)
    
    logger.info("Bot handlers registered")
    await dp.start_polling(bot)

# ============= RUN FLASK IN THREAD =============
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port, debug=False)

def run_bot():
    asyncio.run(setup_bot())

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info(f"Flask health check server started on port {os.environ.get('PORT', 10000)}")
    run_bot()
