import os
import json
import time
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FIREBASE_CREDS_STR = os.getenv("FIREBASE_CREDENTIALS")

# Словник для перекладу колонок (малі літери, як ти попросив)
COLUMN_MAPPING = {
    "left": "на публікацію",
    "center": "на тайп",
    "right": "на редактуру"
}

def send_telegram_notification(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Використовуємо parse_mode HTML для безпечних гіперпосилань
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Помилка відправки в TG: {response.text}", flush=True)
    except Exception as e:
        print(f"Помилка мережі TG: {e}", flush=True)

# Ініціалізація Firebase
try:
    creds_dict = json.loads(FIREBASE_CREDS_STR)
    cred = credentials.Certificate(creds_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase успішно ініціалізовано!", flush=True)
except Exception as e:
    print(f"КРИТИЧНА ПОМИЛКА ініціалізації Firebase: {e}", flush=True)

# Кеш для відстеження стану завдань
known_boards = {}
is_initial_load = True

def extract_tasks(doc_data):
    """Допоміжна функція для збору ID, title та url завдань із масивів"""
    tasks = {}
    for key, value in doc_data.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and 'id' in item:
                    tasks[item['id']] = {
                        'title': item.get('title', 'Без назви'),
                        'url': item.get('url', '#'),
                        'column': key
                    }
    return tasks

def on_snapshot(col_snapshot, changes, read_time):
    global known_boards, is_initial_load
    
    if is_initial_load:
        for doc in col_snapshot:
            known_boards[doc.id] = extract_tasks(doc.to_dict())
        print(f"Початкове завантаження завершено. Кешовано дошок: {len(known_boards)}.", flush=True)
        is_initial_load = False
        return

    for change in changes:
        board_id = change.document.id
        doc_data = change.document.to_dict()
        
        # Отримуємо назву тайтлу з поля 'name' документа
        board_name = doc_data.get('name', 'Невідомий тайтл')
        
        current_tasks = extract_tasks(doc_data)
        old_tasks = known_boards.get(board_id, {})

        if change.type.name in ['ADDED', 'MODIFIED']:
            for task_id, task_info in current_tasks.items():
                
                current_col_clean = COLUMN_MAPPING.get(task_info['column'], task_info['column'])

                if task_id not in old_tasks:
                    # ПОДІЯ: Створення нової таски
                    msg = (
                        f"🆕 <b>Нова задача на тайтлі:</b> \"{board_name}\"\n"
                        f"<b>Номер розділу:</b> {task_info['title']}\n"
                        f"<b>Призначення:</b> {current_col_clean}\n"
                        f"🔗 <a href=\"{task_info['url']}\">Перейти</a>"
                    )
                    send_telegram_notification(msg)
                
                elif old_tasks[task_id]['column'] != task_info['column']:
                    # ПОДІЯ: Перетягування таски в іншу колонку
                    old_col_clean = COLUMN_MAPPING.get(old_tasks[task_id]['column'], old_tasks[task_id]['column'])
                    
                    msg = (
                        f"🔄 <b>Завдання переміщено на тайтлі:</b> \"{board_name}\"\n"
                        f"<b>Номер розділу:</b> {task_info['title']}\n"
                        f"<b>Нове призначення:</b> {current_col_clean}\n"
                        f"<b>Було:</b> {old_col_clean}\n"
                        f"🔗 <a href=\"{task_info['url']}\">Перейти</a>"
                    )
                    send_telegram_notification(msg)
            
            # Оновлюємо локальний кеш стану
            known_boards[board_id] = current_tasks

collection_name = "boards"
collection_ref = db.collection(collection_name)
query_watch = collection_ref.on_snapshot(on_snapshot)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Скрипт зупинено вручну.", flush=True)
