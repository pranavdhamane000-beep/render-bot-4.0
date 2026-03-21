import os
import asyncio
import threading
import uuid
import logging
from flask import Flask
from aiogram import Bot, Dispatcher, types
from paddleocr import PPStructure

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

flask_app = Flask(__name__)
bot = None
dp = None
layout_engine = None

def load_models():
    global layout_engine
    logger.info("Loading PP-DocLayout-S...")
    layout_engine = PPStructure(
        layout_model_dir=None,
        table=False,
        ocr=True,
        show_log=False,
        device="cpu"
    )
    logger.info("PP-DocLayout-S loaded successfully!")

async def handle_document(message: types.Message):
    processing_msg = await message.answer("🧠 Analyzing document with AI...")
    
    try:
        unique_id = uuid.uuid4().hex[:8]
        
        if message.document:
            file = await bot.get_file(message.document.file_id)
            file_path = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}.pdf")
            await bot.download_file(file.file_path, file_path)
        elif message.photo:
            file = await bot.get_file(message.photo[-1].file_id)
            file_path = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}.jpg")
            await bot.download_file(file.file_path, file_path)
        else:
            await processing_msg.delete()
            await message.answer("❌ Please send a document or photo")
            return

        result = layout_engine(file_path)
        
        summary = {}
        for region in result:
            label = region.get('type', 'unknown')
            summary[label] = summary.get(label, 0) + 1

        if not summary:
            await message.answer("❌ No layout elements detected")
            await processing_msg.delete()
            os.remove(file_path)
            return

        response = "📊 **Layout Analysis Results**\n\n"
        for label, count in summary.items():
            response += f"• **{label.upper()}**: {count}\n"

        if "table" in summary or "title" in summary:
            response = "📄 **DOCUMENT DETECTED**\n\n" + response
        else:
            response = "🖼️ **NON-DOCUMENT / IMAGE**\n\n" + response

        await processing_msg.delete()
        await message.answer(response)
        os.remove(file_path)

    except Exception as e:
        logger.error(f"Error: {e}")
        await processing_msg.delete()
        await message.answer(f"❌ Error: {str(e)[:100]}")

async def setup_bot():
    global bot, dp
    load_models()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.message.register(handle_document, lambda m: m.document or m.photo)
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=lambda: flask_app.run(host='0.0.0.0', port=10000, debug=False), daemon=True).start()
    asyncio.run(setup_bot())
