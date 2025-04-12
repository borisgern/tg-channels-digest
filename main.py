import logging
from telethon import TelegramClient, events
import datetime
import asyncio
import openai
import sqlite3
from pathlib import Path
import signal
import sys

# Import configuration
from config import (
    API_ID, API_HASH, BOT_TOKEN, CHANNEL_USERNAME,
    OPENAI_API_KEY, GPT_MODEL, SUMMARY_PROMPT_TEMPLATE,
    DIGEST_INTERVAL_MINUTES # Import interval
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai_client = openai.AsyncClient(api_key=OPENAI_API_KEY)

# Database setup
DB_PATH = 'users.db'
POSTS_DB_PATH = 'posts.db'

def init_database():
    """Initialize SQLite database and create users table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_seen TEXT NOT NULL,
            digest_time TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

def register_user(user_id: int, username: str = None):
    """Register a new user in the database if not exists.
    
    Args:
        user_id: Telegram user ID
        username: Optional username
    
    Returns:
        bool: True if new user was registered, False if already exists
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if user exists
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    if cursor.fetchone() is not None:
        conn.close()
        return False
    
    # Register new user
    now = datetime.datetime.now().isoformat()
    cursor.execute(
        'INSERT INTO users (user_id, username, first_seen) VALUES (?, ?, ?)',
        (user_id, username, now)
    )
    
    conn.commit()
    conn.close()
    logger.info(f"New user registered: {user_id} ({username})")
    return True

def init_posts_database():
    """Initialize SQLite database for posts and create table if it doesn't exist."""
    conn = sqlite3.connect(POSTS_DB_PATH)
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
    logger.info("Posts database initialized successfully")

def save_post_to_db(timestamp: str, content: str):
    """Save a new post to the posts database.
    
    Args:
        timestamp: ISO format timestamp
        content: Post content/text
    """
    conn = sqlite3.connect(POSTS_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        'INSERT INTO posts (timestamp, content) VALUES (?, ?)',
        (timestamp, content)
    )
    
    conn.commit()
    conn.close()
    logger.info(f"Saved new post to DB with timestamp {timestamp}")

def get_unsent_posts():
    """Get unsent posts from the database."""
    conn = sqlite3.connect(POSTS_DB_PATH)
    cursor = conn.cursor()
    
    # Select unsent posts
    cursor.execute(
        'SELECT id, timestamp, content FROM posts WHERE sent = FALSE ORDER BY timestamp ASC'
    )
    posts = cursor.fetchall()
    conn.close()
    return posts

def mark_posts_as_sent(post_ids: list):
    """Mark specified post IDs as sent in the database."""
    if not post_ids:
        return
    
    conn = sqlite3.connect(POSTS_DB_PATH)
    cursor = conn.cursor()
    
    placeholders = ', '.join('?' * len(post_ids))
    cursor.execute(
        f'UPDATE posts SET sent = TRUE WHERE id IN ({placeholders})',
        post_ids
    )
    
    conn.commit()
    conn.close()
    logger.info(f"Marked {len(post_ids)} posts as sent")

def get_recent_posts_for_manual_digest(hours: int = 4):
    """Get posts from the last N hours for manual /digest command."""
    conn = sqlite3.connect(POSTS_DB_PATH)
    cursor = conn.cursor()
    
    # Calculate cutoff time
    cutoff_time = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    
    # Select posts regardless of sent status
    cursor.execute(
        'SELECT timestamp, content FROM posts WHERE timestamp > ? ORDER BY timestamp ASC',
        (cutoff_time,)
    )
    posts = cursor.fetchall()
    conn.close()
    return posts

def count_unsent_posts():
    """Get count of unsent posts from the database."""
    conn = sqlite3.connect(POSTS_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM posts WHERE sent = FALSE')
    count = cursor.fetchone()[0]
    
    # Get the earliest unsent post timestamp
    cursor.execute('SELECT MIN(timestamp) FROM posts WHERE sent = FALSE')
    earliest_timestamp = cursor.fetchone()[0]
    
    conn.close()
    return count, earliest_timestamp

# Initialize the Telegram clients
bot = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
user_client = TelegramClient('user_session', API_ID, API_HASH)

# In-memory post cache (no longer used for primary storage)
# post_cache = [] 

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Handle the /start command and register new users"""
    # Get sender info
    sender = await event.get_sender()
    user_id = sender.id
    username = sender.username
    
    # Try to register user
    is_new_user = register_user(user_id, username)
    
    welcome_msg = '👋 Привет! Ты зарегистрирован. ' if is_new_user else '👋 Привет! '
    welcome_msg += '''Я буду сохранять сообщения и отправлять их дайджестом.

Доступные команды:
/digest - получить дайджест за последние 4 часа
/status - узнать количество постов для следующего дайджеста'''
    
    await event.respond(welcome_msg)

async def format_posts(posts, is_auto=False):
    """Format posts into a readable list.
    
    Args:
        posts: List of (timestamp, content) or (id, timestamp, content) tuples
        is_auto: Boolean indicating if this is an automatic digest
        
    Returns:
        str: Formatted digest list
    """
    header = "📬 Автодайджест:" if is_auto else "📬 Дайджест постов за последние 4 часа:"
    digest_list = f"{header}\n"
    
    for post_data in posts:
        # Handle different tuple structures
        if len(post_data) == 3: # (id, timestamp, content)
            _, timestamp, content = post_data
        elif len(post_data) == 2: # (timestamp, content)
            timestamp, content = post_data
        else:
            continue # Skip invalid data
        
        # Convert ISO timestamp to time only
        try:
            time_str = datetime.datetime.fromisoformat(timestamp).strftime("%H:%M")
        except ValueError:
            time_str = "??:??" # Handle potential malformed timestamp
            
        # Get the first line of the content as a summary
        text = content.split('\n')[0] if content else "[Медиа-сообщение]"
        digest_list += f"— [{time_str}] {text}\n"
    
    return digest_list

async def generate_ai_summary(posts):
    """Generate an AI summary of the posts using OpenAI."""
    if not posts:
        return ""
    
    try:
        # Format posts for the prompt, handling different tuple structures
        formatted_posts = "\n\n".join([
            f"[{datetime.datetime.fromisoformat(p[1]).strftime('%H:%M')}] {p[2]}" if len(p) == 3 else 
            f"[{datetime.datetime.fromisoformat(p[0]).strftime('%H:%M')}] {p[1]}" 
            for p in posts if len(p) >= 2
        ])
        
        # Prepare the prompt
        prompt = SUMMARY_PROMPT_TEMPLATE.format(posts=formatted_posts)
        
        # Call OpenAI API
        response = await openai_client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that summarizes Telegram channel posts."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=250
        )
        
        # Extract and return the summary
        summary = response.choices[0].message.content.strip()
        logger.info("Successfully generated AI summary")
        return summary
        
    except Exception as e:
        logger.error(f"Error generating AI summary: {e}")
        return "⚠️ Не удалось создать AI-обзор."

@bot.on(events.NewMessage(pattern='/digest'))
async def digest_handler(event):
    """Handle the /digest command - send posts from last 4 hours"""
    
    posts = get_recent_posts_for_manual_digest(hours=4)
    
    if not posts:
        await event.respond('📭 Нет постов за последние 4 часа.')
        return

    try:
        # Generate AI summary
        summary = await generate_ai_summary(posts)
        
        # Format the digest list
        digest_list = await format_posts(posts, is_auto=False)

        # Combine summary and digest
        full_message = f"{summary}\n\n{digest_list}"
        
        # Send the combined message
        await event.respond(full_message)
        
        logger.info("Manual digest sent")
        
    except Exception as e:
        logger.error(f"Error sending manual digest: {e}")
        # Fallback: Send only the list if summary fails
        try:
            digest_list = await format_posts(posts, is_auto=False)
            await event.respond(digest_list)
        except Exception as e2:
            logger.error(f"Failed to send fallback digest list: {e2}")
            await event.respond("Произошла ошибка при формировании дайджеста.")

def get_registered_users():
    """Get all registered user IDs from the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

@user_client.on(events.NewMessage(chats=CHANNEL_USERNAME))
async def channel_handler(event):
    """Handle new messages from the channel and save to DB"""
    # Get message details
    timestamp = event.message.date.isoformat() # Use message date
    content = event.message.text or "[Медиа-сообщение]"
    message_id = event.message.id
    
    # Save post to database
    save_post_to_db(timestamp, content)
    
    # Prepare notification message for users
    time_str = event.message.date.strftime("%H:%M")
    text = content.split('\n')[0] if content else "[Медиа-сообщение]"
    if len(text) > 100:
        text = text[:100] + "..."
    
    notification = f"📥 Новый пост из @{CHANNEL_USERNAME}\n"
    notification += f"⏰ Время: {time_str}\n"
    notification += f"📝 Текст: {text}\n"
    if event.message.media:
        notification += f"📎 Тип медиа: {event.message.media.__class__.__name__}\n"
    # notification += f"\nВсего постов в кэше: {len(post_cache)}" # Remove cache count
    
    # Send notification to all registered users
    for user_id in get_registered_users():
        try:
            await bot.send_message(user_id, notification)
        except Exception as e:
            logger.error(f"Failed to send notification to user {user_id}: {e}")
    
    # Log to console
    logger.info(f"New post (ID: {message_id}) saved to DB")

async def automatic_digest_task():
    """Background task that sends digest periodically."""
    logger.info(f"Starting automatic digest task (interval: {DIGEST_INTERVAL_MINUTES} minutes)")
    
    while True:
        try:
            # Wait for the specified interval
            await asyncio.sleep(DIGEST_INTERVAL_MINUTES * 60)
            
            logger.info("Running automatic digest job...")
            
            # Get unsent posts
            posts = get_unsent_posts()
            
            if not posts:
                logger.info("No new unsent posts for automatic digest.")
                continue
            
            logger.info(f"Found {len(posts)} unsent posts for digest.")
            
            # Generate AI summary
            summary = await generate_ai_summary(posts)
            
            # Format the regular digest list
            digest_list = await format_posts(posts, is_auto=True)
            
            # Combine summary and digest
            full_message = f"{summary}\n\n{digest_list}"
            
            # Send the combined message to all registered users
            sent_to_users = 0
            for user_id in get_registered_users():
                try:
                    await bot.send_message(user_id, full_message)
                    sent_to_users += 1
                except Exception as e:
                    logger.error(f"Failed to send auto-digest to user {user_id}: {e}")
            
            logger.info(f"Sent automatic digest to {sent_to_users} users.")
            
            # Mark posts as sent
            post_ids = [p[0] for p in posts]
            mark_posts_as_sent(post_ids)
            
        except asyncio.CancelledError:
            logger.info("Automatic digest task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in automatic digest task: {e}")
            # Wait a bit before retrying in case of error
            await asyncio.sleep(60)

async def shutdown(signal, loop):
    """Cleanup tasks tied to the service's shutdown."""
    logger.info(f"Received exit signal {signal.name}...")
    
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    
    logger.info(f"Cancelling {len(tasks)} outstanding tasks")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

@bot.on(events.NewMessage(pattern='/status'))
async def status_handler(event):
    """Handle the /status command - show statistics about pending posts"""
    try:
        # Get count of unsent posts
        post_count, earliest_timestamp = count_unsent_posts()
        
        if post_count == 0:
            await event.respond('📊 Статус:\n— Нет новых постов для следующего дайджеста')
            return
        
        # Format message
        status = "📊 Статус:\n"
        status += f"— Постов готово к отправке: {post_count}\n"
        
        # Add info about earliest post if available
        if earliest_timestamp:
            try:
                earliest_time = datetime.datetime.fromisoformat(earliest_timestamp)
                time_str = earliest_time.strftime("%H:%M")
                status += f"— Первый пост от: {time_str}\n"
            except ValueError:
                pass
        
        # Add info about next digest
        next_digest = datetime.datetime.now() + datetime.timedelta(minutes=DIGEST_INTERVAL_MINUTES)
        next_digest_str = next_digest.strftime("%H:%M")
        status += f"\nСледующий автодайджест примерно в {next_digest_str}"
        
        await event.respond(status)
        logger.info("Status info sent")
        
    except Exception as e:
        logger.error(f"Error sending status: {e}")
        await event.respond("❌ Произошла ошибка при получении статуса.")

async def main():
    """Start the bot and user client"""
    # Initialize databases
    init_database() # users.db
    init_posts_database() # posts.db
    
    # Handle shutdown gracefully
    loop = asyncio.get_event_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    for s in signals:
        loop.add_signal_handler(
            s, lambda s=s: asyncio.create_task(shutdown(s, loop))
        )
    
    await bot.start()
    await user_client.start()
    
    logger.info("Bot started successfully")
    
    # Start the automatic digest task
    auto_digest_task = asyncio.create_task(automatic_digest_task())
    
    try:
        # Run clients and background task
        await asyncio.gather(
            bot.run_until_disconnected(), 
            user_client.run_until_disconnected(),
            auto_digest_task # Add task to gather
        )
    finally:
        # Cleanup
        if not auto_digest_task.done():
             auto_digest_task.cancel()
        await bot.disconnect()
        await user_client.disconnect()
        logger.info("Bot stopped gracefully")

if __name__ == '__main__':
    try:
        user_client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    finally:
        user_client.loop.close()
        logger.info("Successfully shutdown the bot") 