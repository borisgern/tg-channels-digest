import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file first, overriding existing ones
load_dotenv(override=True)

# Configure logging after loading environment variables
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Log loaded environment variables
logger.info("Environment variables loaded from .env file (override=True)")

# Telegram API credentials
API_ID = int(os.getenv('TELEGRAM_API_ID'))  # Convert to int as API_ID must be numeric
API_HASH = os.getenv('TELEGRAM_API_HASH')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Channel configuration
CHANNEL_USERNAME = os.getenv('TELEGRAM_CHANNEL_USERNAME')  # Keep for backwards compatibility
CHANNEL_USERNAMES = os.getenv('TELEGRAM_CHANNEL_USERNAMES', CHANNEL_USERNAME)
CHANNELS = [channel.strip() for channel in CHANNEL_USERNAMES.split(',') if channel.strip()]

# OpenAI Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Choose which GPT model to use for summarization
#GPT_MODEL = "gpt-3.5-turbo"
GPT_MODEL = "gpt-4o-mini"
# Prompt template for post summarization
SUMMARY_PROMPT_TEMPLATE = """
You are an AI assistant generating a smart digest of posts from various Telegram channels. The channels may cover different topic (e.g., AI, education, news, memes), and your task is to help the user quickly understand what's important.

Here's what you need to do:
1. Group the posts by topic (if the same topic is mentioned across multiple channels -- make that explicit).
2. For each topic, write a short summary (1--3 sentences) with the core insight or message.
3. Avoid repetition -- if multiple posts talk about the same event, just mention that it was discussed in several channels.
4. Add short references like [1], [2], etc., for each post -- these will link back to the original messages.
5. Write clearly and concisely in Russian, with an emphasis on usefulness.

Posts:
{posts}

Output format:
🧠 AI Digest:

**📌 Topic 1: [Title or key fact]**  
Short explanation of the insight. [1], [3]

**📌 Topic 2: ...**  
...

If there are memes, jokes, or light-hearted content, include them at the end under a separate section like:  
🎭 **Fun & Informal**

If there's nothing meaningful to say -- don't invent content. It's better to be brief and relevant than verbose and vague.

"""

# Interval in minutes for automatic digest sending
raw_interval = os.getenv('DIGEST_INTERVAL_MINUTES')
logger.info(f"Raw DIGEST_INTERVAL_MINUTES from env: {raw_interval}")
DIGEST_INTERVAL_MINUTES = int(os.getenv('DIGEST_INTERVAL_MINUTES', '60'))
logger.info(f"Digest interval set to {DIGEST_INTERVAL_MINUTES} minutes")

# Validate that all required environment variables are set
required_env_vars = [
    'TELEGRAM_API_ID',
    'TELEGRAM_API_HASH',
    'TELEGRAM_BOT_TOKEN',
    'TELEGRAM_CHANNEL_USERNAME',
    'OPENAI_API_KEY'
]

missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}") 