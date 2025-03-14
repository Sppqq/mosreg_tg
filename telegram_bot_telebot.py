import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import telebot
from telebot import types
from mosreg_schedule_selenium import MosregSchedule
import threading
import queue

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = telebot.TeleBot(os.getenv("TELEGRAM_BOT_TOKEN"))

# Очередь для асинхронных операций
schedule_queue = queue.Queue()

def get_next_workday(current_date):
    """
    Получение следующего рабочего дня (пропускает выходные)
    """
    next_day = current_date + timedelta(days=1)
    while next_day.weekday() >= 5:  # 5 - суббота, 6 - воскресенье
        next_day += timedelta(days=1)
    return next_day

def get_schedule(date=None):
    """
    Функция для получения расписания на указанную дату
    """
    try:
        scheduler = MosregSchedule(headless=True)
        lessons = scheduler.get_schedule(date)
        scheduler.close()
        return lessons
    except Exception as e:
        logger.error(f"Ошибка при получении расписания: {e}")
        return None

def format_schedule(lessons, date_str):
    """
    Форматирование расписания для отправки в сообщении
    """
    if not lessons:
        return f"📅 *На {date_str} уроков нет*\n\nВозможно, это выходной день или каникулы."
    
    if len(lessons) == 0:
        return f"📅 *На {date_str} уроков нет*\n\nВозможно, это выходной день или каникулы."
    
    message = f"📅 *Расписание на {date_str}*\n\n"
    
    for i, lesson in enumerate(lessons, 1):
        message += f"*{i}. {lesson['subject']}*\n"
        
        if lesson['start_time'] != "Не указано" or lesson['end_time'] != "Не указано":
            time_str = f"{lesson['start_time']}"
            if lesson['end_time'] != "Не указано":
                time_str += f" - {lesson['end_time']}"
            message += f"⏰ {time_str}\n"
        
        # Добавляем домашнее задание, если оно есть
        if 'homework' in lesson and lesson['homework'] != "Не указано":
            message += f"📚 Задание: {lesson['homework']}\n"
        
        message += "\n"
    
    return message

def process_schedule_request(message, date):
    """
    Обработка запроса расписания в отдельном потоке
    """
    try:
        lessons = get_schedule(date)
        schedule_queue.put((message.chat.id, format_schedule(lessons, date)))
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса: {e}")
        schedule_queue.put((message.chat.id, "❌ Произошла ошибка при получении расписания. Попробуйте позже."))

@bot.message_handler(commands=['start'])
def start(message):
    """
    Обработчик команды /start
    """
    user = message.from_user
    bot.reply_to(
        message,
        f"Привет, {user.first_name}! 👋\n\n"
        "Я бот для получения расписания из МЭШ.\n\n"
        "Используйте следующие команды:\n"
        "/today - расписание на сегодня\n"
        "/tomorrow - расписание на завтра\n"
        "/date - расписание на конкретную дату\n"
        "/help - помощь по использованию бота"
    )

@bot.message_handler(commands=['help'])
def help_command(message):
    """
    Обработчик команды /help
    """
    help_text = (
        "📚 *Помощь по использованию бота*\n\n"
        "*Доступные команды:*\n"
        "/today - расписание на сегодня\n"
        "/tomorrow - расписание на завтра\n"
        "/date - расписание на конкретную дату\n"
        "/help - показать это сообщение\n\n"
        "*Получение расписания на дату:*\n"
        "1. Отправьте команду /date\n"
        "2. Введите дату в формате ДД-ММ-ГГГГ (например, 15-03-2025)\n\n"
        "Бот работает с использованием куки из файла cookies.json, которые должны быть действительными."
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['today'])
def today_command(message):
    """
    Обработчик команды /today
    """
    bot.reply_to(message, "Получаю расписание на сегодня... ⏳")
    
    today = datetime.now().strftime("%d-%m-%Y")
    today_readable = datetime.now().strftime("%d.%m.%Y")
    
    # Запускаем получение расписания в отдельном потоке
    thread = threading.Thread(
        target=process_schedule_request,
        args=(message, today)
    )
    thread.start()

@bot.message_handler(commands=['tomorrow'])
def tomorrow_command(message):
    """
    Обработчик команды /tomorrow
    """
    today = datetime.now()
    next_workday = get_next_workday(today)
    
    # Форматируем даты для запроса и отображения
    next_workday_str = next_workday.strftime("%d-%m-%Y")
    next_workday_readable = next_workday.strftime("%d.%m.%Y")
    
    # Определяем, является ли следующий день выходным
    if next_workday.weekday() >= 5:
        day_name = "понедельник" if next_workday.weekday() == 0 else "следующий рабочий день"
        bot.reply_to(message, f"Получаю расписание на {day_name} ({next_workday_readable})... ⏳")
    else:
        bot.reply_to(message, f"Получаю расписание на завтра ({next_workday_readable})... ⏳")
    
    # Запускаем получение расписания в отдельном потоке
    thread = threading.Thread(
        target=process_schedule_request,
        args=(message, next_workday_str)
    )
    thread.start()

@bot.message_handler(commands=['date'])
def date_command(message):
    """
    Обработчик команды /date
    """
    msg = bot.reply_to(
        message,
        "Введите дату, на которую нужно получить расписание, в формате ДД-ММ-ГГГГ.\n"
        "Например: 15-03-2025"
    )
    bot.register_next_step_handler(msg, process_date)

def process_date(message):
    """
    Обработчик ввода даты
    """
    date_text = message.text.strip()
    
    # Проверка формата даты
    try:
        datetime.strptime(date_text, "%d-%m-%Y")
    except ValueError:
        bot.reply_to(
            message,
            "❌ Неверный формат даты. Пожалуйста, используйте формат ДД-ММ-ГГГГ (например, 15-03-2025)."
        )
        return
    
    bot.reply_to(message, f"Получаю расписание на {date_text}... ⏳")
    
    # Запускаем получение расписания в отдельном потоке
    thread = threading.Thread(
        target=process_schedule_request,
        args=(message, date_text)
    )
    thread.start()

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    """
    Обработчик всех остальных сообщений
    """
    bot.reply_to(message, "Используйте команды бота для получения расписания. /help для справки.")

def check_schedule_queue():
    """
    Проверка очереди сообщений с расписанием
    """
    while True:
        try:
            chat_id, message = schedule_queue.get_nowait()
            bot.send_message(chat_id, message, parse_mode="Markdown")
        except queue.Empty:
            break
    bot.threading.Timer(1.0, check_schedule_queue).start()

def main():
    """
    Основная функция запуска бота
    """
    # Запускаем проверку очереди сообщений
    check_schedule_queue()
    
    # Запускаем бота
    logger.info("Запускаем бота...")
    bot.infinity_polling()

if __name__ == "__main__":
    main() 