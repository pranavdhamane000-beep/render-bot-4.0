import os
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from paddleocr import LayoutDetection

# ============= CONFIGURATION =============
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Replace with your bot token
DOCUMENT_THRESHOLD = 2  # Number of layout elements needed to classify as document
CONFIDENCE_THRESHOLD = 0.3  # Minimum confidence for detections

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= DOCUMENT CLASSIFIER =============
class DocumentClassifier:
    def __init__(self):
        """Initialize PP-DocLayout-S model through PaddleOCR"""
        logger.info("🔄 Loading PP-DocLayout-S model...")
        try:
            # Load the efficient document layout model
            self.model = LayoutDetection(
                model_name="PP-DocLayout-S",
                # Optional: Set confidence threshold if supported
            )
            logger.info("✅ Model loaded successfully!")
        except Exception as e:
            logger.error(f"❌ Failed to load model: {e}")
            raise

    def classify_image(self, image_path: str) -> dict:
        """
        Classify image as document or non-document
        
        Returns:
            dict: {
                'is_document': bool,
                'detection_count': int,
                'labels': list,
                'confidence_scores': list,
                'detections': list
            }
        """
        try:
            # Run layout detection
            results = self.model.predict(
                image_path,
                batch_size=1,
                layout_nms=True  # Non-Maximum Suppression for cleaner results
            )
            
            # Parse detections
            detections = []
            for result in results:
                if hasattr(result, 'boxes') and result.boxes:
                    for box in result.boxes:
                        detections.append({
                            'label': box.get('label', 'unknown'),
                            'score': box.get('score', 0),
                            'bbox': box.get('coordinate', [])
                        })
            
            # Filter by confidence threshold
            filtered_detections = [d for d in detections if d['score'] >= CONFIDENCE_THRESHOLD]
            
            # Extract labels and scores
            labels = [d['label'] for d in filtered_detections]
            scores = [d['score'] for d in filtered_detections]
            
            # Classify as document if enough layout elements detected
            is_document = len(filtered_detections) >= DOCUMENT_THRESHOLD
            
            return {
                'is_document': is_document,
                'detection_count': len(filtered_detections),
                'total_detections': len(detections),
                'labels': labels,
                'confidence_scores': scores,
                'detections': filtered_detections
            }
            
        except Exception as e:
            logger.error(f"Error classifying image: {e}")
            return {
                'is_document': False,
                'detection_count': 0,
                'total_detections': 0,
                'labels': [],
                'confidence_scores': [],
                'detections': [],
                'error': str(e)
            }

# ============= TELEGRAM BOT HANDLERS =============
class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.classifier = DocumentClassifier()
        self.temp_dir = "temp_images"
        
        # Create temp directory if it doesn't exist
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

    def get_temp_path(self) -> str:
        """Generate unique temp file path"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return os.path.join(self.temp_dir, f"image_{timestamp}.jpg")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = (
            "👋 **Welcome to Document Detection Bot!**\n\n"
            "I use **PP-DocLayout-S** (PaddleOCR) to detect document layouts.\n\n"
            "📄 **What I can detect:**\n"
            "• Document titles and headings\n"
            "• Text paragraphs\n"
            "• Tables and figures\n"
            "• Formulas and algorithms\n"
            "• Headers and footers\n"
            "• Page numbers\n"
            "• References and footnotes\n"
            "• And 15+ more layout elements!\n\n"
            "🔍 **How to use:**\n"
            "Simply send me any image (photo or file), and I'll tell you if it's a document!\n\n"
            f"⚙️ *Detection threshold:* {DOCUMENT_THRESHOLD}+ layout elements\n"
            f"🎯 *Confidence threshold:* {CONFIDENCE_THRESHOLD * 100}%\n\n"
            "📊 **Commands:**\n"
            "/start - Show this message\n"
            "/help - Detailed help\n"
            "/stats - Show model information"
        )
        await update.message.reply_text(welcome_message, parse_mode='Markdown')

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = (
            "📖 **How Document Detection Works**\n\n"
            "1️⃣ **Send any image** - I'll analyze it using PP-DocLayout-S\n"
            "2️⃣ **Layout detection** - I identify document elements like text, tables, etc.\n"
            "3️⃣ **Classification** - If I find 2+ layout elements, it's a document!\n\n"
            "**What makes something a document?**\n"
            "✓ Contains structured text blocks\n"
            "✓ Has titles, paragraphs, or tables\n"
            "✓ Includes typical document elements like headers/footers\n"
            "✓ Shows academic or business document structure\n\n"
            "**Supported image formats:**\n"
            "JPG, PNG, JPEG, and most common image formats\n\n"
            "**Examples of documents:**\n"
            "• Scanned papers and forms\n"
            "• Screenshots of articles\n"
            "• PDF pages converted to images\n"
            "• Academic papers and reports\n\n"
            "**Examples of non-documents:**\n"
            "• Photos of people/places\n"
            "• Nature photography\n"
            "• Abstract artwork\n"
            "• Memes and casual photos"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        stats_text = (
            "📊 **Model Statistics**\n\n"
            "🤖 **Model:** PP-DocLayout-S\n"
            "🏢 **Framework:** PaddleOCR / PaddlePaddle\n"
            "📦 **Model Size:** 4.8 MB\n"
            "⚡ **CPU Speed:** ~10ms per image\n"
            "🎯 **mAP Accuracy:** 70.9% on document datasets\n"
            "📚 **Detection Classes:** 23 types\n\n"
            "**Detectable elements:**\n"
            "• document title | paragraph title | text\n"
            "• page number | abstract | table of contents\n"
            "• references | footnotes | header | footer\n"
            "• algorithm | formula | image | figure\n"
            "• table | caption | seal\n"
            "• aside text | formula number | etc.\n\n"
            f"⚙️ **Current Settings:**\n"
            f"• Document threshold: {DOCUMENT_THRESHOLD} detections\n"
            f"• Confidence threshold: {CONFIDENCE_THRESHOLD * 100}%\n"
            f"• Non-Maximum Suppression: Enabled"
        )
        await update.message.reply_text(stats_text, parse_mode='Markdown')

    async def handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming images"""
        try:
            # Send typing indicator
            await update.message.chat.send_action(action="typing")
            
            # Get the image file
            if update.message.photo:
                photo_file = await update.message.photo[-1].get_file()
            elif update.message.document:
                photo_file = await update.message.document.get_file()
            else:
                await update.message.reply_text("Please send an image file.")
                return
            
            # Notify user we're processing
            processing_msg = await update.message.reply_text("🔍 **Analyzing image layout...**", parse_mode='Markdown')
            
            # Download image to temp file
            temp_path = self.get_temp_path()
            await photo_file.download_to_drive(temp_path)
            
            # Classify the image
            result = self.classifier.classify_image(temp_path)
            
            # Delete temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
            
            # Delete processing message
            await processing_msg.delete()
            
            # Prepare response based on classification
            if result.get('error'):
                error_msg = f"⚠️ **Error:** {result['error']}\n\nPlease try again with a different image."
                await update.message.reply_text(error_msg, parse_mode='Markdown')
                return
            
            if result['is_document']:
                # Format document response with details
                unique_labels = list(set(result['labels']))
                labels_text = "\n".join([f"• {label}" for label in unique_labels[:8]])
                
                response = (
                    f"📄 **✓ DOCUMENT DETECTED**\n\n"
                    f"Found **{result['detection_count']}** layout elements\n"
                    f"_(Confidence threshold: {CONFIDENCE_THRESHOLD * 100}%)_\n\n"
                    f"**Detected elements:**\n{labels_text}"
                )
                
                if len(unique_labels) > 8:
                    response += f"\n\n*...and {len(unique_labels) - 8} more types*"
                
                # Add confidence info
                avg_confidence = sum(result['confidence_scores']) / len(result['confidence_scores']) if result['confidence_scores'] else 0
                response += f"\n\n📊 *Avg confidence:* {avg_confidence:.1%}"
                
            else:
                # Format non-document response
                if result['detection_count'] == 0:
                    response = (
                        f"❌ **NOT A DOCUMENT**\n\n"
                        f"No document layout elements detected.\n\n"
                        f"This appears to be a non-document image (photo, artwork, etc.)."
                    )
                else:
                    response = (
                        f"❌ **NOT A DOCUMENT**\n\n"
                        f"Found only {result['detection_count']} layout element(s)\n"
                        f"_(Need {DOCUMENT_THRESHOLD}+ to classify as document)_\n\n"
                        f"Detected: {', '.join(set(result['labels']))}"
                    )
            
            await update.message.reply_text(response, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error processing image: {e}")
            await update.message.reply_text(
                "⚠️ Sorry, I couldn't process that image. Please try again with a different image."
            )

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle document files"""
        if update.message.document.mime_type and update.message.document.mime_type.startswith('image/'):
            await self.handle_image(update, context)
        else:
            await update.message.reply_text(
                "Please send an image file (JPG, PNG, etc.). I can only analyze images."
            )

    def run(self):
        """Start the bot"""
        # Create application
        app = Application.builder().token(self.token).build()
        
        # Add command handlers
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("stats", self.stats_command))
        
        # Add message handlers
        app.add_handler(MessageHandler(filters.PHOTO, self.handle_image))
        app.add_handler(MessageHandler(filters.Document.IMAGE, self.handle_document))
        
        # Start bot
        logger.info("🚀 Starting Telegram bot...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

# ============= MAIN =============
if __name__ == '__main__':
    # Replace with your bot token
    bot = TelegramBot(BOT_TOKEN)
    bot.run()
