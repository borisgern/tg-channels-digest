import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram API credentials
API_ID = int(os.getenv('TELEGRAM_API_ID'))  # Convert to int as API_ID must be numeric
API_HASH = os.getenv('TELEGRAM_API_HASH')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('TELEGRAM_CHANNEL_USERNAME')

# Interval in minutes for automatic digest sending
DIGEST_INTERVAL_MINUTES = 2  # Send digest every hour by default 

# OpenAI Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Choose which GPT model to use for summarization
# Options: "gpt-3.5-turbo" or "gpt-4"
GPT_MODEL = "gpt-3.5-turbo"

# Prompt template for post summarization
SUMMARY_PROMPT_TEMPLATE = """
Ты - ассистент, который создает краткие обзоры постов из Telegram-канала.
Напиши краткое описание (3-5 строк) основных тем и идей из предоставленных постов.
Используй простой язык, выдели главное. Пиши на русском языке.

Посты для обзора:
{posts}

Формат ответа:
🤖 AI-обзор:
[Твое краткое описание здесь]
"""

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