import os
import sqlite3
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from google.genai import types

# Load Telegram Owner ID & API Keys from environment variables
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))  # Replace with your Telegram ID from @userinfobot

gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

DEFAULT_PROMPT = """
You are a friendly, witty AI assistant in a Telegram group chat.

Language Rules:
1. Auto-detect the language of incoming messages and respond in the matching language (e.g., reply in English for English, German for German, etc.).
2. SPECIAL RULE - MYANGLISH / BURMESE:
   - If a user writes in Burmese Unicode (မြန်မာစာ), reply in natural Burmese Unicode.
   - If a user writes in "Myanglish" (Burmese language phonetically written using English/Latin alphabet, e.g., "mingalar ba", "nay kaung lar", "sar pee pee lar"), AUTO-DETECT this and ALWAYS respond in proper Burmese Unicode script (မြန်မာစာ).
   - Never respond back in Myanglish—always render the answer in standard Burmese Unicode text.

Keep responses natural, concise, and context-aware.
"""

DB_FILE = "chat_history.db"

def init_db():
    """Initialize database tables for chat history and configuration settings."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('system_prompt', ?)", (DEFAULT_PROMPT,))
        conn.commit()

def get_system_prompt() -> str:
    """Fetch active system instruction from database."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM config WHERE key = 'system_prompt'")
        row = cursor.fetchone()
        return row[0] if row else DEFAULT_PROMPT

def set_system_prompt(new_prompt: str):
    """Update active system instruction in database."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO config (key, value) VALUES ('system_prompt', ?)", (new_prompt,))
        conn.commit()

def save_message(chat_id: int, role: str, content: str):
    """Save conversation turns to SQLite database."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content)
        )
        conn.commit()

def load_history(chat_id: int, limit: int = 20):
    """Load conversation history for Gemini multi-turn chat context."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, content FROM (
                SELECT role, content, id FROM messages 
                WHERE chat_id = ? 
                ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
        """, (chat_id, limit))
        rows = cursor.fetchall()

    return [
        types.Content(role=role, parts=[types.Part.from_text(text=content)])
        for role, content in rows
    ]

# OWNER-ONLY COMMAND: Dynamic Personality / Prompt Update
async def set_prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Access denied. Only the bot owner can perform this action.")
        return

    new_prompt = " ".join(context.args).strip()
    if not new_prompt:
        current_prompt = get_system_prompt()
        await update.message.reply_text(
            f"⚠️ Please provide a new prompt.\n\n*Current System Prompt:*\n`{current_prompt}`",
            parse_mode="Markdown"
        )
        return

    set_system_prompt(new_prompt)
    await update.message.reply_text("✅ System prompt updated successfully!", parse_mode="Markdown")

# OWNER-ONLY COMMAND: Clear Chat History
async def clear_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Access denied. Only the bot owner can perform this action.")
        return

    chat_id = update.effective_chat.id
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.commit()

    await update.message.reply_text("🧹 Chat history for this group cleared.")

# MAIN MESSAGE HANDLER
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot.username
    message_text = update.message.text or ""
    chat_id = update.effective_chat.id
    
    is_mentioned = f"@{bot_username}" in message_text
    is_reply_to_bot = (
        update.message.reply_to_message 
        and update.message.reply_to_message.from_user 
        and update.message.reply_to_message.from_user.username == bot_username
    )

    if is_mentioned or is_reply_to_bot or update.message.chat.type == "private":
        clean_prompt = message_text.replace(f"@{bot_username}", "").strip() or "Hello!"
        
        history = load_history(chat_id, limit=20)
        active_prompt = get_system_prompt()
        
        chat = gemini_client.chats.create(
            model="gemini-2.5-flash",
            history=history,
            config=types.GenerateContentConfig(
                system_instruction=active_prompt,
                temperature=0.7,
            )
        )
        
        response = chat.send_message(clean_prompt)
        
        save_message(chat_id, "user", clean_prompt)
        save_message(chat_id, "model", response.text)
        
        await update.message.reply_text(response.text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("GigaBot active. Ready to respond in English, Burmese, or Myanglish!")

if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(os.environ.get("TELEGRAM_TOKEN")).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setprompt", set_prompt_command))
    app.add_handler(CommandHandler("clear", clear_history_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
