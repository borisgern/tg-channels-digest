import logging
from telethon import TelegramClient, events, sync
from telethon.sessions import StringSession

# Import configuration
from config import API_ID, API_HASH, BOT_TOKEN, CHANNEL_USERNAME

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

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Handler for the /start command."""
    sender = await event.get_sender()
    logger.info(f"User {sender.id} started the bot.")
    await event.respond('Hello! I will now forward messages from the channel.')
    # Store the user ID to send messages later
    # In a real application, you might want a more robust way to manage users
    bot.sender_id = sender.id

@user_client.on(events.NewMessage(chats=CHANNEL_USERNAME))
async def channel_handler(event):
    """Handler for new messages in the specified channel."""
    # Check if the bot has been started and we know who to send the message to
    if hasattr(bot, 'sender_id') and bot.sender_id:
        try:
            # Forward the message to the user who started the bot
            await bot.send_message(bot.sender_id, event.message)
            logger.info(f"Forwarded message {event.message.id} to user {bot.sender_id}")
        except Exception as e:
            logger.error(f"Could not forward message: {e}")
    else:
        logger.warning(f"Received message {event.message.id} but no user started the bot yet.")

async def main():
    """Main function to start both clients."""
    logger.info("Starting user client...")
    await user_client.start()
    logger.info("User client started.")

    logger.info("Starting bot client...")
    # The bot client is already started implicitly by the decorator handler registration
    # but we run it until disconnected to keep the script alive.
    logger.info(f"Bot started. Listening for messages in @{CHANNEL_USERNAME}...")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    # Run the main function
    user_client.loop.run_until_complete(main()) 