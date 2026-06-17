import os
import json
import time
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# Локально завантажуємо змінні з .env (на Railway вони підтягнуться автоматично)
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FIREBASE_CREDS_STR = os.getenv("FIREBASE_CREDENTIALS")

def send_telegram_notification(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Помилка відправки в TG: {response.text}")
    except Exception as e:
        print(f"Помилка мережі: {e}")

# Ініціалізація Firebase через рядок JSON зі змінних оточення
creds_dict = json.loads(FIREBASE_CREDS_STR)
cred = credentials.Certificate(creds_dict)
firebase_admin.initialize_app(cred)

db = firestore.client()

# Флаг для ігнорування початкового дампу бази даних при старті скрипта
is_initial_load = True

def on_snapshot(col_snapshot, changes, read_time):
    global is_initial_load
    
    if is_initial_load:
        print("Початкове завантаження бази успішне. Слухаю нові зміни...")
        is_initial_load = False
        return

    for change in changes:
        doc_data = change.document.to_dict()
        task_title = doc_data.get('title', 'Без назви')
        task_status = doc_data.get('status', 'Не вказано') # або поле, де зберігається назва колонки

        if change.type.name == 'ADDED':
            msg = f"🆕 *Додано нове завдання!*\n\n📌 *Назва:* {task_title}\n📋 *Статус:* {task_status}"
            send_telegram_notification(msg)
            
        elif change.type.name == 'MODIFIED':
            msg = f"🔄 *Завдання змінено!*\n\n📌 *Назва:* {task_title}\n📋 *Новий статус/поле:* {task_status}"
            send_telegram_notification(msg)

# Вкажи точну назву своєї колекції у Firestore замість "tasks"
collection_ref = db.collection("tasks")
query_watch = collection_ref.on_snapshot(on_snapshot)

# Утримуємо головний потік живим на сервері
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Скрипт зупинено.")
