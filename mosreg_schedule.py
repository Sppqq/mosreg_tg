import requests
import json
from datetime import datetime
import os
from dotenv import load_dotenv
import time

# Загрузка переменных окружения
load_dotenv()

class MosregAPI:
    def __init__(self):
        self.base_url = "https://authedu.mosreg.ru"
        self.token = os.getenv("MOSREG_TOKEN")
        if not self.token:
            raise ValueError("Необходимо указать токен в переменной окружения MOSREG_TOKEN")
        
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # Добавляем задержку между запросами (1 секунда)
        self.request_delay = 1

    def get_schedule(self, date=None):
        """
        Получение расписания уроков на указанную дату
        :param date: Дата в формате DD-MM-YYYY (по умолчанию сегодня)
        :return: Список уроков
        """
        if date is None:
            date = datetime.now().strftime("%d-%m-%Y")

        url = f"{self.base_url}/diary/schedules/schedule/"
        params = {
            "date": date
        }

        try:
            # Добавляем задержку перед запросом
            time.sleep(self.request_delay)
            
            print(f"Отправка запроса на {url} с параметрами {params}")
            print(f"Заголовки: {self.headers}")
            
            response = requests.get(url, headers=self.headers, params=params)
            
            # Проверяем код ответа
            print(f"Получен ответ от API: {response.status_code}")
            
            # Печатаем содержимое ответа для отладки
            content_type = response.headers.get('Content-Type', '')
            print(f"Content-Type: {content_type}")
            
            # Выводим первые 500 символов ответа для анализа
            print(f"Ответ (первые 500 символов): {response.text[:500]}")
            
            # Пытаемся распарсить JSON только если сервер вернул успешный статус
            if response.status_code == 200:
                # Пытаемся парсить JSON только если контент не пустой
                if response.text.strip():
                    try:
                        schedule_data = response.json()
                        
                        # Вывод для отладки
                        print(f"Содержимое JSON: {json.dumps(schedule_data, ensure_ascii=False, indent=2)[:500]}...")
                        
                        # Обработка полученных данных
                        lessons = []
                        
                        # Проверяем формат ответа и адаптируем парсинг
                        if "schedule" in schedule_data:
                            # Старый формат
                            for lesson in schedule_data.get("schedule", []):
                                lesson_info = {
                                    "subject": lesson.get("subject", {}).get("name", "Не указано"),
                                    "start_time": lesson.get("startTime", "Не указано"),
                                    "end_time": lesson.get("endTime", "Не указано"),
                                    "room": lesson.get("room", "Не указано"),
                                    "teacher": lesson.get("teacher", {}).get("name", "Не указано")
                                }
                                lessons.append(lesson_info)
                        else:
                            # Новый формат (адаптируем в зависимости от структуры)
                            for lesson in schedule_data.get("lessons", []):
                                lesson_info = {
                                    "subject": lesson.get("subject", {}).get("name", "Не указано"),
                                    "start_time": lesson.get("startTime", "Не указано"),
                                    "end_time": lesson.get("endTime", "Не указано"),
                                    "room": lesson.get("room", "Не указано"),
                                    "teacher": lesson.get("teacher", {}).get("name", "Не указано")
                                }
                                lessons.append(lesson_info)
                        
                        return lessons
                    except json.JSONDecodeError as e:
                        print(f"Ошибка при парсинге JSON: {e}")
                        print(f"Содержимое ответа не является JSON: {response.text[:200]}")
                        return None
                else:
                    print("Сервер вернул пустой ответ")
                    return None
            else:
                response.raise_for_status()
                return None

        except requests.exceptions.RequestException as e:
            print(f"Ошибка при получении расписания: {e}")
            return None

def main():
    # Пример использования
    try:
        api = MosregAPI()
        
        # Получаем текущую дату в формате ДД-ММ-ГГГГ
        today = datetime.now().strftime("%d-%m-%Y")
        print(f"Запрашиваем расписание на {today}")
        
        # Получаем расписание на сегодня
        lessons = api.get_schedule()
        
        if lessons:
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
            print("Не удалось получить расписание")

    except Exception as e:
        print(f"Произошла ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main() 