import os
import logging
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from mosreg_schedule_selenium import MosregSchedule

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
WAITING_FOR_DATE = 1

# Функция для получения расписания
async def get_schedule(date=None):
    """
    Асинхронная функция для получения расписания на указанную дату
    """
    # Используем ThreadPoolExecutor для запуска блокирующего кода в отдельном потоке
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        def get_schedule_blocking():
            try:
                scheduler = MosregSchedule(headless=True)  # Используем headless режим для бота
                lessons = scheduler.get_schedule(date)
                scheduler.close()
                return lessons
            except Exception as e:
                logger.error(f"Ошибка при получении расписания: {e}")
                return None
        
        # Запускаем блокирующий код в отдельном потоке
        return await asyncio.get_event_loop().run_in_executor(pool, get_schedule_blocking)

# Форматирование расписания для отправки в сообщении
def format_schedule(lessons, date_str):
    """
    Форматирование расписания для отправки в сообщении
    """
    if not lessons or len(lessons) == 0:
        return f"На {date_str} расписание не найдено или нет уроков."
    
    message = f"📅 *Расписание на {date_str}*\n\n"
    
    for i, lesson in enumerate(lessons, 1):
        message += f"*{i}. {lesson['subject']}*\n"
        
        if lesson['start_time'] != "Не указано" or lesson['end_time'] != "Не указано":
            time_str = f"{lesson['start_time']}"
            if lesson['end_time'] != "Не указано":
                time_str += f" - {lesson['end_time']}"
            message += f"⏰ {time_str}\n"
        
        if lesson['room'] != "Не указано":
            message += f"🚪 Кабинет: {lesson['room']}\n"
        
        if lesson['teacher'] != "Не указано":
            message += f"👩‍🏫 Учитель: {lesson['teacher']}\n"
        
        message += "\n"
    
    return message

# Обработчики команд бота
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /start
    """
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я бот для получения расписания из МЭШ.\n\n"
        "Используйте следующие команды:\n"
        "/today - расписание на сегодня\n"
        "/tomorrow - расписание на завтра\n"
        "/date - расписание на конкретную дату\n"
        "/help - помощь по использованию бота"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /help
    """
    await update.message.reply_text(
        "📚 *Помощь по использованию бота*\n\n"
        "*Доступные команды:*\n"
        "/today - расписание на сегодня\n"
        "/tomorrow - расписание на завтра\n"
        "/date - расписание на конкретную дату\n"
        "/help - показать это сообщение\n\n"
        "*Получение расписания на дату:*\n"
        "1. Отправьте команду /date\n"
        "2. Введите дату в формате ДД-ММ-ГГГГ (например, 15-03-2025)\n\n"
        "Бот работает с использованием куки из файла cookies.json, которые должны быть действительными.",
        parse_mode="Markdown"
    )

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /today
    """
    await update.message.reply_text("Получаю расписание на сегодня... ⏳")
    
    today = datetime.now().strftime("%d-%m-%Y")
    today_readable = datetime.now().strftime("%d.%m.%Y")
    
    lessons = await get_schedule(today)
    message = format_schedule(lessons, today_readable)
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /tomorrow
    """
    await update.message.reply_text("Получаю расписание на завтра... ⏳")
    
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d-%m-%Y")
    tomorrow_readable = (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y")
    
    lessons = await get_schedule(tomorrow)
    message = format_schedule(lessons, tomorrow_readable)
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def date_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик команды /date
    """
    await update.message.reply_text(
        "Введите дату, на которую нужно получить расписание, в формате ДД-ММ-ГГГГ.\n"
        "Например: 15-03-2025"
    )
    
    return WAITING_FOR_DATE

async def process_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик ввода даты
    """
    date_text = update.message.text.strip()
    
    # Проверка формата даты
    try:
        datetime.strptime(date_text, "%d-%m-%Y")
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат даты. Пожалуйста, используйте формат ДД-ММ-ГГГГ (например, 15-03-2025)."
        )
        return WAITING_FOR_DATE
    
    await update.message.reply_text(f"Получаю расписание на {date_text}... ⏳")
    
    # Получаем и отправляем расписание
    date_readable = datetime.strptime(date_text, "%d-%m-%Y").strftime("%d.%m.%Y")
    
    lessons = await get_schedule(date_text)
    message = format_schedule(lessons, date_readable)
    
    await update.message.reply_text(message, parse_mode="Markdown")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик отмены ввода даты
    """
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик ошибок
    """
    logger.error(f"Произошла ошибка: {context.error}")
    
    # Отправляем сообщение об ошибке пользователю
    error_message = "❌ Произошла ошибка при выполнении команды. Пожалуйста, попробуйте позже."
    
    if update.effective_message:
        await update.effective_message.reply_text(error_message)

def main():
    """
    Основная функция запуска бота
    """
    # Получаем токен бота из переменных окружения
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Не задан токен бота. Укажите TELEGRAM_BOT_TOKEN в файле .env")
        return
    
    # Создаем приложение
    application = Application.builder().token(token).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("tomorrow", tomorrow_command))
    
    # Добавляем обработчик ввода даты
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("date", date_command)],
        states={
            WAITING_FOR_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("Запускаем бота...")
    application.run_polling()

if __name__ == "__main__":
    main() 