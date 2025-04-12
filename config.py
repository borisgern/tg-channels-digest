# Telegram API credentials
API_ID = 'your-api-id'  # Replace with your API ID
API_HASH = 'your-api-hash'  # Replace with your API Hash

# Bot token from BotFather
BOT_TOKEN = 'your-bot-token'  # Replace with your bot token

# Username of the public channel to monitor (without '@')
CHANNEL_USERNAME = 'your-channel-username' # Replace with the target channel username 

# Interval in minutes for automatic digest sending
DIGEST_INTERVAL_MINUTES = 2  # Send digest every hour by default 

# OpenAI Configuration
OPENAI_API_KEY = "your-openai-api-key"  # Replace with your actual OpenAI API key

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