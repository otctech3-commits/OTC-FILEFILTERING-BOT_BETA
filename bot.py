import logging
import os
import json
from telegram import Update, File
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from flask import Flask, request, jsonify

# --- Configuration and Setup ---

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Environment variables for bot token and webhook URL
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# In-memory file database for this example.
# IMPORTANT: For persistent storage on Render, you MUST use an external database
# (like PostgreSQL or MongoDB) instead of this dictionary, as Render's local filesystem is ephemeral.
FILE_DATABASE = {}  # {file_id: {file_name, mime_type, user_id, type_hint}}

# Mime types to highlight as "Video" or other specific types
VIDEO_MIMES = [
    'video/mp4', 'video/x-matroska', 'video/quicktime', 'video/mpeg', 
    'video/webm', 'video/x-msvideo'  # Includes MP4, MKV, MOV, AVI, etc.
]
DOCUMENT_MIMES = ['application/pdf', 'application/zip', 'text/plain']
IMAGE_MIMES = ['image/jpeg', 'image/png', 'image/gif']

# --- Utility Functions ---

def get_file_type_hint(mime_type: str) -> str:
    """Provides a user-friendly hint based on the file's MIME type."""
    if mime_type in VIDEO_MIMES:
        return "Video (MP4/MKV/Other)"
    if mime_type in DOCUMENT_MIMES:
        return "Document (PDF/ZIP/Text)"
    if mime_type in IMAGE_MIMES:
        return "Image"
    # General case: try to infer from the MIME subtype
    return mime_type.split('/')[-1].upper() if '/' in mime_type else "Other File"


# --- Telegram Handler Functions ---

async def start(update: Update, context) -> None:
    """Sends a welcome message and explains bot usage."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hello, {user.mention_html()}! 👋\n\n"
        "I am a File Indexing Bot. Send me any file (like MP4, MKV, PDF, etc.) "
        "and I will save its metadata.\n\n"
        "You can search for files using the /search command followed by a keyword, "
        "e.g., <code>/search MyMovie</code>"
    )

async def handle_file(update: Update, context) -> None:
    """Handles any document, video, or audio message, extracts metadata, and stores it."""
    message = update.effective_message
    user_id = message.from_user.id
    
    # Determine the file object and its properties
    file_obj = None
    file_name = "N/A"
    
    # Prioritize 'document' which catches most general files (PDF, ZIP, custom formats)
    if message.document:
        file_obj = message.document
        file_name = file_obj.file_name or "Unnamed Document"
        mime_type = file_obj.mime_type or "application/octet-stream"
        
    # Check for 'video' files (MP4, MKV, etc.)
    elif message.video:
        file_obj = message.video
        file_name = file_obj.file_name or "Unnamed Video"
        mime_type = file_obj.mime_type or "video/mp4"

    # Check for 'audio' files
    elif message.audio:
        file_obj = message.audio
        file_name = file_obj.file_name or "Unnamed Audio"
        mime_type = file_obj.mime_type or "audio/mpeg"

    # If a file object was successfully identified
    if file_obj:
        file_id = file_obj.file_id
        type_hint = get_file_type_hint(mime_type)
        
        # Store metadata
        FILE_DATABASE[file_id] = {
            "file_name": file_name,
            "mime_type": mime_type,
            "user_id": user_id,
            "type_hint": type_hint,
            "file_unique_id": file_obj.file_unique_id
        }
        
        logger.info(f"Stored file {file_name} ({file_id}) for user {user_id}. Type: {type_hint}")

        # Confirmation message
        await message.reply_html(
            f"✅ File indexed successfully!\n"
            f"<b>Name:</b> <code>{file_name}</code>\n"
            f"<b>Type:</b> {type_hint} (MIME: {mime_type})\n"
            f"<b>File ID:</b> <code>{file_id}</code>\n\n"
            "You can now search for this file using keywords from its name."
        )
    else:
        # Should not happen if filters are set correctly, but good for safety
        await message.reply_text("I received a message, but it didn't contain a file I know how to process.")


async def search_files(update: Update, context) -> None:
    """Searches the in-memory database based on user query."""
    if not context.args:
        await update.message.reply_text("Please provide a search keyword after the command, e.g., /search movie.")
        return

    query = " ".join(context.args).lower()
    
    # Get files owned by the current user (simple private filtering)
    user_id = update.effective_user.id
    results = []

    for file_id, data in FILE_DATABASE.items():
        # Check if the file belongs to the user and the name contains the query
        if data["user_id"] == user_id and query in data["file_name"].lower():
            results.append(data)
    
    if not results:
        await update.message.reply_text(f"❌ No files found matching '{query}' in your private index.")
        return

    # Prepare the search results response
    response_text = f"🔎 Found {len(results)} files matching '<code>{query}</code>':\n\n"
    
    for i, data in enumerate(results[:5]): # Show top 5 results
        # Use an index to make it easy to identify
        response_text += (
            f"<b>{i+1}.</b> <code>{data['file_name']}</code>\n"
            f"  Type: {data['type_hint']} | ID: <code>{data['file_unique_id']}</code>\n"
        )
    
    if len(results) > 5:
        response_text += f"\n... and {len(results) - 5} more. Refine your search."
    
    await update.message.reply_html(response_text)


async def error_handler(update: Update, context) -> None:
    """Log the error and send a traceback message to the developer."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    # Optionally notify the user
    if update.effective_message:
        await update.effective_message.reply_text("An internal error occurred. Please try again.")

# --- Application Setup ---

# Initialize the Flask application
app = Flask(__name__)
# Initialize the telegram application
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Add Handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("search", search_files))
# Filter for all file types: documents, videos, and audios
file_filters = filters.Document() | filters.Video() | filters.Audio()
application.add_handler(MessageHandler(file_filters, handle_file))

# Error handler
application.add_error_handler(error_handler)


# --- Webhook Integration for Render Deployment ---

@app.route("/")
def index():
    """Simple health check route."""
    return "Telegram File Filter Bot is running."

@app.route("/" + TELEGRAM_TOKEN, methods=["POST"])
async def telegram_webhook():
    """Handles incoming Telegram updates via POST request."""
    if request.method == "POST":
        # Get the update from the request body
        update_json = request.get_json(force=True)
        # Process the update using the telegram application
        update = Update.de_json(update_json, application.bot)
        await application.process_update(update)
        return jsonify({"status": "ok"})
    return "Method not allowed", 405

@app.route("/set_webhook")
async def set_webhook():
    """Sets the webhook URL for the bot to receive updates."""
    if not WEBHOOK_URL:
        return "WEBHOOK_URL environment variable is not set.", 500

    webhook_path = "/" + TELEGRAM_TOKEN
    full_webhook_url = WEBHOOK_URL.rstrip('/') + webhook_path
    
    # Remove existing webhook first, then set the new one
    await application.bot.delete_webhook()
    success = await application.bot.set_webhook(url=full_webhook_url)

    if success:
        return f"Webhook successfully set to: {full_webhook_url}"
    else:
        return "Failed to set webhook.", 500

# This block is executed when running the script directly (e.g., for local testing or to manually set the webhook)
if __name__ == "__main__":
    # If run locally, you might want to call the set_webhook endpoint after starting the server.
    logger.info("Starting Flask application locally...")
    # In a real Render deployment, Gunicorn handles the server start, but you run this to manually set the webhook.
    # For a Render deployment, you would typically hit the /set_webhook endpoint manually after deployment.
    # Note: Flask's default development server is single-threaded and unsuitable for production.
    # Render uses Gunicorn which addresses this.
    app.run(port=int(os.environ.get("PORT", 5000)))