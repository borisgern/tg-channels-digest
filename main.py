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
import re
import pytz
import telethon.errors
from functools import partial

# Import configuration
from config import (
    API_ID, API_HASH, BOT_TOKEN, CHANNELS,
    OPENAI_API_KEY, GPT_MODEL, SUMMARY_PROMPT_TEMPLATE,
    DIGEST_TIME,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    
    # Create posts table with channel information and post link
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            channel_title TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            content TEXT NOT NULL,
            post_link TEXT,
            sent BOOLEAN DEFAULT FALSE
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully (posts table updated with post_link)")

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

async def save_post(channel_id: str, channel_title: str, timestamp: str, content: str, post_link: str):
    """Save a post to the database, including its link."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO posts (channel_id, channel_title, timestamp, content, post_link, sent) VALUES (?, ?, ?, ?, ?, FALSE)',
        (channel_id, channel_title, timestamp, content, post_link)
    )
    conn.commit()
    conn.close()
    logger.info(f"Saved post from {channel_title} with link: {post_link}")

def get_unsent_posts():
    """Get all unsent posts from the database, including their links."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, channel_title, timestamp, content, post_link
            FROM posts
            WHERE sent = FALSE
            ORDER BY timestamp ASC
        ''')
        posts = cursor.fetchall()
        conn.close()
        
        logger.info(f"Found {len(posts)} unsent posts (with links)")
        
        # Log the first few posts for debugging
        if posts:
            for i, post in enumerate(posts[:3]):
                if len(post) >= 5: # Check length before accessing index
                    logger.debug(f"Post {i+1}: ID={post[0]}, Channel={post[1]}, Time={post[2]}, Link={post[4]}") # Логируем ссылку
                else:
                    logger.debug(f"Post {i+1} has unexpected format: {post}")
        
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
    """Get posts from the last N hours for manual digest, including links."""
    try:
        # Calculate timestamp for N hours ago
        now = datetime.now()
        hours_ago = now - timedelta(hours=hours)
        timestamp_threshold = hours_ago.isoformat()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, channel_title, timestamp, content, post_link
            FROM posts
            WHERE timestamp > ?
            ORDER BY timestamp ASC
        ''', (timestamp_threshold,))
        
        posts = cursor.fetchall()
        conn.close()
        
        # Validate and clean posts data
        valid_posts = []
        for post in posts:
            try:
                # Ensure we have exactly 5 values
                if len(post) != 5:
                    logger.warning(f"Skipping post with invalid format (expected 5): {post}")
                    continue
                    
                post_id, channel_title, timestamp, content, post_link = post
                
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
                
        logger.info(f"Found {len(valid_posts)} posts for manual digest (hours={hours})")
        return valid_posts
        
    except Exception as e:
        logger.error(f"Error getting recent posts for manual digest: {e}")
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

async def format_digest(posts):
    """Format posts into a readable digest, including post links."""
    if not posts:
        return "Нет постов для включения в дайджест."

    # Group posts by channel
    channels = {}
    for post in posts:
        if len(post) != 5:
            logger.warning(f"Skipping post in format_digest due to invalid format (expected 5): {post}")
            continue
        post_id, channel_title, timestamp, content, post_link = post
        if channel_title not in channels:
            channels[channel_title] = []
        channels[channel_title].append((timestamp, content, post_link))

    # Format digest
    digest = "🧠 Дайджест:\n\n"

    # Group posts by topic (you can implement more sophisticated grouping later)
    topics = {}
    topic_counter = 1

    for channel_title, channel_posts in channels.items():
        topic_name = f"Новые посты из {channel_title}"
        topics[topic_name] = channel_posts

    for topic_name, topic_posts in topics.items():
        # Add topic header
        digest += f"📌 Тема {topic_counter}: {topic_name}\n"
        
        # Add posts under this topic
        for timestamp, content, post_link in topic_posts:
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime("%H:%M")
            except ValueError:
                time_str = "[invalid time]"

            # Format post content with clickable link
            preview = content[:200] + "..." if len(content) > 200 else content
            if post_link:
                digest += f"• [{time_str}]({post_link}): {preview}\n"
            else:
                digest += f"• {time_str}: {preview}\n"
        
        digest += "\n"
        topic_counter += 1

    # Add entertainment section
    digest += "🎭 Интересное\n"
    digest += "Развлекательного контента в постах не найдено.\n"

    return digest

def has_entertainment_content(posts):
    """Check if there's any entertainment content in the posts."""
    # Add your logic to detect entertainment content
    return False

def format_entertainment_content(posts):
    """Format the entertainment content section."""
    # Add your logic to format entertainment content
    return "Развлекательного контента в постах не найдено."

async def summarize_posts(posts):
    """Generate a summary of posts using OpenAI, returning summary text and a link map."""
    if not posts:
        logger.info("[summarize_posts] No posts received, returning None, None.")
        return None, None # Return None for both summary and link map
        
    try:
        # Format posts for the prompt and create link map
        formatted_posts = []
        link_map = {} # Dictionary to store {index: link}
        for i, post in enumerate(posts):
            try:
                if len(post) != 5:
                    logger.warning(f"Skipping post in summarize_posts due to invalid format (expected 5): {post}")
                    continue

                post_id, channel_title, timestamp, content, post_link = post
                
                # Validate timestamp
                try:
                    time_str = datetime.fromisoformat(timestamp).strftime('%H:%M')
                except ValueError:
                    logger.warning(f"Skipping post with invalid timestamp: {timestamp}")
                    continue

                # Add post number, time, channel, content, and link to the formatted list
                formatted_posts.append(f"[{i+1}] [{time_str}] [{channel_title}] {content}\n   Link: {post_link}")
                link_map[i+1] = post_link # Store the link with its number
            except Exception as e:
                logger.error(f"Error formatting post for summary: {e}")
                continue
                
        if not formatted_posts:
            logger.warning("[summarize_posts] No valid posts to format for prompt, returning None, None.")
            return None, None
            
        # Join all valid posts
        posts_text = "\n\n".join(formatted_posts)
        
        # Call OpenAI API
        logger.info(f"[summarize_posts] Calling OpenAI API with {len(formatted_posts)} formatted posts.")
        response = await openai_client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT_TEMPLATE},
                {"role": "user", "content": posts_text}
            ],
            temperature=0.7,
            max_tokens=3000
        )
        
        summary = response.choices[0].message.content.strip()
        
        # --- LOGGING BEFORE RETURN ---
        logger.info(f"[summarize_posts] OpenAI response received. Summary length: {len(summary) if summary else 0}")
        logger.info(f"[summarize_posts] Summary snippet: {summary[:200] if summary else 'N/A'} ...")
        logger.info(f"[summarize_posts] Link map generated: {link_map}")
        # --- END LOGGING ---
        
        if summary.endswith('...') or summary.endswith('…'):
            logger.warning("Summary appears to be truncated. Consider increasing max_tokens.")
        
        return summary, link_map # Return summary text and link map
        
    except Exception as e:
        logger.error(f"[summarize_posts] Error during generation: {e}")
        return None, None # Return None for both on error

async def send_digest(manual=False, target_user_id=None):
    """Generate, format with links, and send digest.
    
    Args:
        manual (bool): If True, get recent posts instead of unsent.
        target_user_id (int, optional): If provided and manual=True, send only to this user.
    """
    try:
        posts = get_recent_posts_for_manual_digest() if manual else get_unsent_posts()
        if not posts:
            if not manual:
                logger.info("No new unsent posts for automatic digest.")
                return None
            return "Нет постов для дайджеста за последние 4 часа."

        summary, link_map = await summarize_posts(posts)
        if not summary:
            logger.error("[send_digest] Failed to generate summary.")
            return "Произошла ошибка при генерации дайджеста."

        # Create the message with Markdown links
        final_summary = summary
        if link_map:
            logger.info(f"[send_digest] Starting link replacement using string.replace(). Link map size: {len(link_map)}")
            replacements_made = 0
            for num in sorted(link_map.keys(), reverse=True):
                link = link_map[num]
                placeholder = f"[{num}]"
                markdown_link = f"[{num}]({link})"
                summary_before_replace = final_summary
                final_summary = final_summary.replace(placeholder, markdown_link)
                if summary_before_replace != final_summary:
                    replacements_made += 1
                    logger.debug(f"  Replaced '{placeholder}' -> '{markdown_link}'")
            logger.info(f"[send_digest] Finished link replacement. Replacements made: {replacements_made}")
        else:
            logger.debug("[send_digest] Link map is empty or None. Skipping replacement.")

        # Determine recipients
        recipient_ids = []
        if manual and target_user_id:
            recipient_ids = [target_user_id]
            logger.info(f"[send_digest] Manual digest requested. Sending only to user {target_user_id}")
        elif not manual:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM users')
            recipient_ids = [row[0] for row in cursor.fetchall()]
            conn.close()
            logger.info(f"[send_digest] Automatic digest. Sending to {len(recipient_ids)} registered users.")
        else: # Manual digest without target_user_id (should not happen from /digest command)
             logger.warning("[send_digest] Manual digest called without target_user_id. Sending to all users.")
             conn = sqlite3.connect(DB_PATH)
             cursor = conn.cursor()
             cursor.execute('SELECT user_id FROM users')
             recipient_ids = [row[0] for row in cursor.fetchall()]
             conn.close()

        # Send to recipients
        sent_to_count = 0
        if not recipient_ids:
            logger.warning("No recipients found for digest.")
        else:
            for user_id in recipient_ids:
                try:
                    await bot.send_message(user_id, final_summary, parse_mode='markdown', link_preview=False)
                    sent_to_count += 1
                except Exception as e:
                    logger.error(f"Failed to send digest to user {user_id}: {e}")
        logger.info(f"Sent {'manual' if manual else 'automatic'} digest to {sent_to_count} users.")
        
        # Mark posts as sent ONLY for automatic digest if sent successfully
        if not manual and sent_to_count > 0:
            post_ids = [post[0] for post in posts if len(post) > 0 and isinstance(post[0], int)]
            if post_ids:
                mark_posts_as_sent(post_ids)
                logger.info(f"Marked {len(post_ids)} posts as sent after automatic digest")
            else:
                logger.warning("No post IDs found to mark as sent for automatic digest")
        elif not manual and sent_to_count == 0:
             logger.warning("Automatic digest was not sent to any users, posts will NOT be marked as sent.")
        
        # Return the generated summary only if it was a manual request 
        # (even if sending failed, the text was still generated)
        if manual:
            return final_summary
        else: # Automatic digest doesn't need to return the text
            return None

    except Exception as e:
        logger.error(f"Error in send_digest: {e}", exc_info=True)
        # Return an error message for manual requests, None for automatic
        return "Произошла ошибка при отправке дайджеста." if manual else None

# --- Handler Definitions (NO DECORATORS) --- 

async def start_handler(event):
    """Handle the /start command and register new users"""
    sender = None
    user_id = None
    username = None
    try:
        sender = await event.get_sender()
        user_id = sender.id
        username = sender.username
        logger.info(f"Received /start command from user_id={user_id}, username={username}")
        logger.info(f"Attempting to register user {user_id}...")
        is_new_user = register_user(user_id, username)
        logger.info(f"register_user returned: {is_new_user} for user_id={user_id}")
        welcome_msg = '👋 Привет! Ты зарегистрирован. ' if is_new_user else '👋 Привет! Ты уже был зарегистрирован. '
        welcome_msg += '''Я буду сохранять сообщения и отправлять их дайджестом.\n\nДоступные команды:\n/digest - получить дайджест за последние 4 часа\n/status - узнать количество постов для следующего дайджеста'''
        await event.respond(welcome_msg)
        logger.info(f"Sent welcome message to user_id={user_id}")
    except Exception as e:
        logger.error(f"Error in start_handler for user_id={user_id}: {e}", exc_info=True)
        try:
            await event.respond("Произошла ошибка при обработке команды /start.")
        except Exception as resp_err:
             logger.error(f"Failed to send error message to user_id={user_id}: {resp_err}")

async def digest_handler(event):
    """Handle /digest command - trigger manual digest generation and send result ONLY to the requester."""
    sender_id = None
    status_message = None
    try:
        sender_id = event.sender_id
        logger.info(f"Processing /digest command from user {sender_id}")
        status_message = await event.respond("⏳ Генерирую дайджест за последние 4 часа...")
        result_message = await send_digest(manual=True, target_user_id=sender_id)
        if result_message and not (result_message.startswith("Нет постов") or result_message.startswith("Произошла ошибка")):
            logger.info(f"Digest sent successfully to user {sender_id} by send_digest.")
            try:
                await status_message.delete()
                logger.info(f"Deleted status message for user {sender_id}.")
            except Exception as del_err:
                logger.error(f"Could not delete status message for user {sender_id}: {del_err}")
        elif result_message:
            logger.info(f"Handling status/error message for user {sender_id}: {result_message}")
            try:
                await status_message.edit(result_message)
                logger.info(f"Edited status message for user {sender_id}.")
            except telethon.errors.rpcerrorlist.MessageIdInvalidError:
                logger.warning(f"Failed to edit status message for user {sender_id} (MessageIdInvalidError). Sending new message.")
                try:
                    await status_message.delete()
                except Exception as del_err:
                     logger.error(f"Could not delete status message after edit failed for user {sender_id}: {del_err}")
                await event.respond(result_message)
            except Exception as edit_err:
                logger.error(f"Failed to edit status message for user {sender_id}: {edit_err}. Sending new message.")
                try:
                    await status_message.delete()
                except Exception as del_err:
                     logger.error(f"Could not delete status message after edit failed for user {sender_id}: {del_err}")
                await event.respond(result_message)
        else:
            logger.error(f"send_digest(manual=True) returned None unexpectedly for user {sender_id}")
            error_text = "Произошла внутренняя ошибка при обработке дайджеста."
            try:
                await status_message.edit(error_text)
            except Exception:
                 logger.warning(f"Failed to edit status message for unexpected None result for user {sender_id}. Sending new message.")
                 try:
                    await status_message.delete()
                 except Exception: pass
                 await event.respond(error_text)
    except Exception as e:
        logger.error(f"Error in digest_handler for user {sender_id}: {e}", exc_info=True)
        try:
            error_text = "Произошла ошибка при обработке команды /digest."
            if status_message:
                try:
                    await status_message.edit(error_text)
                except Exception:
                    await event.respond(error_text)
            else:
                 await event.respond(error_text)
        except Exception as resp_err:
             logger.error(f"Failed to send final error message to user {sender_id}: {resp_err}")

async def status_handler(event):
    """Handle /status command - show statistics about unsent posts by channel."""
    try:
        now = datetime.now()
        hours_ago = now - timedelta(hours=4)
        timestamp_threshold = hours_ago.isoformat()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''SELECT channel_title, COUNT(*) as post_count FROM posts WHERE timestamp > ? AND sent = FALSE GROUP BY channel_title''', (timestamp_threshold,))
        stats = cursor.fetchall()
        cursor.execute('''SELECT timestamp FROM posts WHERE sent = FALSE ORDER BY timestamp ASC LIMIT 1''')
        earliest_post = cursor.fetchone()
        cursor.execute('SELECT COUNT(*) FROM posts WHERE sent = FALSE')
        total_unsent_count = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM users')
        registered_user_count = cursor.fetchone()[0]
        conn.close()
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
        try:
            next_run_dt_tz = await get_next_run_time()
            response += f"\nСледующий автодайджест: {next_run_dt_tz.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"
        except Exception as e:
            logger.error(f"Error getting next run time for status: {e}")
            response += "\nНе удалось определить время следующего автодайджеста."
        await event.respond(response)
    except Exception as e:
        logger.error(f"Error in status_handler: {e}")
        await event.respond("Произошла ошибка при получении статуса.")

async def channel_handler(event, bot):
    """Handle new messages from monitored channels."""
    try:
        channel = await event.get_chat()
        channel_id = str(channel.id)
        channel_title = channel.title
        channel_username = channel.username
        if not any(channel_id in ch or (channel_username and channel_username in ch) for ch in CHANNELS):
            logger.debug(f"Ignoring message from non-monitored channel: {channel_title} ({channel_username or channel_id})")
            return
        if event.message.text:
            content = event.message.text
        elif event.message.media:
            content = "[Media message]"
            if hasattr(event.message.media, 'caption') and event.message.media.caption:
                content += f": {event.message.media.caption}"
        else:
            logger.debug(f"Skipping message without content from {channel_title}")
            return
        message_id = event.message.id
        post_link = f"https://t.me/{channel_username}/{message_id}" if channel_username else f"https://t.me/c/{channel_id}/{message_id}"
        timestamp = event.message.date.isoformat()
        await save_post(channel_id, channel_title, timestamp, content, post_link)
        time_str = event.message.date.strftime("%H:%M")
        notification = f"📥 Новый пост из [{channel_title}]({post_link})\n⏰ Время: {time_str}\n📝 Текст: {content[:100]}{'...' if len(content) > 100 else ''}"
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users')
        users = cursor.fetchall()
        conn.close()
        for user_id_tuple in users:
            try:
                await bot.send_message(user_id_tuple[0], notification, parse_mode='markdown') 
            except Exception as e:
                logger.error(f"Failed to notify user {user_id_tuple[0]}: {e}")
    except Exception as e:
        logger.error(f"Error in channel_handler: {e}")

async def get_next_run_time():
    """Calculate the next run time based on DIGEST_TIME (Europe/Lisbon)."""
    try:
        tz = pytz.timezone('Europe/Lisbon')
        now_tz = datetime.now(tz)
        
        # Parse configured time
        hour, minute = map(int, DIGEST_TIME.split(':'))
        target_time = datetime.min.replace(hour=hour, minute=minute)
        
        # Calculate next run time
        next_run_dt_tz = tz.localize(datetime.combine(now_tz.date(), target_time.time()))
        
        # If the target time has already passed today, schedule for tomorrow
        if now_tz >= next_run_dt_tz:
            next_run_dt_tz += timedelta(days=1)
        
        logger.info(f"Current time (Europe/Lisbon): {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
        logger.info(f"Next digest run time (Europe/Lisbon): {next_run_dt_tz.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
        
        return next_run_dt_tz
    except Exception as e:
        logger.error(f"Error calculating next run time: {e}")
        raise

async def automatic_digest_task():
    """Background task that sends digest daily at 00:00 Europe/Lisbon."""
    logger.info("Starting automatic digest task (scheduled for 00:00 Europe/Lisbon)")
    
    while True:
        try:
            # Calculate the next run time
            next_run_time_tz = await get_next_run_time()
            now_utc = datetime.now(pytz.utc) # Use timezone-aware datetime
            
            # Convert next run time to UTC for comparison and sleep calculation
            next_run_time_utc = next_run_time_tz.astimezone(pytz.utc)
            
            wait_seconds = (next_run_time_utc - now_utc).total_seconds()
            
            if wait_seconds > 0:
                logger.info(f"Waiting for {wait_seconds:.2f} seconds until next scheduled digest ({next_run_time_tz.strftime('%Y-%m-%d %H:%M:%S %Z%z')})...")
                await asyncio.sleep(wait_seconds)
            else:
                # If calculated time is in the past (e.g., due to startup delay), run immediately and schedule for next day
                logger.warning(f"Calculated next run time {next_run_time_tz.strftime('%Y-%m-%d %H:%M:%S %Z%z')} is in the past. Running now and rescheduling.")
                # Optional: add a small delay to prevent rapid looping if there's an issue
                await asyncio.sleep(5)
                
            logger.info("Running automatic digest job...")
            
            # Call send_digest (handles getting posts, summarizing, sending, marking as sent)
            await send_digest(manual=False)

        except asyncio.CancelledError:
            logger.info("Automatic digest task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in automatic digest task: {e}", exc_info=True)
            # Wait a bit before retrying in case of error to avoid tight loop
            logger.info("Waiting 60 seconds before retrying digest task after error.")
            await asyncio.sleep(60)

async def main():
    """Start the bot and user client"""
    # Initialize databases
    init_database() # users.db
    init_posts_database() # posts.db
    
    # Debug: Print all environment variables
    logger.info(f"Environment variables in main: {dict(os.environ)}")
    
    # --- INITIALIZE CLIENTS INSIDE MAIN --- 
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    user_client = TelegramClient('user_session', API_ID, API_HASH)
    
    # --- Start clients --- 
    try:
        logger.info("Starting bot client...")
        await bot.start(bot_token=BOT_TOKEN)
        logger.info("Bot client started.")
        
        logger.info("Starting user client...")
        await user_client.start()
        logger.info("User client started.")
        
    except Exception as e:
        logger.error(f"Error starting Telegram clients: {e}", exc_info=True)
        return # Exit if clients fail to start

    logger.info("All clients started successfully")

    # --- REGISTER HANDLERS MANUALLY --- 
    bot.add_event_handler(start_handler, events.NewMessage(pattern='/start'))
    bot.add_event_handler(digest_handler, events.NewMessage(pattern='/digest'))
    bot.add_event_handler(status_handler, events.NewMessage(pattern='/status'))
    user_client.add_event_handler(
        partial(channel_handler, bot=bot), 
        events.NewMessage(chats=CHANNELS)
    )
    logger.info("Event handlers registered successfully.")
    
    # Start the automatic digest task
    auto_digest_task = asyncio.create_task(automatic_digest_task())
    
    # --- Setup signal handlers (as before) ---
    loop = asyncio.get_running_loop()
    signals_to_handle = (signal.SIGTERM, signal.SIGINT)
    
    async def shutdown_wrapper(sig):
        logger.info(f"Received signal {sig.name}. Initiating shutdown...")
        # Cancel the digest task first
        if not auto_digest_task.done():
            auto_digest_task.cancel()
            try:
                await auto_digest_task
            except asyncio.CancelledError:
                 logger.info("Automatic digest task cancelled successfully.")
            except Exception as e_cancel:
                 logger.error(f"Error during digest task cancellation: {e_cancel}")

        # Disconnect clients
        logger.info("Disconnecting clients...")
        if bot.is_connected():
            await bot.disconnect()
        if user_client.is_connected():
            await user_client.disconnect()
        logger.info("Clients disconnected.")
        
        # Cancel remaining tasks (should ideally be fewer now)
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if tasks:
            logger.info(f"Cancelling {len(tasks)} remaining outstanding tasks...")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Remaining tasks cancelled.")
        else:
             logger.info("No remaining tasks to cancel.")
            
    for s in signals_to_handle:
        try:
            loop.add_signal_handler(
                 s, lambda s=s: asyncio.create_task(shutdown_wrapper(s))
            )
            logger.info(f"Registered signal handler for {s.name}")
        except NotImplementedError:
             logger.warning(f"Signal handling for {s.name} not supported on this platform (e.g., Windows). Relying on KeyboardInterrupt.")

    try:
        logger.info("Running clients until disconnected...")
        await auto_digest_task
        
    except asyncio.CancelledError:
         logger.info("Main task or clients were cancelled. Shutting down...")
    finally:
         logger.info("Main execution block finished. Cleanup should have happened...")
         # Ensure clients are disconnected even if shutdown handler had issues
         if bot.is_connected():
             logger.warning("Bot still connected in finally block, attempting disconnect.")
             await bot.disconnect()
         if user_client.is_connected():
             logger.warning("User client still connected in finally block, attempting disconnect.")
             await user_client.disconnect()
         logger.info("Bot stopped gracefully")

if __name__ == '__main__':
    try:
        # Debug: Print all environment variables
        logger.info(f"Environment variables before main: {dict(os.environ)}")
        
        # Run the main coroutine using asyncio.run
        asyncio.run(main()) # This handles loop creation and closing

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down (handled by asyncio.run or signal handlers)...")
    except Exception as e:
        logger.error(f"Error running bot: {e}", exc_info=True) # Log traceback
    # finally:
        # No need for explicit loop.close() when using asyncio.run
        # logger.info("Successfully shutdown the bot") 