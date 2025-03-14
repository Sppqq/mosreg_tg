import os
import logging
import asyncio
from datetime import datetime, timedelta, date
import calendar
import pickle
import time
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
WAITING_FOR_TIME = 2
WAITING_FOR_CONFIRMATION = 3

# Глобальный кэш для хранения расписания, чтобы не запрашивать его повторно
schedule_cache = {}
# Время жизни кэша в секундах (24 часа)
CACHE_TTL = 86400

# Словарь для хранения настроек автоматических рассылок для групп
group_subscriptions = {}
# Имя файла для хранения настроек групп
GROUP_SETTINGS_FILE = 'group_settings.pkl'

# Функция для получения расписания
async def get_schedule(date=None):
    """
    Асинхронная функция для получения расписания на указанную дату с использованием кэша
    """
    global schedule_cache
    current_time = time.time()
    
    # Если дата не указана, используем сегодняшнюю
    if date is None:
        date = datetime.now().strftime("%d-%m-%Y")
    
    # Проверяем кэш с увеличенным временем жизни для ускорения
    if date in schedule_cache and current_time - schedule_cache[date]['timestamp'] < CACHE_TTL:
        logger.info(f"Используем кэшированное расписание для {date}")
        return schedule_cache[date]['data']
    
    # Если данных нет в кэше или они устарели, получаем новые
    logger.info(f"Запрашиваем новое расписание для {date}")
    
    # Используем ThreadPoolExecutor для запуска блокирующего кода в отдельном потоке
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        def get_schedule_blocking():
            try:
                # Устанавливаем таймаут для ускорения процесса при проблемах с сетью
                scheduler = MosregSchedule(headless=True)  # Используем headless режим для бота
                lessons = scheduler.get_schedule(date)
                scheduler.close()
                return lessons
            except Exception as e:
                logger.error(f"Ошибка при получении расписания: {e}")
                return None
        
        # Запускаем блокирующий код в отдельном потоке с таймаутом
        try:
            # Устанавливаем таймаут в 30 секунд для запроса
            lessons = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(pool, get_schedule_blocking),
                timeout=30
            )
        except asyncio.TimeoutError:
            logger.error(f"Таймаут при получении расписания для {date}")
            # Проверяем, есть ли кешированное расписание, даже устаревшее
            if date in schedule_cache:
                logger.info(f"Используем устаревшее кешированное расписание для {date}")
                return schedule_cache[date]['data']
            return None
        
        # Сохраняем результат в кэш
        if lessons is not None:
            schedule_cache[date] = {
                'data': lessons,
                'timestamp': current_time
            }
            
            # Сохраняем кэш на диск для долговременного хранения
            try:
                with open('schedule_cache.pkl', 'wb') as f:
                    pickle.dump(schedule_cache, f)
            except Exception as e:
                logger.error(f"Ошибка при сохранении кэша: {e}")
                
        return lessons

# Загрузка кэша при запуске
def load_cache():
    global schedule_cache
    try:
        if os.path.exists('schedule_cache.pkl'):
            with open('schedule_cache.pkl', 'rb') as f:
                schedule_cache = pickle.load(f)
                logger.info(f"Загружен кэш расписания с {len(schedule_cache)} записями")
    except Exception as e:
        logger.error(f"Ошибка при загрузке кэша: {e}")
        schedule_cache = {}

# Загрузка настроек групп
def load_group_settings():
    global group_subscriptions
    try:
        if os.path.exists(GROUP_SETTINGS_FILE):
            with open(GROUP_SETTINGS_FILE, 'rb') as f:
                group_subscriptions = pickle.load(f)
                logger.info(f"Загружены настройки для {len(group_subscriptions)} групп")
    except Exception as e:
        logger.error(f"Ошибка при загрузке настроек групп: {e}")
        group_subscriptions = {}

# Сохранение настроек групп
def save_group_settings():
    try:
        with open(GROUP_SETTINGS_FILE, 'wb') as f:
            pickle.dump(group_subscriptions, f)
        logger.info(f"Сохранены настройки для {len(group_subscriptions)} групп")
    except Exception as e:
        logger.error(f"Ошибка при сохранении настроек групп: {e}")

# Получение русского названия дня недели
def get_weekday_name(date_str):
    """
    Получает русское название дня недели из строки даты формата DD.MM.YYYY
    """
    weekday_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    try:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        return weekday_names[dt.weekday()]
    except Exception as e:
        logger.error(f"Ошибка при получении дня недели: {e}")
        return ""

# Форматирование расписания для отправки в сообщении
def format_schedule(lessons, date_str):
    """
    Форматирование расписания для отправки в сообщении
    """
    if not lessons or len(lessons) == 0:
        weekday = get_weekday_name(date_str)
        return f"❌ На {date_str} ({weekday}) расписание не найдено или нет уроков."
    
    # Фильтруем уроки, исключая те, что в списке игнорирования
    filtered_lessons = []
    for lesson in lessons:
        subject = lesson['subject']
        should_ignore = False
        
        # Проверяем начинается ли название предмета со слова "Группа"
        if subject.lower().startswith("группа"):
            # Но НЕ игнорируем те, что содержат "_РОВ"
            if "_ров" not in subject.lower():
                should_ignore = True
                
        # Добавляем урок в отфильтрованный список, только если его не нужно игнорировать
        if not should_ignore:
            filtered_lessons.append(lesson)
    
    # Если после фильтрации не осталось уроков
    if not filtered_lessons:
        weekday = get_weekday_name(date_str)
        return f"❌ На {date_str} ({weekday}) уроков нет, или все они входят в список игнорируемых."
    
    # Получаем день недели
    weekday = get_weekday_name(date_str)
    message = f"📅 *Расписание на {date_str} ({weekday})*\n\n"
    
    # Словарь эмодзи для предметов
    subject_emojis = {
        "математика": "🔢",
        "алгебра": "🧮",
        "геометрия": "📐",
        "русский": "🇷🇺",
        "литература": "📚",
        "английский": "🇬🇧",
        "иностранный": "🌍",
        "история": "🏛️",
        "обществознание": "👥",
        "география": "🗺️",
        "биология": "🧬",
        "химия": "🧪",
        "физика": "⚛️",
        "информатика": "💻",
        "физическая культура": "🏃‍♂️",
        "физкультура": "🏋️",
        "изо": "🎨",
        "музыка": "🎵",
        "технология": "🔧",
        "обж": "🚨",
        "группа": "👥"
    }
    
    for i, lesson in enumerate(filtered_lessons, 1):
        subject = lesson['subject']
        
        # Добавление специального эмодзи в зависимости от предмета
        emoji = "📝"  # Эмодзи по умолчанию
        for key, value in subject_emojis.items():
            if key.lower() in subject.lower():
                emoji = value
                break
        
        message += f"{emoji} *{i}. {subject}*"
        
        if lesson['start_time'] != "Не указано":
            time_str = f" ⏰ {lesson['start_time']}"
            if lesson['end_time'] != "Не указано":
                time_str += f" - {lesson['end_time']}"
            message += time_str
        
        message += "\n"
        
        # Добавляем домашнее задание с эмодзи
        homework = lesson.get('homework', "Не указано")
        if homework == "Не указано" or not homework:
            homework = "без дз"
            homework_emoji = "✅"
        elif "нет" in homework.lower() or "без" in homework.lower():
            homework_emoji = "✅"
        else:
            homework_emoji = "📒"
        
        message += f"{homework_emoji} ДЗ: {homework}\n\n"
    
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
        "/week - расписание на текущую неделю\n"
        "/month - календарь на текущий месяц\n"
        "/date - расписание на конкретную дату\n"
        "/groups - настройка ежедневной отправки расписания на завтра в группу (только для администраторов)\n"
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
        "/week - расписание на текущую неделю\n"
        "/month - календарь на текущий месяц\n"
        "/date - расписание на конкретную дату\n"
        "/groups - настройка ежедневной отправки расписания на завтра в группу (только для администраторов)\n"
        "/help - показать это сообщение\n\n"
        "*Получение расписания на дату:*\n"
        "1. Отправьте команду /date\n"
        "2. Введите дату в формате ДД-ММ-ГГГГ (например, 15-03-2025)\n\n"
        "*Настройка автоматической отправки расписания:*\n"
        "1. В групповом чате отправьте команду /groups (только администраторы)\n"
        "2. Введите время в формате ЧЧ:ММ (например, 07:30)\n"
        "3. Подтвердите настройку отправив 'Да'\n"
        "4. Расписание будет отправляться на завтрашний день (кроме пятницы и субботы)\n\n"
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

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /week - отображает расписание на текущую неделю
    """
    await update.message.reply_text("Получаю расписание на текущую неделю... ⏳")
    
    # Определяем текущую дату
    today = datetime.now().date()
    
    # Определяем начало и конец недели (понедельник и воскресенье)
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    
    # Формируем общее сообщение
    message = f"📆 *Расписание на неделю ({start_of_week.strftime('%d.%m')} - {end_of_week.strftime('%d.%m')})*\n\n"
    
    # Добавляем кнопки с днями недели
    keyboard = []
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    row = []
    
    for i in range(7):
        day_date = start_of_week + timedelta(days=i)
        callback_data = f"date_{day_date.strftime('%d-%m-%Y')}"
        button_text = f"{day_names[i]} ({day_date.strftime('%d.%m')})"
        
        # Выделяем текущий день
        if day_date == today:
            button_text = f"✅ {button_text}"
            
        row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
        
        # Создаем ряды по 2-3 кнопки
        if len(row) == 3 or i == 6:
            keyboard.append(row)
            row = []
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Выберите день недели для просмотра расписания:",
        reply_markup=reply_markup
    )

async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /month - отображает календарь на текущий месяц
    """
    # Получаем текущий месяц и год
    now = datetime.now()
    month = now.month
    year = now.year
    
    # Создаем календарь для этого месяца
    await show_calendar(update, context, month, year)

async def show_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE, month, year):
    """
    Отображает календарь на указанный месяц
    """
    # Получаем информацию о месяце
    cal = calendar.monthcalendar(year, month)
    month_name = calendar.month_name[month]
    
    # Формируем заголовок календаря
    header = f"📅 *Календарь на {month_name} {year}*\n\n"
    
    # Создаем клавиатуру для календаря
    keyboard = []
    
    # Добавляем навигационные кнопки (предыдущий/следующий месяц)
    nav_row = []
    # Кнопка для предыдущего месяца
    prev_month = month - 1
    prev_year = year
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1
    nav_row.append(InlineKeyboardButton("◀️ Пред", callback_data=f"calendar_{prev_year}_{prev_month}"))
    
    # Кнопка для следующего месяца
    next_month = month + 1
    next_year = year
    if next_month == 13:
        next_month = 1
        next_year += 1
    nav_row.append(InlineKeyboardButton("След ▶️", callback_data=f"calendar_{next_year}_{next_month}"))
    
    keyboard.append(nav_row)
    
    # Добавляем названия дней недели
    days_row = []
    for day in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]:
        days_row.append(InlineKeyboardButton(day, callback_data="ignore"))
    keyboard.append(days_row)
    
    # Добавляем дни месяца
    today = date.today()
    for week in cal:
        week_row = []
        for day in week:
            if day == 0:
                # Пустые дни (до начала или после конца месяца)
                week_row.append(InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                # Форматируем дату для callback_data
                callback_date = f"{day:02d}-{month:02d}-{year}"
                
                # Выделяем текущий день
                if day == today.day and month == today.month and year == today.year:
                    day_text = f"✅{day}"
                else:
                    day_text = str(day)
                    
                week_row.append(InlineKeyboardButton(day_text, callback_data=f"date_{callback_date}"))
        
        if week_row:  # Добавляем только непустые ряды
            keyboard.append(week_row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Отправляем сообщение с календарем
    if update.callback_query:
        # Если это обновление существующего календаря
        await update.callback_query.edit_message_text(
            text=header,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        # Если это новый календарь
        await update.message.reply_text(
            text=header,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def calendar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик callback-запросов от календаря
    """
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    
    if callback_data.startswith("calendar_"):
        # Обработка навигации по календарю
        _, year, month = callback_data.split("_")
        await show_calendar(update, context, int(month), int(year))
    
    elif callback_data.startswith("date_"):
        # Обработка выбора даты
        _, date_str = callback_data.split("_")
        
        # Отображаем сообщение о загрузке
        await query.edit_message_text(f"Получаю расписание на {date_str.replace('-', '.')}... ⏳")
        
        # Получаем расписание на выбранную дату
        try:
            date_readable = datetime.strptime(date_str, "%d-%m-%Y").strftime("%d.%m.%Y")
            lessons = await get_schedule(date_str)
            message = format_schedule(lessons, date_readable)
            
            # Отправляем расписание
            await query.edit_message_text(
                text=message, 
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка при получении расписания на дату {date_str}: {e}")
            await query.edit_message_text(f"❌ Произошла ошибка при получении расписания на {date_str.replace('-', '.')}.")
    
    elif callback_data == "ignore":
        # Игнорируем нажатия на заголовки дней недели и пустые ячейки
        pass

async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик команды /groups - настройка ежедневной отправки расписания в группу
    """
    # Проверяем, является ли пользователь администратором группы
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type == 'private':
        await update.message.reply_text(
            "Эта команда доступна только в групповых чатах."
        )
        return ConversationHandler.END
    
    # Проверяем права администратора
    chat_member = await context.bot.get_chat_member(chat.id, user.id)
    if chat_member.status not in ['administrator', 'creator']:
        await update.message.reply_text(
            "Только администраторы группы могут настраивать автоматическую отправку расписания."
        )
        return ConversationHandler.END
    
    # Сохраняем идентификатор чата в контексте
    context.user_data['chat_id'] = chat.id
    
    # Если группа уже настроена, покажем текущие настройки
    if str(chat.id) in group_subscriptions:
        settings = group_subscriptions[str(chat.id)]
        await update.message.reply_text(
            f"Текущие настройки для этой группы:\n"
            f"Время отправки: {settings['time']}\n\n"
            f"Хотите изменить настройки? Отправьте время в формате ЧЧ:ММ (например, 07:30).\n"
            f"Или отправьте /cancel для отмены, или /disable для отключения автоматической отправки."
        )
    else:
        await update.message.reply_text(
            "Настройка ежедневной отправки расписания на завтрашний день в группу.\n\n"
            "Отправьте время в формате ЧЧ:ММ (например, 07:30), в которое будет отправляться расписание на завтра.\n"
            "Обратите внимание: расписание не будет отправляться в пятницу и субботу.\n\n"
            "Или отправьте /cancel для отмены."
        )
    
    return WAITING_FOR_TIME

async def process_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик ввода времени для отправки расписания
    """
    time_text = update.message.text.strip()
    
    # Проверка формата времени
    try:
        hours, minutes = map(int, time_text.split(':'))
        if not (0 <= hours < 24 and 0 <= minutes < 60):
            raise ValueError("Некорректное время")
    except ValueError:
        await update.message.reply_text(
            "❌ Некорректный формат времени. Пожалуйста, используйте формат ЧЧ:ММ (например, 07:30)."
        )
        return WAITING_FOR_TIME
    
    # Сохраняем время в контексте
    context.user_data['time'] = time_text
    
    await update.message.reply_text(
        f"Расписание на завтра будет отправляться ежедневно в {time_text}.\n"
        f"(кроме пятницы и субботы, так как на следующий день выходные)\n\n"
        f"Подтвердите настройку отправив 'Да' или отмените отправив /cancel."
    )
    
    return WAITING_FOR_CONFIRMATION

async def confirm_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обработчик подтверждения настройки отправки расписания
    """
    confirmation = update.message.text.strip().lower()
    
    if confirmation != 'да':
        await update.message.reply_text("Настройка отменена.")
        return ConversationHandler.END
    
    # Получаем сохраненные данные
    chat_id = context.user_data.get('chat_id')
    time_text = context.user_data.get('time')
    
    if not chat_id or not time_text:
        await update.message.reply_text("Ошибка: не хватает данных для настройки. Пожалуйста, начните снова.")
        return ConversationHandler.END
    
    # Сохраняем настройки группы
    group_subscriptions[str(chat_id)] = {
        'time': time_text,
        'last_sent_date': None
    }
    
    # Сохраняем настройки на диск
    save_group_settings()
    
    await update.message.reply_text(
        f"✅ Настройка завершена! Расписание на завтра будет отправляться ежедневно в {time_text}.\n"
        f"(кроме пятницы и субботы, так как на следующий день выходные)"
    )
    
    return ConversationHandler.END

async def disable_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Отключение автоматической отправки расписания
    """
    chat = update.effective_chat
    
    if str(chat.id) in group_subscriptions:
        del group_subscriptions[str(chat.id)]
        save_group_settings()
        await update.message.reply_text("✅ Автоматическая отправка расписания отключена.")
    else:
        await update.message.reply_text("❌ Автоматическая отправка расписания не была настроена для этой группы.")
    
    return ConversationHandler.END

async def check_group_schedules(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Функция для проверки и отправки расписаний в настроенные группы
    Отправляет расписание на завтра, кроме пятницы и субботы
    """
    current_time = datetime.now()
    current_hour = current_time.hour
    current_minute = current_time.minute
    current_date = current_time.strftime("%d.%m.%Y")
    current_weekday = current_time.weekday()  # 0-6, где 0 - понедельник, 6 - воскресенье
    
    # Не отправляем расписание на следующий день в пятницу (4) и субботу (5)
    if current_weekday in [4, 5]:
        return
    
    # Получаем дату на завтра
    tomorrow_date = current_time + timedelta(days=1)
    tomorrow = tomorrow_date.strftime("%d-%m-%Y")
    tomorrow_readable = tomorrow_date.strftime("%d.%m.%Y")
    
    for chat_id, settings in group_subscriptions.items():
        try:
            # Парсим время отправки
            hours, minutes = map(int, settings['time'].split(':'))
            
            # Проверяем, наступило ли время отправки
            if current_hour == hours and current_minute == minutes:
                # Проверяем, не отправляли ли мы уже расписание сегодня
                if settings.get('last_sent_date') != current_date:
                    # Получаем расписание на завтра
                    lessons = await get_schedule(tomorrow)
                    if lessons is not None:
                        message = format_schedule(lessons, tomorrow_readable)
                        
                        # Отправляем расписание в группу
                        await context.bot.send_message(
                            chat_id=int(chat_id),
                            text=message,
                            parse_mode="Markdown"
                        )
                        
                        # Обновляем дату последней отправки
                        group_subscriptions[chat_id]['last_sent_date'] = current_date
                        save_group_settings()
                        
                        logger.info(f"Расписание на завтра ({tomorrow_readable}) отправлено в группу {chat_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке расписания в группу {chat_id}: {e}")

def main():
    """
    Основная функция запуска бота
    """
    # Получаем токен бота из переменных окружения
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Не задан токен бота. Укажите TELEGRAM_BOT_TOKEN в файле .env")
        return
    
    # Загружаем кэш и настройки групп
    load_cache()
    load_group_settings()
    
    # Создаем приложение
    application = Application.builder().token(token).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("tomorrow", tomorrow_command))
    application.add_handler(CommandHandler("week", week_command))
    application.add_handler(CommandHandler("month", month_command))
    
    # Добавляем обработчик ввода даты
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("date", date_command)],
        states={
            WAITING_FOR_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
    
    # Добавляем обработчик для команды /groups
    groups_handler = ConversationHandler(
        entry_points=[CommandHandler("groups", groups_command)],
        states={
            WAITING_FOR_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_time),
                CommandHandler("disable", disable_subscription)
            ],
            WAITING_FOR_CONFIRMATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_subscription)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(groups_handler)
    
    # Добавляем обработчик callback-запросов для календаря
    application.add_handler(CallbackQueryHandler(calendar_callback))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Добавляем задачу для периодической проверки и отправки расписаний в группы
    job_queue = application.job_queue
    job_queue.run_repeating(check_group_schedules, interval=60, first=10)
    
    # Запускаем бота
    logger.info("Запускаем бота...")
    application.run_polling()

if __name__ == "__main__":
    main() 