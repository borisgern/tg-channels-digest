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
import os

# Import configuration
from config import (
    API_ID, API_HASH, BOT_TOKEN, CHANNELS,
    OPENAI_API_KEY, GPT_MODEL, SUMMARY_PROMPT_TEMPLATE,
    DIGEST_INTERVAL_MINUTES # Import interval
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Log imported configuration - This should now be the definitive value
logger.info(f"Using DIGEST_INTERVAL_MINUTES from config: {DIGEST_INTERVAL_MINUTES} minutes")

# Debug: Print all environment variables
logger.info(f"Environment variables after import: {dict(os.environ)}")

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
    now = datetime.now().isoformat()
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
    try:
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
        
        # Log the number of unsent posts found
        logger.info(f"Found {len(posts)} unsent posts")
        
        # Log the first few posts for debugging
        if posts:
            for i, post in enumerate(posts[:3]):
                logger.debug(f"Post {i+1}: ID={post[0]}, Channel={post[1]}, Time={post[2]}")
        
        return posts
    except Exception as e:
        logger.error(f"Error getting unsent posts: {e}")
        return []

def mark_posts_as_sent(post_ids: list):
    """Mark specified post IDs as sent in the database."""
    if not post_ids:
        return
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Use parameterized query to avoid SQL injection
        placeholders = ', '.join('?' * len(post_ids))
        cursor.execute(
            f'UPDATE posts SET sent = TRUE WHERE id IN ({placeholders})',
            post_ids
        )
        
        conn.commit()
        conn.close()
        logger.info(f"Marked {len(post_ids)} posts as sent")
    except Exception as e:
        logger.error(f"Error marking posts as sent: {e}")

def get_recent_posts_for_manual_digest(hours=4):
    """Get posts from the last N hours for manual digest."""
    try:
        # Calculate timestamp for N hours ago
        now = datetime.now()
        hours_ago = now - timedelta(hours=hours)
        timestamp_threshold = hours_ago.isoformat()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT channel_title, timestamp, content
            FROM posts
            WHERE timestamp > ? AND sent = FALSE
            ORDER BY timestamp ASC
        ''', (timestamp_threshold,))
        
        posts = cursor.fetchall()
        conn.close()
        
        # Validate and clean posts data
        valid_posts = []
        for post in posts:
            try:
                # Ensure we have exactly 3 values
                if len(post) != 3:
                    logger.warning(f"Skipping post with invalid format: {post}")
                    continue
                    
                channel_title, timestamp, content = post
                
                # Validate timestamp format
                try:
                    datetime.fromisoformat(timestamp)
                except ValueError:
                    logger.warning(f"Invalid timestamp format: {timestamp}")
                    continue
                    
                valid_posts.append(post)
            except Exception as e:
                logger.error(f"Error processing post: {e}")
                continue
                
        return valid_posts
        
    except Exception as e:
        logger.error(f"Error getting recent posts: {e}")
        return []

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
    sender = None
    user_id = None
    username = None
    try:
        # Get sender info
        sender = await event.get_sender()
        user_id = sender.id
        username = sender.username
        logger.info(f"Received /start command from user_id={user_id}, username={username}")

        # Try to register user
        logger.info(f"Attempting to register user {user_id}...")
        is_new_user = register_user(user_id, username)
        logger.info(f"register_user returned: {is_new_user} for user_id={user_id}")

        welcome_msg = '👋 Привет! Ты зарегистрирован. ' if is_new_user else '👋 Привет! Ты уже был зарегистрирован. '
        welcome_msg += '''Я буду сохранять сообщения и отправлять их дайджестом.

Доступные команды:
/digest - получить дайджест за последние 4 часа
/status - узнать количество постов для следующего дайджеста'''

        await event.respond(welcome_msg)
        logger.info(f"Sent welcome message to user_id={user_id}")

    except Exception as e:
        logger.error(f"Error in start_handler for user_id={user_id}: {e}", exc_info=True)
        try:
            await event.respond("Произошла ошибка при обработке команды /start.")
        except Exception as resp_err:
             logger.error(f"Failed to send error message to user_id={user_id}: {resp_err}")

async def format_digest(posts):
    """Format posts into a readable digest."""
    if not posts:
        return "No posts to include in digest."

    # Group posts by channel
    channels = {}
    for post in posts:
        # Unpack all 4 values returned by get_unsent_posts
        post_id, channel_title, timestamp, content = post
        if channel_title not in channels:
            channels[channel_title] = []
        # Store timestamp and content for formatting
        channels[channel_title].append((timestamp, content))

    # Format digest
    digest = "📬 Дайджест неотправленных постов:\n\n"

    for channel_title, channel_posts in channels.items():
        digest += f"**Канал {channel_title}**\n"
        for timestamp, content in channel_posts:
            # Convert ISO timestamp to readable format
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime("%H:%M")
            except ValueError:
                time_str = "[invalid time]"

            # Format post content
            preview = content[:100] + "..." if len(content) > 100 else content
            digest += f"— [{time_str}] {preview}\n\n"

    return digest

async def summarize_posts(posts):
    """Generate a summary of posts using OpenAI."""
    if not posts:
        return None
        
    try:
        # Format posts for the prompt, with validation
        formatted_posts = []
        for post in posts:
            try:
                if len(post) != 3:
                    continue
                    
                channel_title, timestamp, content = post
                
                # Validate timestamp
                try:
                    time_str = datetime.fromisoformat(timestamp).strftime('%H:%M')
                except ValueError:
                    logger.warning(f"Skipping post with invalid timestamp: {timestamp}")
                    continue
                    
                formatted_posts.append(f"[{time_str}] {content}")
            except Exception as e:
                logger.error(f"Error formatting post for summary: {e}")
                continue
                
        if not formatted_posts:
            logger.warning("No valid posts to summarize")
            return None
            
        # Join all valid posts
        posts_text = "\n\n".join(formatted_posts)
        
        # Call OpenAI API
        response = await openai_client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT_TEMPLATE},
                {"role": "user", "content": posts_text}
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
            
        # Extract post IDs for marking as sent later
        post_ids = []
        for post in posts:
            try:
                # Check if post has an ID (first element)
                if len(post) > 0 and isinstance(post[0], int):
                    post_ids.append(post[0])
            except Exception as e:
                logger.error(f"Error extracting post ID: {e}")
            
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
        if not manual and post_ids:
            mark_posts_as_sent(post_ids)
            logger.info(f"Marked {len(post_ids)} posts as sent after manual digest")
            
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
    logger.info(f"get_registered_users: Found {len(users)} users: {users}")
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
    logger.info(f"Using DIGEST_INTERVAL_MINUTES from config: {DIGEST_INTERVAL_MINUTES}")
    
    # Debug: Print all environment variables
    logger.info(f"Environment variables: {dict(os.environ)}")
    
    while True:
        try:
            # Wait for the specified interval using the value directly from config
            current_interval = DIGEST_INTERVAL_MINUTES
            logger.info(f"Waiting for {current_interval} minutes before next digest")
            await asyncio.sleep(current_interval * 60)
            
            logger.info("Running automatic digest job...")
            
            # Get unsent posts
            posts = get_unsent_posts()
            
            if not posts:
                logger.info("No new unsent posts for automatic digest.")
                continue
            
            logger.info(f"Found {len(posts)} unsent posts for digest.")
            
            # Extract post IDs for marking as sent later
            post_ids = []
            for post in posts:
                try:
                    # Check if post has an ID (first element)
                    if len(post) > 0 and isinstance(post[0], int):
                        post_ids.append(post[0])
                except Exception as e:
                    logger.error(f"Error extracting post ID: {e}")
            
            # Generate AI summary
            summary = await summarize_posts(posts)
            
            # Format the regular digest list
            digest_list = await format_digest(posts)
            
            # Combine summary and digest
            # Ensure summary is not None before adding
            if summary:
                full_message = f"{summary}\n\n{digest_list}"
            else:
                full_message = digest_list # Send only digest if summary failed
            
            # Send the combined message to all registered users
            sent_to_users = 0
            registered_users = get_registered_users() # Get users first
            if not registered_users:
                logger.warning("No registered users found to send digest to. Skipping sending and marking as sent.")
            else:
                for user_id in registered_users:
                    try:
                        await bot.send_message(user_id, full_message)
                        sent_to_users += 1
                    except Exception as e:
                        logger.error(f"Failed to send auto-digest to user {user_id}: {e}")

                logger.info(f"Sent automatic digest to {sent_to_users} users.")

                # Mark posts as sent ONLY if sent to at least one user
                if sent_to_users > 0 and post_ids:
                    mark_posts_as_sent(post_ids)
                    logger.info(f"Marked {len(post_ids)} posts as sent after automatic digest")
                elif not post_ids:
                    logger.warning("No post IDs found to mark as sent")
                else: # sent_to_users == 0
                    logger.warning("Digest was not sent to any users, posts will NOT be marked as sent.")

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
        
        # Get total unsent count
        cursor.execute('SELECT COUNT(*) FROM posts WHERE sent = FALSE')
        total_unsent_count = cursor.fetchone()[0]
        
        # Get registered user count
        cursor.execute('SELECT COUNT(*) FROM users')
        registered_user_count = cursor.fetchone()[0]
        
        conn.close()
        
        # Fix formatting with \\n for newlines
        response = f"📊 Статус:\n"
        response += f"— Зарегистрировано пользователей: {registered_user_count}\n"
        response += f"— Всего неотправленных постов: {total_unsent_count}\n"

        if not earliest_post:
            response += "— Самый ранний пост: Нет неотправленных постов\n"
        else:
            try:
                earliest_dt = datetime.fromisoformat(earliest_post[0])
                earliest_time = earliest_dt.strftime("%Y-%m-%d %H:%M")
                response += f"— Самый ранний пост: {earliest_time}\n"
            except ValueError:
                response += "— Самый ранний пост: Неверный формат времени\n"

        if stats:
            response += "\nПо каналам (неотправленные за 4 часа):\n"
            for title, count in stats:
                response += f"  - {title}: {count} постов\n"
        else:
             response += "\nНет неотправленных постов за последние 4 часа.\n"

        # Add next digest info
        response += f"\nСледующий автодайджест примерно через {DIGEST_INTERVAL_MINUTES} минут"

        await event.respond(response)
        
    except Exception as e:
        logger.error(f"Error in status_handler: {e}")
        await event.respond("Произошла ошибка при получении статуса.")

async def main():
    """Start the bot and user client"""
    # Initialize databases
    init_database() # users.db
    init_posts_database() # posts.db
    
    # Debug: Print all environment variables
    logger.info(f"Environment variables in main: {dict(os.environ)}")
    
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
    logger.info(f"Using DIGEST_INTERVAL_MINUTES: {DIGEST_INTERVAL_MINUTES} minutes")
    
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
        # Debug: Print all environment variables
        logger.info(f"Environment variables before main: {dict(os.environ)}")
        
        # Run the main function
        user_client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Error running bot: {e}")
    finally:
        user_client.loop.close()
        logger.info("Successfully shutdown the bot") 