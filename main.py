import logging
from telethon import TelegramClient, events
import datetime
import asyncio
import openai
import sqlite3
from pathlib import Path
import signal
import sys
from datetime import datetime, timedelta

# Import configuration
from config import (
    API_ID, API_HASH, BOT_TOKEN, CHANNELS,
    OPENAI_API_KEY, GPT_MODEL, SUMMARY_PROMPT_TEMPLATE,
    DIGEST_INTERVAL_MINUTES # Import interval
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai_client = openai.AsyncClient(api_key=OPENAI_API_KEY)

# Database setup
DB_PATH = 'digest.db'  # Use a single database file

def init_database():
    """Initialize SQLite database and create necessary tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_seen TEXT
        )
    ''')
    
    # Create posts table with channel information
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            channel_title TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            content TEXT NOT NULL,
            sent BOOLEAN DEFAULT FALSE
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
    logger.info("Posts database initialized successfully")

async def save_post(channel_id: str, channel_title: str, timestamp: str, content: str):
    """Save a post to the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO posts (channel_id, channel_title, timestamp, content, sent) VALUES (?, ?, ?, ?, FALSE)',
        (channel_id, channel_title, timestamp, content)
    )
    conn.commit()
    conn.close()
    logger.info(f"Saved post from channel {channel_title}")

def get_unsent_posts():
    """Get all unsent posts from the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, channel_title, timestamp, content 
        FROM posts 
        WHERE sent = FALSE 
        ORDER BY timestamp ASC
    ''')
    posts = cursor.fetchall()
    conn.close()
    return posts

def mark_posts_as_sent(post_ids: list):
    """Mark specified post IDs as sent in the database."""
    if not post_ids:
        return
    
    conn = sqlite3.connect(DB_PATH)
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
    """Get posts from the last N hours for manual digest."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Calculate timestamp for N hours ago
    now = datetime.now()
    hours_ago = now - timedelta(hours=hours)
    timestamp_threshold = hours_ago.isoformat()
    
    cursor.execute('''
        SELECT channel_title, timestamp, content
        FROM posts
        WHERE timestamp > ?
        ORDER BY timestamp ASC
    ''', (timestamp_threshold,))
    
    posts = cursor.fetchall()
    conn.close()
    return posts

def count_unsent_posts():
    """Get count of unsent posts from the database."""
    conn = sqlite3.connect(DB_PATH)
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

async def format_digest(posts):
    """Format posts into a readable digest."""
    if not posts:
        return "No posts to include in digest."
        
    # Group posts by channel
    channels = {}
    for channel_title, timestamp, content in posts:
        if channel_title not in channels:
            channels[channel_title] = []
        channels[channel_title].append((timestamp, content))
    
    # Format digest
    digest = "📬 Дайджест постов за последние 4 часа:\n\n"
    
    for channel_title, channel_posts in channels.items():
        for timestamp, content in channel_posts:
            # Convert ISO timestamp to readable format
            dt = datetime.fromisoformat(timestamp)
            time_str = dt.strftime("%H:%M")
            
            # Format post content
            preview = content[:100] + "..." if len(content) > 100 else content
            digest += f"— [{time_str}] {preview}\n\n"
            
    return digest

async def summarize_posts(posts):
    """Generate a summary of posts using OpenAI."""
    if not posts:
        return None
        
    try:
        # Format posts for the prompt
        formatted_posts = "\n\n".join([
            f"[{datetime.fromisoformat(p[1]).strftime('%H:%M')}] {p[2]}" 
            for p in posts if len(p) >= 2
        ])
        
        # Call OpenAI API
        response = await openai_client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT_TEMPLATE},
                {"role": "user", "content": formatted_posts}
            ],
            temperature=0.7,
            max_tokens=250
        )
        
        summary = response.choices[0].message.content.strip()
        return f"🤖 AI-обзор:\n{summary}"
        
    except Exception as e:
        logger.error(f"Error generating summary: {e}")
        return None

async def send_digest(manual=False):
    """Send digest to all registered users."""
    try:
        # Get posts
        posts = get_recent_posts_for_manual_digest() if manual else get_unsent_posts()
        
        if not posts:
            return "No posts to include in digest."
            
        # Format digest
        digest = await format_digest(posts)
        
        # Try to get AI summary
        summary = await summarize_posts(posts)
        if summary:
            digest = f"{digest}\n\n{summary}"
            
        # Send to all users
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users')
        users = cursor.fetchall()
        
        for user_id in users:
            try:
                await bot.send_message(user_id[0], digest)
            except Exception as e:
                logger.error(f"Failed to send digest to user {user_id[0]}: {e}")
                
        # Mark posts as sent if this was an automatic digest
        if not manual:
            cursor.execute('UPDATE posts SET sent = TRUE WHERE sent = FALSE')
            conn.commit()
            
        conn.close()
        return digest
        
    except Exception as e:
        logger.error(f"Error sending digest: {e}")
        return "Error generating digest. Please try again later."

@bot.on(events.NewMessage(pattern='/digest'))
async def digest_handler(event):
    """Handle /digest command - send digest of recent posts."""
    try:
        logger.info(f"Processing /digest command from user {event.sender_id}")
        digest = await send_digest(manual=True)
        await event.respond(digest)
    except Exception as e:
        logger.error(f"Error in digest_handler: {e}")
        await event.respond("Error processing digest command. Please try again later.")

def get_registered_users():
    """Get all registered user IDs from the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

@user_client.on(events.NewMessage(chats=CHANNELS))
async def channel_handler(event):
    """Handle new messages from monitored channels."""
    try:
        # Get channel info
        channel = await event.get_chat()
        channel_id = str(channel.id)
        channel_title = channel.title
        
        # Check if this channel is in our monitored list
        if not any(channel_id in ch or channel.username in ch for ch in CHANNELS):
            logger.debug(f"Ignoring message from non-monitored channel: {channel_title}")
            return
        
        # Get message content
        if event.message.text:
            content = event.message.text
        elif event.message.media:
            content = "[Media message]"
            if hasattr(event.message.media, 'caption') and event.message.media.caption:
                content += f": {event.message.media.caption}"
        else:
            logger.debug(f"Skipping message without content from {channel_title}")
            return
            
        # Save post to database
        timestamp = event.message.date.isoformat()
        await save_post(channel_id, channel_title, timestamp, content)
        
        # Format notification
        time_str = event.message.date.strftime("%H:%M")
        notification = f"📥 Новый пост из {channel_title}\n⏰ Время: {time_str}\n📝 Текст: {content[:100]}{'...' if len(content) > 100 else ''}"
        
        # Send notification to all registered users
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users')
        users = cursor.fetchall()
        conn.close()
        
        for user_id in users:
            try:
                await bot.send_message(user_id[0], notification)
            except Exception as e:
                logger.error(f"Failed to notify user {user_id[0]}: {e}")
                
    except Exception as e:
        logger.error(f"Error in channel_handler: {e}")

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
            summary = await summarize_posts(posts)
            
            # Format the regular digest list
            digest_list = await format_digest(posts)
            
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
    """Handle /status command - show statistics about unsent posts by channel."""
    try:
        # Calculate timestamp for 4 hours ago
        now = datetime.now()
        hours_ago = now - timedelta(hours=4)
        timestamp_threshold = hours_ago.isoformat()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT channel_title, COUNT(*) as post_count
            FROM posts
            WHERE timestamp > ? AND sent = FALSE
            GROUP BY channel_title
        ''', (timestamp_threshold,))
        
        stats = cursor.fetchall()
        
        # Get the earliest unsent post
        cursor.execute('''
            SELECT timestamp
            FROM posts
            WHERE sent = FALSE
            ORDER BY timestamp ASC
            LIMIT 1
        ''')
        earliest_post = cursor.fetchone()
        
        conn.close()
        
        if not stats:
            await event.respond("📊 Статус:\n— Постов готово к отправке: 0\n\nСледующий автодайджест примерно через 60 минут")
            return
            
        # Count total posts
        total_posts = sum(count for _, count in stats)
        
        # Format earliest post time
        earliest_time = "Нет постов"
        if earliest_post:
            earliest_time = datetime.fromisoformat(earliest_post[0]).strftime("%H:%M")
        
        # Calculate next digest time
        next_digest = now + timedelta(minutes=DIGEST_INTERVAL_MINUTES)
        next_digest_str = next_digest.strftime("%H:%M")
        
        response = f"📊 Статус:\n— Постов готово к отправке: {total_posts}\n— Первый пост от: {earliest_time}\n\nСледующий автодайджест примерно в {next_digest_str}"
            
        logger.info(f"Sending status response: {response}")
        await event.respond(response)
        
    except Exception as e:
        logger.error(f"Error in status_handler: {e}")
        await event.respond("Error getting status. Please try again later.")

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