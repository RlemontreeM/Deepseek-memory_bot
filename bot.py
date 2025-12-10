import os
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests
from supabase import create_client, Client
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# ========== КОНФИГУРАЦИЯ ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== SUPABASE КЛИЕНТ ==========
supabase: Client = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Подключено к Supabase")
except Exception as e:
    logger.error(f"❌ Ошибка подключения к Supabase: {e}")

# ========== ФУНКЦИИ РАБОТЫ С БАЗОЙ ==========
def save_message(user_id: str, role: str, content: str):
    """Сохраняет сообщение в базу данных"""
    if not supabase:
        logger.error("Supabase не подключён")
        return False
    
    try:
        data = {
            "user_id": str(user_id),
            "role": role,
            "content": content
        }
        response = supabase.table("conversations").insert(data).execute()
        logger.info(f"💾 Сохранено сообщение для {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}")
        return False

def get_conversation_history(user_id: str, limit: int = 20):
    """Получает историю диалога из базы"""
    if not supabase:
        return []
    
    try:
        response = supabase.table("conversations") \
            .select("*") \
            .eq("user_id", str(user_id)) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        
        # Возвращаем в хронологическом порядке (от старых к новым)
        history = response.data[::-1]
        return history
    except Exception as e:
        logger.error(f"❌ Ошибка чтения истории: {e}")
        return []

def clear_history(user_id: str):
    """Очищает историю пользователя"""
    if not supabase:
        return False
    
    try:
        response = supabase.table("conversations") \
            .delete() \
            .eq("user_id", str(user_id)) \
            .execute()
        
        logger.info(f"🗑️ История очищена для {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка очистки: {e}")
        return False

def get_stats(user_id: str):
    """Получает статистику диалога"""
    if not supabase:
        return {"total": 0, "user": 0, "assistant": 0}
    
    try:
        response = supabase.table("conversations") \
            .select("*") \
            .eq("user_id", str(user_id)) \
            .execute()
        
        history = response.data
        total = len(history)
        user_messages = sum(1 for msg in history if msg["role"] == "user")
        assistant_messages = sum(1 for msg in history if msg["role"] == "assistant")
        
        return {
            "total": total,
            "user": user_messages,
            "assistant": assistant_messages,
            "last_active": history[-1]["created_at"] if history else None
        }
    except Exception as e:
        logger.error(f"❌ Ошибка получения статистики: {e}")
        return {"total": 0, "user": 0, "assistant": 0}

# ========== DEEPSEEK API ==========
def get_ai_response(user_id: str, user_message: str) -> str:
    """Получает ответ от DeepSeek с учётом истории"""
    
    # 1. Сохраняем сообщение пользователя
    save_message(user_id, "user", user_message)
    
    # 2. Получаем историю (последние 15 сообщений)
    history = get_conversation_history(user_id, limit=15)
    
    # 3. Формируем промпт для DeepSeek
    messages = []
    
    # Системное сообщение
    messages.append({
        "role": "system",
        "content": """Ты - AI-помощник с долгосрочной памятью. 
        Тебе доступна вся история нашего диалога. 
        Отвечай естественно, учитывая контекст прошлых сообщений.
        Будь внимателен к деталям и обещаниям, которые были даны ранее."""
    })
    
    # Добавляем историю
    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": msg["content"]
        })
    
    # Добавляем текущее сообщение
    messages.append({"role": "user", "content": user_message})
    
    # 4. Отправляем запрос к DeepSeek
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "deepseek-chat",
        "messages": messages,
        "stream": False,
        "max_tokens": 2000,
        "temperature": 0.7
    }
    
    try:
        response = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers=headers,
            json=data,
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            ai_response = result["choices"][0]["message"]["content"]
            
            # 5. Сохраняем ответ AI
            save_message(user_id, "assistant", ai_response)
            
            return ai_response
        else:
            logger.error(f"DeepSeek API error: {response.status_code}")
            return "⚠️ Произошла ошибка при обработке запроса. Попробуйте ещё раз."
            
    except requests.exceptions.Timeout:
        return "⏰ Время ожидания истекло. Попробуйте позже."
    except Exception as e:
        logger.error(f"Ошибка соединения: {e}")
        return "🔧 Техническая ошибка. Бот скоро будет восстановлен."

# ========== TELEGRAM HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome = """
    🧠 *DeepSeek Memory Bot*
    
    Я помню *всю* нашу историю разговоров!
    Каждое сообщение сохраняется в облаке.
    
    *Команды:*
    /start - это сообщение
    /history - показать историю
    /clear - очистить историю
    /stats - статистика диалога
    
    Просто напиши мне что-нибудь!
    """
    await update.message.reply_text(welcome, parse_mode='Markdown')

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает историю диалога"""
    user_id = str(update.effective_user.id)
    history_data = get_conversation_history(user_id, limit=8)
    
    if not history_data:
        await update.message.reply_text("📭 История диалога пуста.")
        return
    
    text = "📜 *Последние сообщения:*\n\n"
    for msg in history_data[-8:]:  # Показываем последние 8
        emoji = "👤" if msg["role"] == "user" else "🤖"
        time = msg["created_at"][11:16]  # Только время
        content = msg["content"][:80] + ("..." if len(msg["content"]) > 80 else "")
        
        text += f"{emoji} *[{time}]*: {content}\n\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очищает историю"""
    user_id = str(update.effective_user.id)
    if clear_history(user_id):
        await update.message.reply_text("✅ История успешно очищена.")
    else:
        await update.message.reply_text("❌ Не удалось очистить историю.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику"""
    user_id = str(update.effective_user.id)
    stats_data = get_stats(user_id)
    
    text = f"""
    📊 *Статистика диалога:*
    
    Всего сообщений: {stats_data['total']}
    Ваших сообщений: {stats_data['user']}
    Моих ответов: {stats_data['assistant']}
    
    Последняя активность: {stats_data.get('last_active', 'никогда')}
    """
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения"""
    user_id = str(update.effective_user.id)
    user_message = update.message.text
    
    # Показываем "печатает..."
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )
    
    # Получаем ответ от AI
    response = get_ai_response(user_id, user_message)
    
    # Отправляем ответ
    await update.message.reply_text(response)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")
    if update and update.message:
        await update.message.reply_text("⚠️ Произошла ошибка. Бот перезапускается...")

# ========== ЗАПУСК БОТА ==========
def main():
    """Запускает бота"""
    if not all([TELEGRAM_TOKEN, DEEPSEEK_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
        logger.error("❌ Не все переменные окружения заданы!")
        return
    
    # Создаём приложение
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("🤖 Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
