import logging
from telethon import TelegramClient, events, sync
from telethon.sessions import StringSession
import datetime
import asyncio
import sqlite3
from pathlib import Path

# Import configuration
from config import API_ID, API_HASH, BOT_TOKEN, CHANNEL_USERNAME, DIGEST_INTERVAL_MINUTES

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize the Telegram client for the bot
# We use the bot token for authentication
bot = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Initialize a separate client for the user account
# This client will listen to the channel
# You might be prompted for your phone number and code the first time
# We'll use a string session to avoid re-authenticating every time
# Get your session string using a separate script or tool if needed,
# or let Telethon create 'user_session.session' file on first run.
user_client = TelegramClient('user_session', API_ID, API_HASH)

# Database setup
DB_PATH = 'digest.db'

def init_database():
    """Initialize SQLite database and create necessary tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create posts table with sent flag
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            content TEXT NOT NULL,
            sent BOOLEAN DEFAULT FALSE
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

def save_post(timestamp: str, content: str):
    """Save a new post to the database.
    
    Args:
        timestamp: ISO format timestamp
        content: Post content/text
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        'INSERT INTO posts (timestamp, content) VALUES (?, ?)',
        (timestamp, content)
    )
    
    conn.commit()
    conn.close()
    logger.info(f"Saved new post with timestamp {timestamp}")

def get_recent_posts(hours: int = 2):
    """Get unsent posts from the last specified hours.
    
    Args:
        hours: Number of hours to look back
        
    Returns:
        list: List of tuples (timestamp, content)
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Calculate cutoff time
    cutoff_time = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    
    # Only select posts that haven't been sent yet
    cursor.execute(
        'SELECT timestamp, content FROM posts WHERE timestamp > ? AND sent = FALSE ORDER BY timestamp ASC',
        (cutoff_time,)
    )
    
    posts = cursor.fetchall()
    
    # Mark retrieved posts as sent
    if posts:
        cursor.execute(
            'UPDATE posts SET sent = TRUE WHERE timestamp > ? AND sent = FALSE',
            (cutoff_time,)
        )
    
    conn.commit()
    conn.close()
    
    return posts

def format_digest(posts, is_auto=False):
    """Format posts into a readable digest.
    
    Args:
        posts: List of (timestamp, content) tuples
        is_auto: Boolean indicating if this is an automatic digest
        
    Returns:
        str: Formatted digest message
    """
    # Choose header based on digest type
    header = "📬 Автодайджест:" if is_auto else "📬 Дайджест:"
    digest = f"{header}\n"
    
    for timestamp, content in posts:
        # Convert ISO timestamp to time only
        time_str = datetime.datetime.fromisoformat(timestamp).strftime("%H:%M")
        # Get the first line of the content as a summary
        text = content.split('\n')[0] if content else "[Медиа-сообщение]"
        digest += f"— [{time_str}] {text}\n"
    
    return digest

async def send_digest(event=None, hours: int = 2):
    """Send digest to the user.
    
    Args:
        event: Optional event object for command-triggered digests
        hours: Number of hours to look back for posts
    """
    # Get recent posts from database
    posts = get_recent_posts(hours)
    
    if not posts:
        if event:  # Only respond if this was triggered by a command
            await event.respond("No posts collected in the last 2 hours. Check back later!")
        return
    
    # Format the digest (auto=True if no event, meaning it's an automatic digest)
    digest = format_digest(posts, is_auto=not bool(event))
    
    try:
        # If triggered by command, respond to the event
        # Otherwise, send to the stored user ID
        if event:
            await event.respond(digest)
        elif hasattr(bot, 'sender_id') and bot.sender_id:
            await bot.send_message(bot.sender_id, digest)
            logger.info(f"Sent automatic digest to user {bot.sender_id}")
    except Exception as e:
        logger.error(f"Error sending digest: {e}")

async def automatic_digest_task():
    """Background task that sends digest periodically."""
    logger.info(f"Starting automatic digest task (interval: {DIGEST_INTERVAL_MINUTES} minutes)")
    
    while True:
        try:
            # Wait for the specified interval
            await asyncio.sleep(DIGEST_INTERVAL_MINUTES * 60)
            
            # Send digest
            await send_digest()
            
        except Exception as e:
            logger.error(f"Error in automatic digest task: {e}")
            # Wait a bit before retrying in case of error
            await asyncio.sleep(60)

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Handler for the /start command."""
    sender = await event.get_sender()
    logger.info(f"User {sender.id} started the bot.")
    await event.respond('Hello! I will now collect messages from the channel. Use /digest to get a summary of collected posts.')
    # Store the user ID to send messages later
    # In a real application, you might want a more robust way to manage users
    bot.sender_id = sender.id

@bot.on(events.NewMessage(pattern='/digest'))
async def digest_handler(event):
    """Handler for the /digest command."""
    sender = await event.get_sender()
    logger.info(f"User {sender.id} requested a digest.")
    
    # For manual digest requests, we'll show all recent posts regardless of sent status
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Calculate cutoff time
    cutoff_time = (datetime.datetime.now() - datetime.timedelta(hours=2)).isoformat()
    
    # Get all recent posts for manual digest
    cursor.execute(
        'SELECT timestamp, content FROM posts WHERE timestamp > ? ORDER BY timestamp ASC',
        (cutoff_time,)
    )
    
    posts = cursor.fetchall()
    conn.close()
    
    if not posts:
        await event.respond("No posts collected in the last 2 hours. Check back later!")
        return
        
    # Format and send the digest
    digest = format_digest(posts, is_auto=False)
    await event.respond(digest)

@user_client.on(events.NewMessage(chats=CHANNEL_USERNAME))
async def channel_handler(event):
    """Handler for new messages in the specified channel."""
    # Save the message to database
    timestamp = event.message.date.isoformat()
    content = event.message.text or "[Медиа-сообщение]"
    save_post(timestamp, content)
    logger.info(f"Saved message {event.message.id} to database")
    
    # Check if the bot has been started and we know who to send the message to
    if hasattr(bot, 'sender_id') and bot.sender_id:
        try:
            # Notify the user that a new post was collected
            await bot.send_message(bot.sender_id, f"📥 Новый пост из канала @{CHANNEL_USERNAME} добавлен в базу. Используйте /digest для просмотра.")
            logger.info(f"Notified user {bot.sender_id} about new message {event.message.id}")
        except Exception as e:
            logger.error(f"Could not notify user: {e}")
    else:
        logger.warning(f"Received message {event.message.id} but no user started the bot yet.")

async def main():
    """Main function to start both clients and background tasks."""
    # Initialize database
    init_database()
    
    logger.info("Starting user client...")
    await user_client.start()
    logger.info("User client started.")

    # Start the automatic digest task
    asyncio.create_task(automatic_digest_task())
    logger.info("Automatic digest task started.")

    logger.info(f"Bot started. Listening for messages in @{CHANNEL_USERNAME}...")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    # Run the main function
    user_client.loop.run_until_complete(main()) 