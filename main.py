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

def send_telegram_notification(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
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

# Кеш для відстеження стану завдань на дошках: { board_id: { task_id: {title, column} } }
known_boards = {}
is_initial_load = True

def extract_tasks(doc_data):
    """Допоміжна функція для збору всіх тасок з усіх масивів-колонок документа"""
    tasks = {}
    for key, value in doc_data.items():
        # Шукаємо поля, які є масивами (left, center, right тощо)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and 'id' in item:
                    tasks[item['id']] = {
                        'title': item.get('title', 'Без назви'),
                        'column': key
                    }
    return tasks

def on_snapshot(col_snapshot, changes, read_time):
    global known_boards, is_initial_load
    
    # Перший запуск: просто записуємо поточний стан усіх дощок у кеш, щоб не спамити старими тасками
    if is_initial_load:
        for doc in col_snapshot:
            known_boards[doc.id] = extract_tasks(doc.to_dict())
        print(f"Початкове завантаження завершено. Кешовано дошок: {len(known_boards)}. Слухаю зміни...", flush=True)
        is_initial_load = False
        return

    for change in changes:
        board_id = change.document.id
        doc_data = change.document.to_dict()
        
        # Витягуємо таски, які є на дошці прямо зараз
        current_tasks = extract_tasks(doc_data)
        # Отримуємо таски, які були на цій дошці до цієї зміни
        old_tasks = known_boards.get(board_id, {})

        if change.type.name in ['ADDED', 'MODIFIED']:
            # 1. Перевіряємо на наявність НОВИХ завдань або ПЕРЕМІЩЕНИХ
            for task_id, task_info in current_tasks.items():
                if task_id not in old_tasks:
                    # Цього ID взагалі не було в базі -> це нове завдання
                    msg = f"🆕 *Додано нове завдання!*\n\n📌 *Назва:* {task_info['title']}\n📋 *Колонка:* `{task_info['column']}`"
                    send_telegram_notification(msg)
                
                elif old_tasks[task_id]['column'] != task_info['column']:
                    # ID існував, але поле колонки змінилося -> таску перетягнули
                    msg = f"🔄 *Завдання переміщено!*\n\n📌 *Назва:* {task_info['title']}\n📥 *Нова колонка:* `{task_info['column']}`\n📤 *Було в:* `{old_tasks[task_id]['column']}`"
                    send_telegram_notification(msg)
            
            # Оновлюємо кеш цієї дошки для наступних перевірок
            known_boards[board_id] = current_tasks

# Слухаємо правильну колекцію з твого скріншоту
collection_name = "boards"
collection_ref = db.collection(collection_name)
query_watch = collection_ref.on_snapshot(on_snapshot)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Скрипт зупинено вручну.", flush=True)
