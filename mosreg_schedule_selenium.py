import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Загрузка переменных окружения
load_dotenv()

class MosregSchedule:
    def __init__(self, headless=False, cookies_file="cookies.json", browser=None):  # Добавлен параметр browser
        """
        Инициализация класса для получения расписания из МЭШ
        :param headless: Запуск браузера в фоновом режиме (без графического интерфейса)
        :param cookies_file: Путь к файлу с куками
        :param browser: Уже созданный экземпляр браузера Chrome
        """
        # Если браузер уже предоставлен, cookies_file может быть необязательным
        self.cookies_file = cookies_file
        
        if browser:
            # Используем уже созданный браузер
            print("Используем предоставленный экземпляр браузера")
            self.driver = browser
            
            # Проверяем, авторизован ли уже браузер, проверив URL 
            current_url = self.driver.current_url
            print(f"Текущий URL браузера: {current_url}")
            
            # Если авторизованы и уже находимся в расписании, пропускаем авторизацию
            if "school.mosreg.ru" in current_url:
                print("Браузер уже авторизован в системе, пропускаем авторизацию")
                return
        else:
            # Проверка наличия файла с куками
            if not os.path.exists(cookies_file):
                raise ValueError(f"Файл с куками '{cookies_file}' не найден")
                
            # Настройка опций Chrome
            chrome_options = Options()
            if headless:
                chrome_options.add_argument("--headless=new")  # Новый параметр для Chrome
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-notifications")
            chrome_options.add_argument("--disable-infobars")
            
            # Дополнительные заголовки
            chrome_options.add_argument(f"--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
            
            # Инициализация драйвера Chrome
            print("Запуск браузера Chrome...")
            self.driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=chrome_options
            )
            
            # Устанавливаем размер окна
            self.driver.set_window_size(1920, 1080)
        
        # Проверяем авторизацию только если не пропустили её выше
        if not (browser and "school.mosreg.ru" in self.driver.current_url):
            print("Загрузка куки и авторизация...")
            self.login_with_cookies()
    
    def login_with_cookies(self):
        """
        Авторизация с помощью куки из файла
        """
        try:
            # Сначала открываем главную страницу
            print("Загрузка домена для установки куки...")
            self.driver.get("https://authedu.mosreg.ru/")
            time.sleep(2)
            
            # Загружаем куки из файла
            print(f"Загрузка куки из файла {self.cookies_file}...")
            with open(self.cookies_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
                print(f"Загружено {len(cookies)} куки")
            
            # Устанавливаем куки
            for cookie in cookies:
                try:
                    # Удаляем лишние поля, которые могут вызвать ошибки
                    if 'sameSite' in cookie and cookie['sameSite'] == 'None':
                        cookie['sameSite'] = None
                    
                    # Пропускаем куки без домена
                    if 'domain' not in cookie:
                        continue
                    
                    # Для некоторых кук нужно изменить домен
                    if cookie.get('hostOnly', False) and cookie.get('domain', '').startswith('.'):
                        cookie['domain'] = cookie['domain'][1:]  # Удаляем точку в начале
                    
                    # Удаляем ненужные поля
                    for field in ['hostOnly', 'storeId', 'sameSite']:
                        if field in cookie:
                            del cookie[field]
                    
                    self.driver.add_cookie(cookie)
                    print(f"Добавлена кука: {cookie['name']}")
                except Exception as e:
                    print(f"Ошибка при добавлении куки {cookie.get('name')}: {e}")
            
            # Обновляем страницу после установки кук
            print("Обновление страницы после установки кук...")
            self.driver.refresh()
            time.sleep(3)
            
            # Сохраняем текущую страницу для отладки
            with open("after_login.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            print("Текущая страница сохранена в after_login.html")
            
            # Проверяем, успешно ли мы вошли в систему
            if "вход в систему" in self.driver.page_source.lower() or "авторизация" in self.driver.page_source.lower():
                print("ВНИМАНИЕ: Похоже, что вход в систему не выполнен!")
            else:
                print("Похоже, что вход в систему выполнен успешно")
            
        except Exception as e:
            print(f"Ошибка при входе в систему: {e}")
            import traceback
            traceback.print_exc()
            # Сохраняем текущую страницу для отладки
            with open("login_error.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            print("Страница с ошибкой сохранена в login_error.html")
    
    def get_schedule(self, date=None):
        """
        Получение расписания уроков на указанную дату
        :param date: Дата в формате DD-MM-YYYY (по умолчанию сегодня)
        :return: Список уроков или None если уроков нет или произошла ошибка
        """
        if date is None:
            date = datetime.now().strftime("%d-%m-%Y")
        
        # Открываем страницу расписания
        url = f"https://authedu.mosreg.ru/diary/schedules"
        print(f"Открываем страницу списка расписаний: {url}")
        
        try:
            self.driver.get(url)
            time.sleep(5)  # Даем больше времени для загрузки страницы
            
            # Сохраняем текущую страницу для отладки
            with open("schedules_page.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            print("Страница списка расписаний сохранена в schedules_page.html")
            
            # Теперь переходим на конкретную дату
            url = f"https://authedu.mosreg.ru/diary/schedules/schedule/?date={date}"
            print(f"Открываем страницу расписания на дату: {url}")
            
            self.driver.get(url)
            time.sleep(7)  # Даем еще больше времени для загрузки
            
            # Сохраняем текущую страницу для отладки
            with open("schedule_page.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            print("Страница расписания сохранена в schedule_page.html")
            
            # Проверяем наличие сообщения об отсутствии уроков
            try:
                no_lessons_texts = ["Уроков и мероприятий нет", "Уроков и мероприятий на этот день не найдено"]
                page_text = self.driver.find_element(By.TAG_NAME, "body").text
                
                for no_lesson_text in no_lessons_texts:
                    if no_lesson_text in page_text:
                        print(f"Найдено сообщение: '{no_lesson_text}'. На выбранную дату нет уроков.")
                        return []  # Возвращаем пустой список, если уроков нет
            except:
                pass
            
            # Пробуем найти заголовок страницы для проверки успешности загрузки
            try:
                title = self.driver.title
                print(f"Заголовок страницы: {title}")
            except:
                print("Не удалось получить заголовок страницы")
            
            # Используем точные XPath пути, предоставленные пользователем
            lesson_elements = []
            try:
                print("Ищем элементы урока по XPath пути...")
                # Путь до элемента урока
                lesson_elements = self.driver.find_elements(By.XPATH, 
                    "/html/body/div/div/main/div[2]/section/div/div/div/div[2]/div/div/div/div/div/a")
                
                if not lesson_elements:
                    print("Пробуем альтернативный XPath...")
                    lesson_elements = self.driver.find_elements(By.XPATH, 
                        "//div[contains(@class, 'lessons-list')]/div/div/div/a")
                
                if not lesson_elements:
                    print("Пробуем еще один XPath...")
                    lesson_elements = self.driver.find_elements(By.XPATH, 
                        "//a[contains(@href, '/diary/lesson')]")
                
                print(f"Найдено {len(lesson_elements)} элементов урока")
            except Exception as e:
                print(f"Ошибка при поиске элементов урока по XPath: {e}")
            
            # Если не нашли уроки через XPath, используем обычные CSS селекторы
            if not lesson_elements:
                print("Не удалось найти элементы урока по XPath, пробуем CSS селекторы...")
                selectors_to_try = [
                    ".student-diary-schedule", 
                    ".lessons-list", 
                    ".diary-day", 
                    ".diary-schedule", 
                    ".schedule", 
                    ".lesson-card",
                    "div[class*='lesson']",
                    "div[class*='schedule']",
                    ".timetable-container"
                ]
                
                for selector in selectors_to_try:
                    try:
                        print(f"Ищем элемент по селектору: {selector}")
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        if elements:
                            print(f"Найдено {len(elements)} элементов по селектору {selector}")
                            lesson_elements = elements
                            break
                    except Exception as e:
                        print(f"Ошибка при поиске элемента {selector}: {e}")
            
            if not lesson_elements:
                print("Не удалось найти элементы расписания")
                
                # Проверяем явно текст страницы на наличие информации об отсутствии уроков
                body_text = self.driver.find_element(By.TAG_NAME, "body").text
                print(f"Текст на странице: {body_text[:500]}...")
                
                # Ключевые фразы, указывающие на отсутствие уроков
                no_lessons_indicators = [
                    "уроков и мероприятий нет",
                    "уроков нет",
                    "нет уроков",
                    "не найдено",
                    "выходной"
                ]
                
                for indicator in no_lessons_indicators:
                    if indicator in body_text.lower():
                        print(f"Обнаружен индикатор отсутствия уроков: '{indicator}'")
                        return []  # Возвращаем пустой список, если уроков нет
                
                # Если не нашли явных указаний на отсутствие уроков, продолжаем поиск
            
            # Уроки, которые мы собрали
            lessons = []
            
            # Слова, которые указывают на то, что это не урок, а элемент интерфейса
            interface_elements = ["дневник", "библиотека", "портфолио", "справка", "учащийся", 
                                  "расписание", "задания", "оценки", "создать", 
                                  "учёба", "школа", "олимпиады", "версия", "написать нам"]
            
            # Если нашли элементы расписания через XPath
            if lesson_elements:
                print("Обрабатываем найденные элементы урока...")
                for i, elem in enumerate(lesson_elements):
                    try:
                        # Пытаемся получить информацию об уроке через XPath
                        subject = ""
                        try:
                            # Путь до названия урока
                            subject_elem = elem.find_element(By.XPATH, "./div[1]/h6")
                            subject = subject_elem.text.strip()
                        except:
                            try:
                                # Альтернативный путь или просто текст элемента
                                subject = elem.text.strip().split('\n')[0]
                            except:
                                print(f"Не удалось получить название предмета для элемента {i+1}")
                                continue
                        
                        print(f"Элемент {i+1}, предмет: {subject}")
                        
                        # Проверяем, не является ли это элемент интерфейса
                        if any(interface_word in subject.lower() for interface_word in interface_elements):
                            print(f"Элемент {i+1} похож на элемент интерфейса, пропускаем")
                            continue
                        
                        # Создаем информацию об уроке
                        lesson_info = {
                            "subject": subject,
                            "start_time": "Не указано",
                            "end_time": "Не указано",
                            "room": "Не указано",
                            "teacher": "Не указано",
                            "homework": "Не указано"
                        }
                        
                        # Пытаемся получить детали урока
                        lesson_text = elem.text.strip()
                        lines = lesson_text.split('\n')
                        
                        # Типичные фразы, указывающие на домашнее задание
                        homework_indicators = [
                            "дз:", "домашнее задание:", "задание:", "выполнить:", "учить", "прочитать", 
                            "выучить", "сделать", "подготовить", "параграф", "упражнение", "ex.", "exercise",
                            "activity", "student's book", "workbook", "п.", "стр.", "с.", "записать", "решить"
                        ]
                        
                        # Типичные указания на учителя
                        teacher_indicators = [
                            "преподаватель:", "учитель:", "внеурочная деятельность", "элективный курс"
                        ]
                        
                        for line in lines:
                            line = line.strip()
                            
                            # Пропускаем пустые строки и название предмета
                            if not line or line == subject:
                                continue
                            
                            # Время (содержит двоеточие и обычно короткая строка)
                            if ":" in line and len(line) < 20:
                                if "-" in line:
                                    parts = line.split("-")
                                    lesson_info["start_time"] = parts[0].strip()
                                    lesson_info["end_time"] = parts[1].strip()
                                else:
                                    lesson_info["start_time"] = line.strip()
                            
                            # Кабинет (обычно короткая строка с цифрами)
                            elif any(char.isdigit() for char in line) and len(line) < 15 and not ":" in line:
                                # Если в строке есть слово "Кабинет", извлекаем только номер
                                if "кабинет" in line.lower():
                                    lesson_info["room"] = line.split("кабинет", 1)[1].strip()
                                else:
                                    lesson_info["room"] = line.strip()
                            
                            # Определяем, является ли строка домашним заданием или информацией об учителе
                            elif len(line) > 3:
                                # Явные индикаторы домашнего задания
                                is_homework = any(hw_ind.lower() in line.lower() for hw_ind in homework_indicators)
                                # Явные индикаторы учителя
                                is_teacher = any(teacher_ind.lower() in line.lower() for teacher_ind in teacher_indicators)
                                
                                if is_homework or (len(line) > 30 and not is_teacher):
                                    # Если это явно домашнее задание или длинный текст (не учитель)
                                    # Очищаем от префиксов типа "ДЗ:", "Домашнее задание:" и т.д.
                                    homework = line
                                    for prefix in ["дз:", "домашнее задание:", "задание:"]:
                                        if homework.lower().startswith(prefix):
                                            homework = homework[len(prefix):].strip()
                                    
                                    lesson_info["homework"] = homework
                                elif is_teacher or (len(line) < 30 and not is_homework):
                                    # Если это явно учитель или короткий текст (не ДЗ)
                                    # Очищаем от префиксов типа "Учитель:", "Преподаватель:" и т.д.
                                    teacher = line
                                    for prefix in ["учитель:", "преподаватель:"]:
                                        if teacher.lower().startswith(prefix):
                                            teacher = teacher[len(prefix):].strip()
                                    
                                    lesson_info["teacher"] = teacher
                        
                        # Пытаемся получить домашнее задание через XPath
                        try:
                            homework_elem = elem.find_element(By.XPATH, "./div[1]/div[2]/div/div[2]/p")
                            homework = homework_elem.text.strip()
                            if homework:
                                lesson_info["homework"] = homework
                        except:
                            # Если не нашли через XPath, возможно уже нашли через текст
                            pass
                        
                        # Если имя учителя слишком длинное, возможно это домашнее задание
                        if len(lesson_info["teacher"]) > 50 and lesson_info["homework"] == "Не указано":
                            lesson_info["homework"] = lesson_info["teacher"]
                            lesson_info["teacher"] = "Не указано"
                        
                        # Проверяем, не являются ли детали элементами интерфейса
                        if any(interface_word in lesson_info["teacher"].lower() for interface_word in interface_elements):
                            if not any(teacher_ind in lesson_info["teacher"].lower() for teacher_ind in teacher_indicators):
                                lesson_info["teacher"] = "Не указано"
                        
                        if any(interface_word in lesson_info["room"].lower() for interface_word in interface_elements):
                            lesson_info["room"] = "Не указано"
                        
                        # Добавляем урок в список
                        lessons.append(lesson_info)
                        
                    except Exception as e:
                        print(f"Ошибка при обработке элемента {i+1}: {e}")
            
            # Если уроков не нашли, но явных признаков их отсутствия тоже нет
            if not lessons:
                print("Не найдено ни одного урока")
                return []
            
            # Уникализируем уроки (убираем дубликаты)
            unique_lessons = []
            subjects_seen = set()
            
            for lesson in lessons:
                subject = lesson["subject"]
                # Пропускаем элементы, которые выглядят как элементы интерфейса
                if any(interface_word in subject.lower() for interface_word in interface_elements):
                    continue
                    
                if subject not in subjects_seen:
                    subjects_seen.add(subject)
                    unique_lessons.append(lesson)
            
            print(f"Найдено {len(unique_lessons)} уникальных уроков")
            
            # Если после всех проверок не нашли уроков, значит их нет
            if not unique_lessons:
                print("После фильтрации не осталось уроков")
                return []
                
            return unique_lessons
            
        except Exception as e:
            print(f"Ошибка при получении расписания: {e}")
            import traceback
            traceback.print_exc()
            # Сохраняем HTML для отладки
            with open("error_page.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            print("HTML страницы с ошибкой сохранен в error_page.html")
            return None
    
    def close(self):
        """
        Закрытие браузера
        """
        if self.driver:
            self.driver.quit()
            print("Браузер закрыт")

def main():
    try:
        print("Инициализация браузера...")
        scheduler = MosregSchedule(headless=False)  # Видимый режим для отладки
        
        try:
            # Получаем текущую дату в формате ДД-ММ-ГГГГ
            today = datetime.now().strftime("%d-%m-%Y")
            print(f"Запрашиваем расписание на {today}")
            
            # Получаем расписание
            lessons = scheduler.get_schedule()
            
            if lessons and len(lessons) > 0:
                print(f"\nРасписание на {datetime.now().strftime('%d.%m.%Y')}:")
                print("-" * 50)
                for i, lesson in enumerate(lessons, 1):
                    print(f"\nУрок {i}:")
                    print(f"Предмет: {lesson['subject']}")
                    print(f"Время: {lesson['start_time']} - {lesson['end_time']}")
                    print(f"Кабинет: {lesson['room']}")
                    print(f"Учитель: {lesson['teacher']}")
                    print("-" * 50)
            else:
                print("Не удалось получить расписание или расписание пустое")
                print("Возможно, на указанную дату нет уроков или требуется другой способ доступа")
        finally:
            # Закрываем браузер в любом случае
            scheduler.close()
    
    except Exception as e:
        print(f"Произошла ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main() 