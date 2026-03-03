"""
Telegram Bot using PP-DocLayout-S to detect documents
Returns: "its a document" or "its not document" for every image
"""

import os
import logging
from pathlib import Path
from datetime import datetime
import asyncio
from typing import Tuple
import tempfile

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from paddleocr import LayoutDetection
from PIL import Image
import numpy as np

# ==================== CONFIGURATION ====================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Replace with your actual bot token

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== INITIALIZE PP-DOCLAYOUT-S ====================
print("🔄 Loading PP-DocLayout-S model...")
try:
    # Initialize layout detector (NO OCR needed!)
    detector = LayoutDetection(
        model_name="PP-DocLayout-S",  # Ultra-lightweight document layout model
        device='CPU'  # Force CPU usage
    )
    print("✅ PP-DocLayout-S loaded successfully!")
except Exception as e:
    print(f"❌ Error loading model: {e}")
    detector = None

# ==================== DOCUMENT DETECTION FUNCTION ====================
def is_document(image_path: str) -> Tuple[bool, str, dict]:
    """
    Use PP-DocLayout-S to detect if image contains document layout
    Returns: (is_document, reason, details)
    """
    try:
        if detector is None:
            return False, "Model not loaded", {}
        
        # Run layout detection (vision-only, ~15ms on CPU)
        result = detector.predict(image_path)
        
        # Track detected document elements
        document_elements = []
        confidence_scores = []
        
        # Document elements that indicate it's a document
        doc_indicators = [
            'text', 'paragraph_title', 'document_title', 
            'table', 'figure', 'list', 'header', 'footer',
            'text_block', 'paragraph', 'abstract', 'toc'
        ]
        
        # Analyze detection results
        for detection in result:
            if detection['boxes'] and len(detection['boxes']) > 0:
                for box in detection['boxes']:
                    element_type = box.get('label', 'unknown')
                    confidence = box.get('score', 0)
                    
                    # Check if this element indicates a document
                    if element_type in doc_indicators and confidence > 0.5:
                        document_elements.append({
                            'type': element_type,
                            'confidence': confidence
                        })
                        confidence_scores.append(confidence)
        
        # Decision logic
        if len(document_elements) >= 2:  # Multiple document elements found
            avg_confidence = sum(confidence_scores) / len(confidence_scores)
            reason = f"Found {len(document_elements)} document elements"
            details = {
                'elements': document_elements,
                'avg_confidence': avg_confidence,
                'total_elements': len(document_elements)
            }
            return True, reason, details
            
        elif len(document_elements) == 1:  # Single element found
            if confidence_scores[0] > 0.8:  # High confidence single element
                reason = f"Found high-confidence {document_elements[0]['type']}"
                details = {
                    'elements': document_elements,
                    'avg_confidence': confidence_scores[0],
                    'total_elements': 1
                }
                return True, reason, details
        
        # No document elements found
        return False, "No document layout detected", {}
        
    except Exception as e:
        logger.error(f"Detection error: {e}")
        return False, f"Error: {str(e)}", {}

# ==================== TELEGRAM BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    welcome_text = (
        "📄 *Document Detector Bot*\n\n"
        "Using *PP-DocLayout-S* to detect documents!\n\n"
        "Send me any image and I'll tell you if it's a document or not.\n\n"
        "• ✅ *its a document* - if document layout detected\n"
        "• ❌ *its not document* - if no document layout found\n\n"
        "Works on:\n"
        "📑 Certificates\n"
        "📋 Government IDs\n"
        "🧾 Bills & Receipts\n"
        "📚 Book pages\n"
        "❌ Not fooled by: selfies, memes, landscapes"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message"""
    help_text = (
        "*📚 Commands*\n\n"
        "/start - Welcome message\n"
        "/help - This help\n"
        "/status - Check bot status\n"
        "/stats - View detection statistics\n\n"
        "*How it works:*\n"
        "1. You send an image\n"
        "2. PP-DocLayout-S analyzes layout (15ms!)\n"
        "3. Bot replies with result\n\n"
        "*Note:* This is vision-only, no text reading needed!"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check bot status"""
    if detector:
        status_text = (
            "*✅ Bot Status*\n\n"
            "• Model: PP-DocLayout-S\n"
            "• Type: Vision-only layout detector\n"
            "• Speed: ~15ms per image\n"
            "• Status: Online\n\n"
            "Ready to detect documents!"
        )
    else:
        status_text = (
            "*❌ Bot Status*\n\n"
            "• Model: Not loaded\n"
            "• Status: Error\n\n"
            "Please check server logs."
        )
    await update.message.reply_text(status_text, parse_mode='Markdown')


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user statistics"""
    user_data = context.user_data
    
    total = user_data.get('total_checks', 0)
    docs = user_data.get('documents_found', 0)
    non_docs = user_data.get('non_documents', 0)
    
    if total > 0:
        doc_percent = (docs / total) * 100
        stats_text = (
            f"*📊 Your Statistics*\n\n"
            f"Total checks: {total}\n"
            f"Documents found: {docs}\n"
            f"Non-documents: {non_docs}\n"
            f"Document rate: {doc_percent:.1f}%\n"
        )
    else:
        stats_text = "*📊 Your Statistics*\n\nNo checks yet! Send me an image."
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any image sent to the bot"""
    
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
        processing_msg = await update.message.reply_text("🔍 Analyzing image with PP-DocLayout-S...")
        
        # Check if it's a document using PP-DocLayout-S
        is_doc, reason, details = is_document(file_path)
        
        # Update user statistics
        context.user_data['total_checks'] = context.user_data.get('total_checks', 0) + 1
        if is_doc:
            context.user_data['documents_found'] = context.user_data.get('documents_found', 0) + 1
            response = "✅ its a document"
        else:
            context.user_data['non_documents'] = context.user_data.get('non_documents', 0) + 1
            response = "❌ its not document"
        
        # Add debug info (optional - remove in production)
        if details:
            debug_info = f"\n\n🔍 Debug: {reason}"
            if 'elements' in details:
                elements = [f"{e['type']}({e['confidence']:.2f})" for e in details['elements']]
                debug_info += f"\nElements: {', '.join(elements)}"
            response += debug_info
        
        # Delete processing message
        await processing_msg.delete()
        
        # Send result
        await update.message.reply_text(response)
        
        # Log for monitoring
        logger.info(f"Image processed: {response} - {reason}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
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
            processing_msg = await update.message.reply_text("🔍 Analyzing document with PP-DocLayout-S...")
            
            # Check if it's a document
            is_doc, reason, details = is_document(file_path)
            
            # Update statistics
            context.user_data['total_checks'] = context.user_data.get('total_checks', 0) + 1
            if is_doc:
                context.user_data['documents_found'] = context.user_data.get('documents_found', 0) + 1
                response = "✅ its a document"
            else:
                context.user_data['non_documents'] = context.user_data.get('non_documents', 0) + 1
                response = "❌ its not document"
            
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


# ==================== MAIN FUNCTION ====================

def main():
    """Start the bot"""
    
    # Check if model loaded
    if detector is None:
        print("❌ WARNING: PP-DocLayout-S failed to load!")
        print("   The bot will still run but will return errors.")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("stats", stats))
    
    # Add message handlers
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start bot
    print("\n" + "="*50)
    print("🤖 Document Detector Bot Started!")
    print("="*50)
    print("Model: PP-DocLayout-S")
    print("Type: Vision-only layout detector")
    print("Speed: ~15ms per image")
    print("\nSend images to your bot to test!")
    print("Press Ctrl+C to stop.\n")
    
    # Start polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
