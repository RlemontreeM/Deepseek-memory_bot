import os
import logging
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ЗАГЛУШКИ ДЛЯ ПАМЯТИ (вместо Supabase) ==========
def save_message(user_id, role, content):
    logger.info(f"[ЗАГЛУШКА] Сохранено: {user_id} - {role}")
    return True

def get_ai_response(user_message):
    """Упрощённый запрос к DeepSeek без истории"""
    import requests
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    data = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": user_message}],
        "max_tokens": 500
    }
    try:
        response = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return f"Ошибка API: {response.status_code}"
    except Exception as e:
        return f"Ошибка: {str(e)}"

# ========== ОБРАБОТЧИКИ TELEGRAM ==========
def start(update, context):
    update.message.reply_text(
        '🤖 *Бот запущен!*\n'
        'Версия: python-telegram-bot 13.15\n'
        'Режим: Упрощённый тест (память в заглушках)\n'
        'Напиши что-нибудь, и я отвечу через DeepSeek.',
        parse_mode='Markdown'
    )

def handle_message(update, context):
    user_message = update.message.text
    # Сохраняем в заглушку
    save_message(update.effective_user.id, "user", user_message)
    # Получаем ответ от AI
    ai_response = get_ai_response(user_message)
    # Сохраняем ответ в заглушку
    save_message(update.effective_user.id, "assistant", ai_response)
    # Отправляем пользователю
    update.message.reply_text(ai_response)

def main():
    if not TOKEN:
        logger.error("❌ Не найден TELEGRAM_TOKEN")
        return
    if not DEEPSEEK_API_KEY:
        logger.error("❌ Не найден DEEPSEEK_API_KEY")
        return

    # ЯДРО ИЗМЕНЕНИЙ: Используем Updater (для версии 13.15)
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    logger.info("✅ Бот запущен (python-telegram-bot 13.15, Python 3.11)...")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
