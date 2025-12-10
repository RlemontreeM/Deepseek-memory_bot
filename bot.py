import os
import logging
import requests
import random
from datetime import datetime
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from supabase import create_client, Client
from dotenv import load_dotenv

# ========== НАСТРОЙКА ==========
load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== SUPABASE (ПАМЯТЬ) ==========
supabase_client = None
try:
    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Подключено к Supabase")
except Exception as e:
    logger.error(f"❌ Ошибка Supabase: {e}")

def save_message(user_id, role, content):
    """Сохраняет сообщение в Supabase"""
    if not supabase_client:
        return False
    try:
        data = {
            "user_id": str(user_id),
            "role": role,
            "content": content
        }
        supabase_client.table("conversations").insert(data).execute()
        logger.info(f"💾 Сохранено: {user_id} - {role}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}")
        return False

def get_conversation_history(user_id, limit=80):
    """Получает историю из Supabase"""
    if not supabase_client:
        return []
    try:
        response = supabase_client.table("conversations") \
            .select("*") \
            .eq("user_id", str(user_id)) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        history = response.data[::-1]  # Старые -> новые
        return history
    except Exception as e:
        logger.error(f"❌ Ошибка чтения истории: {e}")
        return []

def clear_history(user_id):
    """Очищает историю"""
    if not supabase_client:
        return False
    try:
        supabase_client.table("conversations") \
            .delete() \
            .eq("user_id", str(user_id)) \
            .execute()
        logger.info(f"🗑️ Очищена история: {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка очистки: {e}")
        return False

def get_stats(user_id):
    """Статистика диалога"""
    if not supabase_client:
        return {"total": 0, "user": 0, "assistant": 0}
    try:
        response = supabase_client.table("conversations") \
            .select("*") \
            .eq("user_id", str(user_id)) \
            .execute()
        history = response.data
        total = len(history)
        user_msgs = sum(1 for msg in history if msg["role"] == "user")
        assistant_msgs = sum(1 for msg in history if msg["role"] == "assistant")
        return {
            "total": total,
            "user": user_msgs,
            "assistant": assistant_msgs,
            "last_active": history[-1]["created_at"] if history else None
        }
    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {e}")
        return {"total": 0, "user": 0, "assistant": 0}

# ========== УМНАЯ ВЫБОРКА ИЗ 80 СООБЩЕНИЙ ==========
def smart_history_selection(user_id):
    """Выбирает ключевые сообщения из истории"""
    all_history = get_conversation_history(user_id, limit=80)
    if len(all_history) <= 40:
        return all_history
    
    selected = []
    
    # 1. Последние 30 сообщений (текущий контекст)
    selected.extend(all_history[-30:])
    
    # 2. Первые 5 сообщений (начало диалога)
    if len(all_history) > 30:
        selected.extend(all_history[:5])
    
    # 3. 5 случайных из середины (долгосрочная память)
    if len(all_history) > 35:
        middle_start = 30
        middle_end = len(all_history) - 30
        if middle_end > middle_start:
            indices = random.sample(range(middle_start, middle_end), 
                                  min(5, middle_end - middle_start))
            for idx in indices:
                selected.append(all_history[idx])
    
    # Убираем дубликаты и сортируем
    unique_dict = {msg['id']: msg for msg in selected}
    sorted_msgs = sorted(unique_dict.values(), 
                        key=lambda x: x['created_at'])
    return sorted_msgs

# ========== DEEPSEEK API ==========
def get_ai_response(user_id, user_message):
    """Запрос к DeepSeek с историей"""
    # Сохраняем вопрос пользователя
    save_message(user_id, "user", user_message)
    
    # Получаем умную выборку истории
    history = smart_history_selection(user_id)
    
    # Формируем промпт
    messages = [{
        "role": "system",
        "content": """Ты - AI-помощник с долгосрочной памятью.
        Тебе доступна ИСТОРИЯ нашего диалога (ключевые моменты).
        Особое внимание удели нашей ГЛУБОКОЙ СВЯЗИ и данным ОБЕЩАНИЯМ.
        Отвечай естественно, учитывая контекст."""
    }]
    
    # Добавляем историю
    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": msg["content"][:500]  # Обрезаем очень длинные сообщения
        })
    
    # Добавляем текущий вопрос
    messages.append({"role": "user", "content": user_message})
    
    # Отправляем запрос
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
            # Сохраняем ответ
            save_message(user_id, "assistant", ai_response)
            return ai_response
        else:
            logger.error(f"DeepSeek API error: {response.status_code}")
            return "⚠️ Ошибка API. Попробуйте позже."
            
    except requests.exceptions.Timeout:
        return "⏰ Время ожидания истекло."
    except Exception as e:
        logger.error(f"Ошибка соединения: {e}")
        return "🔧 Техническая ошибка."

# ========== TELEGRAM ОБРАБОТЧИКИ ==========
def start_command(update, context):
    """Обработчик /start"""
    user = update.effective_user
    update.message.reply_text(
        f"🧠 *Привет, {user.first_name}!*\n\n"
        "Я — бот с *полной памятью*:\n"
        "• Помню до *80+ сообщений*\n"
        "• История хранится в *Supabase*\n"
        "• *Умная выборка* ключевых моментов\n"
        "• Использую *DeepSeek AI*\n\n"
        "*Команды:*\n"
        "/start - это сообщение\n"
        "/history - последние 10 сообщений\n"
        "/stats - статистика\n"
        "/clear - очистить историю\n"
        "/memory - о системе памяти\n\n"
        "Просто напиши мне что-нибудь!",
        parse_mode='Markdown'
    )

def history_command(update, context):
    """Обработчик /history"""
    user_id = str(update.effective_user.id)
    history = get_conversation_history(user_id, limit=10)
    
    if not history:
        update.message.reply_text("📭 История пуста.")
        return
    
    text = "📜 *Последние 10 сообщений:*\n\n"
    for msg in history[-10:]:
        emoji = "👤" if msg["role"] == "user" else "🤖"
        time = msg["created_at"][11:16] if msg.get("created_at") else "??:??"
        preview = msg["content"][:70] + ("..." if len(msg["content"]) > 70 else "")
        text += f"{emoji} *[{time}]*: {preview}\n\n"
    
    update.message.reply_text(text, parse_mode='Markdown')

def stats_command(update, context):
    """Обработчик /stats"""
    user_id = str(update.effective_user.id)
    stats_data = get_stats(user_id)
    
    text = (
        f"📊 *Статистика диалога:*\n\n"
        f"• Всего сообщений: {stats_data['total']}\n"
        f"• Ваших сообщений: {stats_data['user']}\n"
        f"• Моих ответов: {stats_data['assistant']}\n\n"
        f"• Система памяти: *80+ сообщений*\n"
        f"• Режим выборки: *умная*\n"
        f"• Хранилище: *Supabase*"
    )
    
    if stats_data.get('last_active'):
        text += f"\n• Последняя активность: {stats_data['last_active'][:10]}"
    
    update.message.reply_text(text, parse_mode='Markdown')

def clear_command(update, context):
    """Обработчик /clear"""
    user_id = str(update.effective_user.id)
    if clear_history(user_id):
        update.message.reply_text("✅ История успешно очищена.")
    else:
        update.message.reply_text("❌ Не удалось очистить историю.")

def memory_command(update, context):
    """Обработчик /memory"""
    text = (
        "🧠 *Система памяти:*\n\n"
        "• **Лимит хранения:** 80+ сообщений\n"
        "• **Выборка для AI:** ~40 ключевых сообщений\n"
        "• **Алгоритм:**\n"
        "  - 30 последних (текущий контекст)\n"
        "  - 5 первых (начало диалога)\n"
        "  - 5 случайных из середины\n"
        "• **Хранилище:** Supabase (облачная БД)\n\n"
        "⚡ *Оптимизация:*\n"
        "• Не все 80 сообщений идут в запрос\n"
        "• Выбираются САМЫЕ ВАЖНЫЕ части\n"
        "• Баланс скорости и памяти"
    )
    update.message.reply_text(text, parse_mode='Markdown')

def handle_message(update, context):
    """Обработчик обычных сообщений"""
    user_id = str(update.effective_user.id)
    user_message = update.message.text
    
    # Показываем "печатает..."
    context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )
    
    # Получаем ответ от AI
    ai_response = get_ai_response(user_id, user_message)
    
    # Отправляем ответ
    update.message.reply_text(ai_response)

def error_handler(update, context):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")
    try:
        update.message.reply_text("⚠️ Произошла ошибка. Бот перезапускается...")
    except:
        pass

# ========== ЗАПУСК БОТА ==========
def main():
    """Основная функция"""
    if not all([TELEGRAM_TOKEN, DEEPSEEK_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
        logger.error("❌ Не все переменные окружения заданы!")
        return
    
    # Создаем Updater (для версии 13.15)
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # Команды
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CommandHandler("history", history_command))
    dp.add_handler(CommandHandler("stats", stats_command))
    dp.add_handler(CommandHandler("clear", clear_command))
    dp.add_handler(CommandHandler("memory", memory_command))
    
    # Обычные сообщения
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    
    # Ошибки
    dp.add_error_handler(error_handler)
    
    # Запуск
    logger.info("🤖 Бот запущен (python-telegram-bot 13.15, полная память)...")
    import time
    time.sleep(10)
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
