import requests
import re
import os
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import time

# Загрузка переменных окружения
load_dotenv()

def analyze_mosh_site():
    """
    Функция для анализа сайта МЭШ и определения правильных эндпоинтов API
    """
    token = os.getenv("MOSREG_TOKEN")
    if not token:
        print("ОШИБКА: Токен не найден. Укажите MOSREG_TOKEN в файле .env")
        return

    # Заголовки для имитации браузера
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru,en-US;q=0.7,en;q=0.3",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0"
    }

    # 1. Посещаем главную страницу дневника
    print("\n--- Шаг 1: Посещаем главную страницу дневника ---")
    main_url = "https://authedu.mosreg.ru/diary"
    try:
        response = requests.get(main_url, headers=headers)
        print(f"Статус: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type')}")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            print(f"Заголовок страницы: {soup.title.text if soup.title else 'Не найден'}")
            
            # Ищем ссылки на API или скрипты
            scripts = soup.find_all('script', {'src': True})
            print(f"\nНайдено {len(scripts)} скриптов:")
            api_candidates = []
            for script in scripts:
                src = script['src']
                print(f"  - {src}")
                if 'api' in src or 'schedule' in src or 'diary' in src:
                    api_candidates.append(src)
            
            print(f"\nПотенциальные API эндпоинты: {api_candidates}")
            
            # Ищем XHR запросы внутри скриптов на странице
            inline_scripts = soup.find_all('script')
            xhr_patterns = [r'fetch\([\'"]([^\'"]+)[\'"]', r'\.get\([\'"]([^\'"]+)[\'"]', r'\.post\([\'"]([^\'"]+)[\'"]']
            api_urls = []
            
            for script in inline_scripts:
                if script.string:
                    for pattern in xhr_patterns:
                        matches = re.findall(pattern, script.string)
                        api_urls.extend(matches)
            
            if api_urls:
                print("\nНайденные URL запросов в скриптах:")
                for url in api_urls:
                    print(f"  - {url}")
            else:
                print("\nНе удалось найти URL запросов в скриптах")
    
    except Exception as e:
        print(f"Ошибка при доступе к главной странице: {e}")

    # 2. Пробуем открыть страницу расписания
    print("\n--- Шаг 2: Пробуем открыть страницу расписания ---")
    schedule_url = "https://authedu.mosreg.ru/diary/schedules"
    try:
        response = requests.get(schedule_url, headers=headers)
        print(f"Статус: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type')}")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            print(f"Заголовок страницы: {soup.title.text if soup.title else 'Не найден'}")
    except Exception as e:
        print(f"Ошибка при доступе к странице расписания: {e}")

    # 3. Открываем страницу расписания на конкретную дату
    print("\n--- Шаг 3: Открываем страницу расписания на конкретную дату ---")
    schedule_date_url = "https://authedu.mosreg.ru/diary/schedules/schedule/?date=14-03-2025"
    try:
        response = requests.get(schedule_date_url, headers=headers)
        print(f"Статус: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type')}")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            print(f"Заголовок страницы: {soup.title.text if soup.title else 'Не найден'}")
            
            # Ищем XHR запросы
            inline_scripts = soup.find_all('script')
            xhr_patterns = [r'fetch\([\'"]([^\'"]+)[\'"]', r'\.get\([\'"]([^\'"]+)[\'"]', r'\.post\([\'"]([^\'"]+)[\'"]']
            api_urls = []
            
            for script in inline_scripts:
                if script.string:
                    for pattern in xhr_patterns:
                        matches = re.findall(pattern, script.string)
                        api_urls.extend(matches)
            
            if api_urls:
                print("\nНайденные URL запросов в скриптах:")
                for url in api_urls:
                    print(f"  - {url}")
                    
                    # Проверяем найденные API
                    if 'schedule' in url:
                        print(f"\nПробуем запрос к найденному API: {url}")
                        api_url = url if url.startswith('http') else f"https://authedu.mosreg.ru{url}"
                        try:
                            api_response = requests.get(api_url, headers=headers)
                            print(f"Статус: {api_response.status_code}")
                            print(f"Content-Type: {api_response.headers.get('Content-Type')}")
                            print(f"Ответ: {api_response.text[:200]}...")
                        except Exception as e:
                            print(f"Ошибка при запросе к API: {e}")
            else:
                print("\nНе удалось найти URL запросов в скриптах")
                
    except Exception as e:
        print(f"Ошибка при доступе к странице расписания на дату: {e}")

if __name__ == "__main__":
    analyze_mosh_site() 