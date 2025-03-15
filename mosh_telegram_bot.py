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
import concurrent.futures

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
# WAITING_FOR_DATE = 1  # Удалено - больше не используется
WAITING_FOR_TIME = 2
WAITING_FOR_CONFIRMATION = 3

# Глобальный кэш для хранения расписания, чтобы не запрашивать его повторно
schedule_cache = {}
# Время жизни кэша в секундах (увеличено с 24 до 48 часов)
CACHE_TTL = 172800  # 48 часов

# Словарь для хранения настроек автоматических рассылок для групп
group_subscriptions = {}
# Имя файла для хранения настроек групп
GROUP_SETTINGS_FILE = 'group_settings.pkl'

# Глобальный экземпляр MosregSchedule для повторного использования
scheduler_instance = None
scheduler_last_used = 0
SCHEDULER_TIMEOUT = 600  # 10 минут неактивности до закрытия

# Глобальный пул потоков для параллельного получения данных
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# Словарь для хранения времени последнего обновления расписания для каждого пользователя и даты
last_refresh_times = {}
# Словарь для хранения реального времени последнего обновления расписания для каждой даты
last_update_times = {}
# Кулдаун для кнопки обновления в секундах (5 минут)
REFRESH_COOLDOWN = 300
# Имя файла для хранения времени последнего обновления
LAST_UPDATE_FILE = 'last_update_times.pkl'
# Имя файла для хранения статуса домашних заданий
HW_STATUS_FILE = 'hw_status.pkl'

# Словарь для хранения статуса домашних заданий для пользователей
hw_status_data = {}

# Функция для инициализации или получения существующего экземпляра планировщика
async def get_scheduler():
    global scheduler_instance, scheduler_last_used
    
    current_time = time.time()
    
    # Если экземпляр не существует или прошло слишком много времени с последнего использования
    if scheduler_instance is None or current_time - scheduler_last_used > SCHEDULER_TIMEOUT:
        # Закрываем старый экземпляр, если он существует
        if scheduler_instance is not None:
            try:
                scheduler_instance.close()
            except Exception as e:
                logger.error(f"Ошибка при закрытии старого экземпляра: {e}")
        
        # Создаем новый экземпляр в отдельном потоке
        def create_scheduler():
            try:
                # ВАЖНО: Для оптимизации класса MosregSchedule нужно внести следующие изменения:
                # 1. Добавить параметр direct_navigation в конструктор класса
                # 2. Если direct_navigation=True, не переходить на страницу расписания при инициализации
                # 3. Модифицировать метод get_schedule, чтобы он принимал параметр direct_date_navigation
                # 4. Реализовать прямой переход на URL с нужной датой, например:
                #    url = f"https://school.mosreg.ru/schedules/day?date={date_str}"
                return MosregSchedule(headless=True)
            except Exception as e:
                logger.error(f"Ошибка при создании экземпляра планировщика: {e}")
                return None
        
        try:
            scheduler_instance = await asyncio.get_event_loop().run_in_executor(thread_pool, create_scheduler)
            if scheduler_instance is None:
                logger.error("Не удалось создать экземпляр планировщика")
                return None
        except Exception as e:
            logger.error(f"Исключение при создании экземпляра планировщика: {e}")
            return None
    
    # Обновляем время последнего использования
    scheduler_last_used = current_time
    return scheduler_instance

# Функция для получения расписания
async def get_schedule(date=None, force_refresh=False):
    """
    Асинхронная функция для получения расписания на указанную дату с использованием кэша
    и прямого перехода на страницу нужного дня
    """
    global schedule_cache, last_update_times
    current_time = time.time()
    
    # Если дата не указана, используем сегодняшнюю
    if date is None:
        date = datetime.now().strftime("%d-%m-%Y")
    
    # Проверяем кэш, если не требуется принудительное обновление
    if not force_refresh and date in schedule_cache and current_time - schedule_cache[date]['timestamp'] < CACHE_TTL:
        logger.info(f"Используем кэшированное расписание для {date}")
        return schedule_cache[date]['data']
    
    # Если данных нет в кэше или они устарели, получаем новые
    logger.info(f"Запрашиваем новое расписание для {date}")
    
    # Получаем или создаем экземпляр планировщика
    scheduler = await get_scheduler()
    if scheduler is None:
        logger.error("Не удалось получить экземпляр планировщика")
        # Проверяем, есть ли кешированное расписание, даже устаревшее
        if date in schedule_cache:
            logger.info(f"Используем устаревшее кешированное расписание для {date}")
            return schedule_cache[date]['data']
        return None
    
    # Преобразуем дату в формат, необходимый для URL (если требуется)
    day, month, year = date.split('-')
    formatted_date = f"{day}.{month}.{year}"
    
    # Используем ThreadPoolExecutor для запуска блокирующего кода в отдельном потоке
    def get_schedule_blocking():
        try:
            # ВАЖНО: Для оптимизации метода get_schedule в классе MosregSchedule:
            # 1. Добавить параметр direct_date_navigation, который по умолчанию False
            # 2. Если direct_date_navigation=True, использовать прямой URL с датой
            # Пример реализации:
            # if direct_date_navigation:
            #     day, month, year = date.split('-')
            #     date_param = f"{day}.{month}.{year}"
            #     driver.get(f"https://school.mosreg.ru/schedules/day?date={date_param}")
            # else:
            #     ... текущая логика выбора даты через UI ...
            
            # Сейчас используем существующий метод, который не имеет этой оптимизации
            lessons = scheduler.get_schedule(date)
            return lessons
        except Exception as e:
            logger.error(f"Ошибка при получении расписания: {e}")
            return None
    
    # Запускаем блокирующий код в отдельном потоке с таймаутом
    try:
        # Устанавливаем таймаут в 20 секунд для запроса
        lessons = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(thread_pool, get_schedule_blocking),
            timeout=20
        )
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при получении расписания для {date}")
        # Проверяем, есть ли кешированное расписание, даже устаревшее
        if date in schedule_cache:
            logger.info(f"Используем устаревшее кешированное расписание для {date}")
            return schedule_cache[date]['data']
        return None
    except Exception as e:
        logger.error(f"Необработанное исключение при получении расписания: {e}")
        if date in schedule_cache:
            return schedule_cache[date]['data']
        return None
    
    # Сохраняем результат в кэш
    if lessons is not None:
        schedule_cache[date] = {
            'data': lessons,
            'timestamp': current_time
        }
        
        # Обновляем информацию о последнем обновлении
        last_update_times[date] = {
            'timestamp': current_time,
            'datetime': datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        
        # Сохраняем информацию о последних обновлениях
        save_last_update_times()
        
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
    Возвращает кортеж (message, lessons) где message - отформатированное сообщение,
    а lessons - список уроков с домашними заданиями для создания кнопок
    """
    if not lessons or len(lessons) == 0:
        weekday = get_weekday_name(date_str)
        return f"❌ На {date_str} ({weekday}) расписание не найдено или нет уроков.", None
    
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
        return f"❌ На {date_str} ({weekday}) уроков нет, или все они входят в список игнорируемых.", None
    
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
    
    # Возвращаем отформатированное сообщение и отфильтрованные уроки
    return message, filtered_lessons

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
        "/month - календарь на текущий месяц\n"
        "/groups - настройка ежедневной отправки расписания на завтра в группу (только для администраторов)"
    )

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
    user_id = update.effective_user.id
    
    if callback_data.startswith("calendar_"):
        # Обработка навигации по календарю
        _, year, month = callback_data.split("_")
        await show_calendar(update, context, int(month), int(year))
    
    elif callback_data.startswith("date_"):
        # Обработка выбора даты
        _, date_str = callback_data.split("_", 1)
        
        # Отображаем сообщение о загрузке и показываем расписание
        await show_schedule_for_date(update, context, date_str)
    
    elif callback_data.startswith("refresh_"):
        # Обработка кнопки обновления расписания
        _, date_str = callback_data.split("_", 1)
        refresh_key = f"{user_id}_{date_str}"
        
        # Проверяем, прошло ли достаточно времени с последнего обновления
        current_time = time.time()
        last_refresh_time = last_refresh_times.get(refresh_key, 0)
        
        if current_time - last_refresh_time >= REFRESH_COOLDOWN:
            # Если прошло достаточно времени, изменяем текст кнопки на "Обновление..."
            keyboard = []
            for row in query.message.reply_markup.inline_keyboard:
                new_row = []
                for button in row:
                    if button.callback_data == callback_data:
                        new_row.append(InlineKeyboardButton("🔄 Обновление...", callback_data="ignore"))
                    else:
                        new_row.append(button)
                keyboard.append(new_row)
                
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
            
            # Обновляем расписание и сохраняем время последнего обновления
            last_refresh_times[refresh_key] = current_time
            # Сохраняем реальное время обновления
            date_readable = datetime.strptime(date_str, "%d-%m-%Y").strftime("%d.%m.%Y")
            last_update_times[date_str] = {
                'timestamp': current_time,
                'datetime': datetime.now().strftime("%d.%m.%Y %H:%M")
            }
            await show_schedule_for_date(update, context, date_str, force_refresh=True)
        else:
            # Если не прошло достаточно времени, показываем сообщение об ошибке
            remaining_time = int(REFRESH_COOLDOWN - (current_time - last_refresh_time))
            await query.answer(f"Подождите ещё {remaining_time} сек. перед обновлением", show_alert=True)
    
    elif callback_data.startswith("homework_"):
        # Обработка запроса на просмотр домашних заданий
        _, date_str = callback_data.split("_", 1)
        await show_homework_buttons(update, context, date_str)
    
    elif callback_data.startswith("hw_subject_"):
        # Обработка выбора предмета для просмотра ДЗ
        parts = callback_data.split("_", 3)
        date_str = parts[2]
        subject_index = int(parts[3])
        await show_homework_detail(update, context, date_str, subject_index)
    
    elif callback_data.startswith("hw_toggle_"):
        # Обработка переключения статуса ДЗ (выполнено/не выполнено)
        parts = callback_data.split("_")
        date_str = parts[2]
        subject_index = int(parts[3])
        current_status = int(parts[4])  # 0 - не выполнено, 1 - выполнено
        
        # Инвертируем статус
        new_status = not bool(current_status)
        
        # Сохраняем новый статус в глобальном хранилище
        user_id_str = str(user_id)
        hw_status_key = f"hw_status_{user_id_str}_{date_str}"
        
        # Инициализируем вложенные словари, если их нет
        if user_id_str not in hw_status_data:
            hw_status_data[user_id_str] = {}
        if date_str not in hw_status_data[user_id_str]:
            hw_status_data[user_id_str][date_str] = {}
        
        # Сохраняем новый статус
        subject_key = f"{date_str}_{subject_index}"
        hw_status_data[user_id_str][date_str][subject_key] = new_status
        
        # Сохраняем обновленные данные в файл
        save_hw_status()
        
        # Также сохраняем в context.user_data для обратной совместимости
        if not context.user_data.get(hw_status_key):
            context.user_data[hw_status_key] = {}
        context.user_data[hw_status_key][subject_key] = new_status
        
        # Обновляем список домашних заданий
        await show_homework_buttons(update, context, date_str)
    
    elif callback_data.startswith("back_to_schedule_"):
        # Возврат к расписанию
        _, _, _, date_str = callback_data.split("_", 3)
        
        # Показываем расписание через общую функцию
        await show_schedule_for_date(update, context, date_str)
    
    elif callback_data == "ignore":
        # Игнорируем нажатия на заголовки дней недели и пустые ячейки
        pass

async def show_homework_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str):
    """
    Отображает кнопки с предметами для просмотра домашних заданий
    """
    query = update.callback_query
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    
    # Получаем расписание на указанную дату
    date_readable = datetime.strptime(date_str, "%d-%m-%Y").strftime("%d.%m.%Y")
    lessons = await get_schedule(date_str)
    _, filtered_lessons = format_schedule(lessons, date_readable)
    
    if not filtered_lessons:
        await query.edit_message_text(
            text=f"❌ Не удалось получить домашние задания на {date_readable}",
            parse_mode="Markdown"
        )
        return
    
    # Получаем день недели
    weekday = get_weekday_name(date_readable)
    message = f"📚 *Домашние задания на {date_readable} ({weekday})*\n"
    
    # Создаем кнопки для каждого предмета в новом формате (3 колонки)
    keyboard = []
    
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
    
    # Получаем статусы заданий из глобального хранилища, если они есть
    hw_status = {}
    if user_id_str in hw_status_data and date_str in hw_status_data[user_id_str]:
        hw_status = hw_status_data[user_id_str][date_str]
    
    # Также проверяем context.user_data для обратной совместимости
    hw_status_key = f"hw_status_{user_id}_{date_str}"
    if not hw_status and hw_status_key in context.user_data:
        hw_status = context.user_data.get(hw_status_key, {})
    
    for i, lesson in enumerate(filtered_lessons):
        subject = lesson['subject']
        subject_key = f"{date_str}_{i}"
        
        # Определяем есть ли домашнее задание
        homework = lesson.get('homework', "Не указано")
        
        # Проверяем статус задания - выполнено или нет
        done = hw_status.get(subject_key, False)
        
        # Обрабатываем текст домашнего задания
        if homework == "Не указано" or not homework or "нет" in homework.lower() or "без" in homework.lower():
            homework_status = "✅"  # Статус "Задания нет" или "Выполнено"
            text_status = "Без ДЗ"
        else:
            # Если есть задание, статус зависит от того, отмечено ли оно как выполненное
            homework_status = "✅" if done else "📒"
            
            # Ограничиваем длину текста задания для кнопки
            if len(homework) > 15:
                text_status = homework[:12] + "..."
            else:
                text_status = homework
        
        # Ограничиваем длину названия предмета для кнопки
        subject_short = subject[:15] + "..." if len(subject) > 15 else subject
        
        # Создаем ряд из трех кнопок: статус, предмет, текст задания
        row = [
            InlineKeyboardButton(homework_status, callback_data=f"hw_toggle_{date_str}_{i}_{1 if done else 0}"),
            InlineKeyboardButton(subject_short, callback_data=f"hw_subject_{date_str}_{i}"),
            InlineKeyboardButton(text_status, callback_data=f"hw_subject_{date_str}_{i}")
        ]
        keyboard.append(row)
    
    # Извлекаем месяц и год из выбранной даты для возврата к календарю
    selected_date = datetime.strptime(date_str, "%d-%m-%Y")
    month = selected_date.month
    year = selected_date.year
    
    # Добавляем кнопки "Назад к расписанию" и "Назад в календарь"
    keyboard.append([InlineKeyboardButton("⬅️ Назад к расписанию", callback_data=f"back_to_schedule_{date_str}")])
    keyboard.append([InlineKeyboardButton("📅 Назад в календарь", callback_data=f"calendar_{year}_{month}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=message,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def show_homework_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str, subject_index):
    """
    Отображает детальную информацию о домашнем задании по выбранному предмету
    """
    query = update.callback_query
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    
    # Получаем расписание на указанную дату
    date_readable = datetime.strptime(date_str, "%d-%m-%Y").strftime("%d.%m.%Y")
    lessons = await get_schedule(date_str)
    _, filtered_lessons = format_schedule(lessons, date_readable)
    
    if not filtered_lessons or subject_index >= len(filtered_lessons):
        await query.edit_message_text(
            text=f"❌ Информация не найдена",
            parse_mode="Markdown"
        )
        return
    
    # Получаем информацию о выбранном предмете
    lesson = filtered_lessons[subject_index]
    subject = lesson['subject']
    
    # Получаем текущий статус ДЗ из глобального хранилища
    done = False
    subject_key = f"{date_str}_{subject_index}"
    
    if user_id_str in hw_status_data and date_str in hw_status_data[user_id_str]:
        done = hw_status_data[user_id_str][date_str].get(subject_key, False)
    else:
        # Проверяем также context.user_data для обратной совместимости
        hw_status_key = f"hw_status_{user_id}_{date_str}"
        if hw_status_key in context.user_data:
            done = context.user_data[hw_status_key].get(subject_key, False)
    
    # Форматируем информацию о домашнем задании
    homework = lesson.get('homework', "Не указано")
    if homework == "Не указано" or not homework:
        homework = "Нет домашнего задания"
        homework_emoji = "✅"
        status_text = "Нет задания"
    elif "нет" in homework.lower() or "без" in homework.lower():
        homework_emoji = "✅"
        status_text = "Нет задания"
    else:
        homework_emoji = "✅" if done else "📒"
        status_text = "Выполнено" if done else "Не выполнено"
    
    # Получаем день недели
    weekday = get_weekday_name(date_readable)
    
    # Создаем сообщение с информацией о домашнем задании
    time_info = ""
    if lesson['start_time'] != "Не указано":
        time_info = f" ⏰ {lesson['start_time']}"
        if lesson['end_time'] != "Не указано":
            time_info += f" - {lesson['end_time']}"
    
    message = f"📚 *Домашнее задание на {date_readable} ({weekday})*\n\n" \
              f"*Предмет: {subject}*{time_info}\n\n" \
              f"{homework_emoji} *Статус: {status_text}*\n" \
              f"*Домашнее задание:*\n{homework}"
    
    # Создаем кнопки
    keyboard = []
    
    # Добавляем кнопку для переключения статуса, если есть задание
    if not (homework == "Нет домашнего задания" or "нет" in homework.lower() or "без" in homework.lower()):
        toggle_text = "✅ Отметить как выполненное" if not done else "📒 Отметить как невыполненное"
        keyboard.append([InlineKeyboardButton(toggle_text, callback_data=f"hw_toggle_{date_str}_{subject_index}_{1 if done else 0}")])
    
    # Извлекаем месяц и год из выбранной даты для возврата к календарю
    selected_date = datetime.strptime(date_str, "%d-%m-%Y")
    month = selected_date.month
    year = selected_date.year
    
    # Добавляем кнопку для возврата к списку предметов
    keyboard.append([InlineKeyboardButton("⬅️ Назад к списку предметов", callback_data=f"homework_{date_str}")])
    # Добавляем кнопку "Назад в календарь"
    keyboard.append([InlineKeyboardButton("📅 Назад в календарь", callback_data=f"calendar_{year}_{month}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=message,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

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
    
    # Создаем список задач для отправки сообщений
    send_tasks = []
    
    for chat_id, settings in group_subscriptions.items():
        try:
            # Парсим время отправки
            hours, minutes = map(int, settings['time'].split(':'))
            
            # Проверяем, наступило ли время отправки
            if current_hour == hours and current_minute == minutes:
                # Проверяем, не отправляли ли мы уже расписание сегодня
                if settings.get('last_sent_date') != current_date:
                    # Добавляем задачу для отправки расписания
                    send_tasks.append(
                        send_schedule_to_group(
                            context.bot, 
                            int(chat_id), 
                            tomorrow, 
                            tomorrow_readable,
                            current_date
                        )
                    )
        except Exception as e:
            logger.error(f"Ошибка при обработке настроек группы {chat_id}: {e}")
    
    # Если есть задачи на отправку, выполняем их параллельно
    if send_tasks:
        await asyncio.gather(*send_tasks)

async def send_schedule_to_group(bot, chat_id, tomorrow, tomorrow_readable, current_date):
    """
    Вспомогательная функция для отправки расписания в группу
    """
    try:
        # Получаем расписание на завтра
        lessons = await get_schedule(tomorrow)
        if lessons is not None:
            message, filtered_lessons = format_schedule(lessons, tomorrow_readable)
            
            # Отправляем расписание в группу с кнопкой ДЗ, если есть уроки
            if filtered_lessons:
                keyboard = [[InlineKeyboardButton("📚 Перейти к ДЗ", callback_data=f"homework_{tomorrow}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="Markdown"
                )
            
            # Обновляем дату последней отправки
            group_subscriptions[str(chat_id)]['last_sent_date'] = current_date
            save_group_settings()
            
            logger.info(f"Расписание на завтра ({tomorrow_readable}) отправлено в группу {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке расписания в группу {chat_id}: {e}")

# Функция для периодической очистки кэша старых записей
def clean_cache():
    global schedule_cache, last_update_times, hw_status_data
    current_time = time.time()
    old_keys = []
    
    # Находим старые записи в кэше расписания
    for date, data in schedule_cache.items():
        if current_time - data['timestamp'] > CACHE_TTL * 2:
            old_keys.append(date)
    
    # Удаляем старые записи из кэша
    for key in old_keys:
        del schedule_cache[key]
    
    if old_keys:
        logger.info(f"Очищено {len(old_keys)} устаревших записей в кэше")
        # Сохраняем обновленный кэш
        try:
            with open('schedule_cache.pkl', 'wb') as f:
                pickle.dump(schedule_cache, f)
        except Exception as e:
            logger.error(f"Ошибка при сохранении кэша после очистки: {e}")
    
    # Очищаем также устаревшие записи о последних обновлениях
    old_update_keys = []
    for date, data in last_update_times.items():
        if current_time - data['timestamp'] > CACHE_TTL * 2:
            old_update_keys.append(date)
    
    # Удаляем старые записи из последних обновлений
    for key in old_update_keys:
        del last_update_times[key]
    
    if old_update_keys:
        logger.info(f"Очищено {len(old_update_keys)} устаревших записей о последних обновлениях")
        # Сохраняем обновленные данные о последних обновлениях
        save_last_update_times()
    
    # Очищаем устаревшие данные о домашних заданиях (старше 30 дней)
    MAX_HW_AGE = 30 * 24 * 60 * 60  # 30 дней в секундах
    current_date = datetime.now()
    cleanup_count = 0
    
    for user_id, dates in hw_status_data.items():
        dates_to_remove = []
        for date_str in dates:
            try:
                # Преобразуем строку даты в объект datetime
                task_date = datetime.strptime(date_str.split('_')[0], "%d-%m-%Y")
                # Если задание старше 30 дней, удаляем его
                if (current_date - task_date).total_seconds() > MAX_HW_AGE:
                    dates_to_remove.append(date_str)
                    cleanup_count += 1
            except Exception as e:
                logger.error(f"Ошибка при обработке даты ДЗ {date_str}: {e}")
        
        # Удаляем устаревшие записи
        for date_str in dates_to_remove:
            del hw_status_data[user_id][date_str]
        
        # Если у пользователя не осталось записей, удаляем его из словаря
        if not hw_status_data[user_id]:
            hw_status_data.pop(user_id, None)
    
    if cleanup_count > 0:
        logger.info(f"Очищено {cleanup_count} устаревших записей о домашних заданиях")
        # Сохраняем обновленные данные о домашних заданиях
        save_hw_status()

# Функция для корректного закрытия браузера при завершении работы
def shutdown():
    global scheduler_instance
    if scheduler_instance is not None:
        try:
            scheduler_instance.close()
            logger.info("Браузер успешно закрыт")
        except Exception as e:
            logger.error(f"Ошибка при закрытии браузера: {e}")
    
    # Сохраняем данные о статусе ДЗ перед выходом
    save_hw_status()
    
    # Закрываем пул потоков
    thread_pool.shutdown(wait=False)
    logger.info("Пул потоков закрыт")

async def show_schedule_for_date(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str, force_refresh=False):
    """
    Отображает расписание на выбранную дату с кнопкой обновления.
    
    Args:
        update: объект Update
        context: контекст бота
        date_str: строка с датой в формате DD-MM-YYYY
        force_refresh: принудительное обновление (игнорирует кулдаун)
    """
    global last_refresh_times
    
    query = update.callback_query
    user_id = update.effective_user.id
    
    try:
        # Проверяем необходимость загрузки сообщения
        if not force_refresh and not query.message.text.startswith("Получаю расписание"):
            await query.edit_message_text(f"Получаю расписание на {date_str.replace('-', '.')}... ⏳")
        
        # Получаем расписание на выбранную дату
        date_readable = datetime.strptime(date_str, "%d-%m-%Y").strftime("%d.%m.%Y")
        lessons = await get_schedule(date_str, force_refresh=force_refresh)
        message, filtered_lessons = format_schedule(lessons, date_readable)
        
        # Извлекаем месяц и год из выбранной даты для возврата к календарю
        selected_date = datetime.strptime(date_str, "%d-%m-%Y")
        month = selected_date.month
        year = selected_date.year
        
        keyboard = []
        
        # Добавляем кнопку "Перейти к ДЗ" только если есть уроки с домашними заданиями
        if filtered_lessons:
            keyboard.append([InlineKeyboardButton("📚 Перейти к ДЗ", callback_data=f"homework_{date_str}")])
        
        # Проверяем, можно ли обновить расписание (прошло ли 5 минут с последнего обновления)
        refresh_key = f"{user_id}_{date_str}"
        current_time = time.time()
        last_refresh_time = last_refresh_times.get(refresh_key, 0)
        can_refresh = current_time - last_refresh_time >= REFRESH_COOLDOWN
        
        # Если принудительное обновление, обновляем время последнего обновления
        if force_refresh:
            last_refresh_times[refresh_key] = current_time
            # Обновляем также реальное время обновления
            last_update_times[date_str] = {
                'timestamp': current_time,
                'datetime': datetime.now().strftime("%d.%m.%Y %H:%M")
            }
        
        # Добавляем информацию о последнем обновлении или кнопку обновления
        if date_str in last_update_times:
            # Показываем время последнего обновления
            update_info = last_update_times[date_str]['datetime']
            message += f"\n\n🔄 Обновлено: {update_info}"
            
            # Добавляем кнопку обновления, только если прошло время кулдауна
            if can_refresh:
                keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{date_str}")])
            else:
                # Расчитываем, сколько осталось времени до возможности обновления
                remaining_seconds = int(REFRESH_COOLDOWN - (current_time - last_refresh_time))
                remaining_minutes = remaining_seconds // 60
                remaining_seconds %= 60
                refresh_text = f"🔄 Обновление через {remaining_minutes}:{remaining_seconds:02d}"
                keyboard.append([InlineKeyboardButton(refresh_text, callback_data="ignore")])
        else:
            # Если информации о последнем обновлении нет, показываем "Обновлено ранее"
            message += f"\n\n🔄 Обновлено ранее"
            
            # Добавляем обычную кнопку обновления
            refresh_text = "🔄 Обновить"
            refresh_callback = f"refresh_{date_str}"
            keyboard.append([InlineKeyboardButton(refresh_text, callback_data=refresh_callback)])
        
        # Добавляем кнопку "Назад в календарь"
        keyboard.append([InlineKeyboardButton("📅 Назад в календарь", callback_data=f"calendar_{year}_{month}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=message, 
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка при показе расписания на дату {date_str}: {e}")
        # В случае ошибки пытаемся отобразить сообщение об ошибке
        try:
            await query.edit_message_text(
                f"❌ Произошла ошибка при получении расписания на {date_str.replace('-', '.')}.\n\n"
                f"Ошибка: {str(e)[:100]}",
                parse_mode="Markdown"
            )
        except Exception:
            logger.error("Не удалось отобразить сообщение об ошибке")

# Загрузка информации о последних обновлениях
def load_last_update_times():
    global last_update_times
    try:
        if os.path.exists(LAST_UPDATE_FILE):
            with open(LAST_UPDATE_FILE, 'rb') as f:
                last_update_times = pickle.load(f)
                logger.info(f"Загружена информация о {len(last_update_times)} последних обновлениях")
    except Exception as e:
        logger.error(f"Ошибка при загрузке информации о последних обновлениях: {e}")
        last_update_times = {}

# Сохранение информации о последних обновлениях
def save_last_update_times():
    try:
        with open(LAST_UPDATE_FILE, 'wb') as f:
            pickle.dump(last_update_times, f)
        logger.info(f"Сохранена информация о {len(last_update_times)} последних обновлениях")
    except Exception as e:
        logger.error(f"Ошибка при сохранении информации о последних обновлениях: {e}")

# Загрузка информации о статусе домашних заданий
def load_hw_status():
    global hw_status_data
    try:
        if os.path.exists(HW_STATUS_FILE):
            with open(HW_STATUS_FILE, 'rb') as f:
                hw_status_data = pickle.load(f)
                logger.info(f"Загружена информация о статусе ДЗ для {len(hw_status_data)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка при загрузке информации о статусе ДЗ: {e}")
        hw_status_data = {}

# Сохранение информации о статусе домашних заданий
def save_hw_status():
    try:
        with open(HW_STATUS_FILE, 'wb') as f:
            pickle.dump(hw_status_data, f)
        logger.info(f"Сохранена информация о статусе ДЗ для {len(hw_status_data)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка при сохранении информации о статусе ДЗ: {e}")

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
    load_last_update_times()
    load_hw_status()
    
    # Создаем приложение
    application = Application.builder().token(token).build()
    
    # Добавляем обработчики команд
    # В боте доступны следующие команды:
    # /start - начало работы с ботом
    # /month - календарь на текущий месяц
    # /groups - настройка ежедневной отправки расписания
    application.add_handler(CommandHandler("start", start))
    # Удалены обработчики help_command, today_command, tomorrow_command, week_command
    application.add_handler(CommandHandler("month", month_command))
    
    # Удален обрабочик ввода даты (date_command)
    
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
    
    # Добавляем задачу для периодической очистки кэша (каждые 6 часов)
    job_queue.run_repeating(clean_cache, interval=21600, first=3600)
    
    # Регистрируем обработчик для корректного завершения работы
    import atexit
    atexit.register(shutdown)
    
    # Запускаем бота
    logger.info("Запускаем бота...")
    try:
        application.run_polling()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
    finally:
        # Убеждаемся, что все ресурсы освобождены при завершении
        shutdown()

if __name__ == "__main__":
    main() 