import os
import json
import logging
import uuid
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
import openai
from config import TELEGRAM_BOT_TOKEN, OPENAI_API_KEY


# ===== КОНСТАНТЫ =====
CHATS_DIR = Path("Chats")
DEFAULT_MESSAGES_COUNT = 750

# Состояния диалога
UPLOAD, GET_NAME, CHAT_MODE = range(3)

# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY

# Настройка логгирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def ensure_dirs():
    """Создает необходимые директории"""
    CHATS_DIR.mkdir(exist_ok=True)

def clean_data(data: list, interlocutor_name: str, max_messages: int = DEFAULT_MESSAGES_COUNT) -> list:
    """
    Очищает данные переписки:
    1. Оставляет только сообщения указанного собеседника
    2. Извлекает текстовый контент из сообщений
    3. Берет последние max_messages сообщений
    """
    cleaned_messages = []
    
    for msg in reversed(data):
        if len(cleaned_messages) >= max_messages:
            break
            
        if not isinstance(msg, dict):
            continue
            
        # Проверяем отправителя
        if msg.get("from") != interlocutor_name:
            continue
            
        # Извлекаем текст сообщения
        text = ""
        if "text" in msg:
            if isinstance(msg["text"], str):
                text = msg["text"]
            elif isinstance(msg["text"], list):
                text = "".join(
                    entity["text"] 
                    for entity in msg["text"] 
                    if isinstance(entity, dict) and "text" in entity
                )
        
        if not text.strip():
            continue
            
        cleaned_messages.append({
            "from": msg["from"],
            "text": text,
            "date": msg.get("date", "")
        })
    
    return list(reversed(cleaned_messages))

def save_conversation(user_id: int, data: list):
    """Сохраняет переписку в JSON файл"""
    filename = CHATS_DIR / f"{user_id}_{uuid.uuid4().hex}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filename

def generate_prompt(interlocutor_name: str, cleaned_data: list) -> str:
    """Генерирует промпт для обучения на основе очищенных данных"""
    messages = "\n".join(
        f"{msg['from']} ({msg['date']}): {msg['text']}" 
        for msg in cleaned_data
    )
    return (
        f"Ты имитируешь стиль общения человека по имени {interlocutor_name}.\n"
        "Вот примеры его сообщений:\n\n"
        f"{messages}\n\n"
        "Отвечай так, как бы ответил этот человек, сохраняя его стиль, "
        "манеру речи и особенности общения. Не упоминай, что ты ИИ. "
        "Длина ответа должна быть естественной для диалога."
        "Отвечай ТОЛЬКО текстом сообщения, без указания имени, даты или других метаданных."
    )

async def get_ai_response(prompt: str, user_input: str) -> str:
    """Получает ответ от OpenAI с имитацией стиля"""
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_input}
            ],
            temperature=0.7,
            max_tokens=500
        )
        return response.choices[0].message['content'].strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "⚠️ Ошибка генерации ответа. Попробуйте позже."

# ===== ОБРАБОТЧИКИ КОМАНД =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    instructions = (
        "👋 Привет! Я бот для имитации стиля общения.\n\n"
        "📝 Как подготовить данные:\n"
        "1. Открой Telegram Desktop\n"
        "2. Выбери нужный диалог\n"
        "3. Нажми ⋮ → Export chat history\n"
        "4. Формат: JSON\n"
        "5. Сними галочки: 'Photos', 'Videos', 'Voice messages'\n"
        "6. Выбери диапазон: 'Last year' или другой\n"
        "7. Экспортируй и отправь мне файл\n\n"
        "После обработки я буду общаться стилем этого человека!"
    )
    
    await update.message.reply_text(instructions)
    return UPLOAD

async def handle_json(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка полученного JSON-файла"""
    user = update.message.from_user
    user_id = user.id
    
    try:
        # Скачивание файла
        file = await context.bot.get_file(update.message.document.file_id)
        json_path = CHATS_DIR / f"temp_{user_id}.json"
        await file.download_to_drive(json_path)
        
        # Загрузка данных
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Проверяем структуру JSON
        if "messages" not in data or not isinstance(data["messages"], list):
            await update.message.reply_text("❌ Некорректный формат JSON: отсутствует список messages")
            return ConversationHandler.END
        
        # Сохраняем данные для обработки
        context.user_data['json_data'] = data
        await update.message.reply_text(
            "✅ Файл получен! Теперь введи имя человека, "
            "стиль которого нужно имитировать (как оно указано в переписке):"
        )
        return GET_NAME
        
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Ошибка чтения JSON. Проверьте целостность файла.")
    except Exception as e:
        logger.error(f"JSON processing error: {e}")
        await update.message.reply_text("❌ Ошибка обработки файла. Проверьте формат.")
    finally:
        if 'json_path' in locals() and json_path.exists():
            os.remove(json_path)
    
    return ConversationHandler.END

async def handle_interlocutor_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка имени собеседника"""
    interlocutor_name = update.message.text
    user_data = context.user_data
    
    if 'json_data' not in user_data:
        await update.message.reply_text("❌ Данные не найдены. Начните заново с /start")
        return ConversationHandler.END
    
    try:
        # Обработка данных
        cleaned_data = clean_data(
            user_data['json_data']["messages"], 
            interlocutor_name
        )
        
        if not cleaned_data:
            await update.message.reply_text(
                f"❌ Не найдено сообщений от '{interlocutor_name}'. "
                "Проверьте имя и повторите попытку."
            )
            return GET_NAME
        
        # Сохранение и подготовка промпта
        save_path = save_conversation(update.message.from_user.id, cleaned_data)
        prompt = generate_prompt(interlocutor_name, cleaned_data)
        
        # Сохраняем промпт в контексте пользователя
        user_data['prompt'] = prompt
        user_data['history'] = []
        
        await update.message.reply_text(
            f"✅ Анализ переписки завершен!\n"
            f"Найдено {len(cleaned_data)} сообщений от {interlocutor_name}\n\n"
            "Теперь я готов к общению в его стиле!\n\n"
            "Просто напиши мне сообщение, и я отвечу как твой собеседник.\n"
            "Используй /exit для выхода из режима."
        )
        return CHAT_MODE
        
    except Exception as e:
        logger.error(f"Data processing error: {e}")
        await update.message.reply_text("❌ Ошибка обработки данных. Попробуйте другой файл.")
        return ConversationHandler.END

async def chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Режим общения с имитацией стиля"""
    user_input = update.message.text
    user_data = context.user_data
    
    # Добавляем сообщение в историю
    user_data['history'].append({"role": "user", "content": user_input})
    
    # Генерируем ответ
    response = await get_ai_response(
        prompt=user_data['prompt'],
        user_input=user_input
    )
    
    # Сохраняем ответ и отправляем
    user_data['history'].append({"role": "assistant", "content": response})
    await update.message.reply_text(response)
    return CHAT_MODE

async def exit_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выход из режима общения"""
    await update.message.reply_text(
        "Выход из режима имитации.\n"
        "Чтобы начать заново, отправь новый JSON-файл или используй /start"
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена текущей операции"""
    await update.message.reply_text("Операция отменена")
    return ConversationHandler.END

# ===== ОСНОВНАЯ ФУНКЦИЯ =====
def main():
    ensure_dirs()
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            UPLOAD: [
                MessageHandler(filters.Document.FileExtension("json"), handle_json),
                CommandHandler('cancel', cancel)
            ],
            GET_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_interlocutor_name),
                CommandHandler('cancel', cancel)
            ],
            CHAT_MODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, chat_mode),
                CommandHandler('exit', exit_chat)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    app.add_handler(conv_handler)
    
    logger.info("Бот запущен")
    app.run_polling()

if __name__ == '__main__':
    main()