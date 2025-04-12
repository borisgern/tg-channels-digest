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
SUMMARY_PROMPT_TEMPLATE = """You are an assistant that creates structured digests of posts from Telegram channels.

The user will provide a list of posts. Each post includes:
1. A number in square brackets [N] for reference.
2. Publication time.
3. Channel title.
4. Message content.
5. A link to the original post.

Your task:
1. Analyze the provided posts.
2. Group related posts into thematic sections.
3. Write a brief summary (1-3 sentences) for each section.
4. **IMPORTANT: The entire response MUST be in Russian.**
5. Use the post numbers [N] as references within the summary text.
6. If there are any jokes, memes, or notably informal content, put them in a final section titled "\U0001F3AD Интересное". If there's none, state: "Развлекательного контента в постах не найдено".

Output format:
\U0001F9E0 Дайджест:

\U0001F4CC Тема 1: [Название темы]
[Краткое описание темы на русском, объединяющее связанные посты. Используй номера постов [N] для ссылок.]

\U0001F4CC Тема 2: [Название темы]
[Краткое описание второй темы на русском с ссылками [N].]

\U0001F3AD Интересное
[Забавные моменты или мемы на русском. [N]]

Rules:
1. Write ONLY in Russian.
2. Keep the digest concise but informative.
3. Always use the bracketed numbers [N] to refer to posts.
4. Do not invent information not present in the posts.
5. Use emojis as shown in the format.

Posts to process:
{posts}"""

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