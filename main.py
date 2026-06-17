import os
import json
import time
import firebase_admin
import telebot
from telebot import types
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FIREBASE_CREDS_STR = os.getenv("FIREBASE_CREDENTIALS")

# Ініціалізуємо Telegram-бота через telebot
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Словник для перекладу колонок
COLUMN_MAPPING = {
    "left": "на публікацію",
    "center": "на тайп",
    "right": "на редактуру"
}

# Глобальна змінна для зберігання поточного фільтру. За замовчуванням — 'all' (усі зміни)
# Можливі значення: 'all', 'left', 'center', 'right'
current_filter = "all"

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

# --- ХЕНДЛЕРИ TELEGRAM (Керування ботом) ---

def get_filter_keyboard():
    """Функція для створення кнопок вибору фільтру"""
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(text="✍️ На редактуру", callback_data="set_filter_right"))
    keyboard.add(types.InlineKeyboardButton(text="⌨️ На тайп", callback_data="set_filter_center"))
    keyboard.add(types.InlineKeyboardButton(text="📢 На публікацію", callback_data="set_filter_left"))
    keyboard.add(types.InlineKeyboardButton(text="🔄 Усі сповіщення", callback_data="set_filter_all"))
    return keyboard

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    """Реакція на команду /start або /menu"""
    bot.send_message(
        message.chat.id, 
        "👋 Вітаю! Оберіть, які саме сповіщення про завдання ви хочете отримувати:", 
        reply_markup=get_filter_keyboard()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('set_filter_'))
def handle_filter_buttons(call):
    """Обробка натискання на кнопки"""
    global current_filter
    
    # Витягуємо вибрану колонку з callback_data
    selected_mode = call.data.replace('set_filter_', '')
    current_filter = selected_mode
    
    names_ukr = {
        "all": "усі сповіщення",
        "right": "на редактуру",
        "center": "на тайп",
        "left": "на публікацію"
    }
    
    # Спливаюче сповіщення в телеграмі
    bot.answer_callback_query(call.id, text=f"Режим змінено: {names_ukr[selected_mode]}")
    
    # Редагуємо поточне повідомлення, щоб підтвердити вибір
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ <b>Налаштування збережено!</b>\n\nТепер ви отримуватимете сповіщення лише про завдання із призначенням: <b>{names_ukr[selected_mode]}</b>.\n\nЩоб змінити вибір, введіть /menu",
        parse_mode="HTML",
        reply_markup=get_filter_keyboard()
    )

# --- СЛУХАЧ FIRESTORE (Фоновий процес) ---

def on_snapshot(col_snapshot, changes, read_time):
    global known_boards, is_initial_load, current_filter
    
    if is_initial_load:
        for doc in col_snapshot:
            known_boards[doc.id] = extract_tasks(doc.to_dict())
        print(f"Початкове завантаження завершено. Кешовано дошок: {len(known_boards)}.", flush=True)
        is_initial_load = False
        return

    for change in changes:
        board_id = change.document.id
        doc_data = change.document.to_dict()
        board_name = doc_data.get('name', 'Невідомий тайтл')
        
        current_tasks = extract_tasks(doc_data)
        old_tasks = known_boards.get(board_id, {})

        if change.type.name in ['ADDED', 'MODIFIED']:
            for task_id, task_info in current_tasks.items():
                task_col = task_info['column']  # 'left', 'center' або 'right'
                
                # 🔥 ГОЛОВНА ФІЛЬТРАЦІЯ: Якщо вибрано конкретний фільтр і він не збігається з колонкою таски — пропускаємо її
                if current_filter != "all" and task_col != current_filter:
                    continue

                current_col_clean = COLUMN_MAPPING.get(task_col, task_col)

                if task_id not in old_tasks:
                    # Нова таска (відповідає нашому фільтру)
                    msg = (
                        f"🆕 <b>Нова задача на тайтлі:</b> \"{board_name}\"\n"
                        f"<b>Номер розділу:</b> {task_info['title']}\n"
                        f"<b>Призначення:</b> {current_col_clean}\n"
                        f"🔗 <a href=\"{task_info['url']}\">Перейти</a>"
                    )
                    bot.send_message(CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
                
                elif old_tasks[task_id]['column'] != task_col:
                    # Таску перемістили в колонку, яка відповідає нашому фільтру
                    old_col_clean = COLUMN_MAPPING.get(old_tasks[task_id]['column'], old_tasks[task_id]['column'])
                    
                    msg = (
                        f"🔄 <b>Завдання переміщено на тайтлі:</b> \"{board_name}\"\n"
                        f"<b>Номер розділу:</b> {task_info['title']}\n"
                        f"<b>Нове призначення:</b> {current_col_clean}\n"
                        f"<b>Було:</b> {old_col_clean}\n"
                        f"🔗 <a href=\"{task_info['url']}\">Перейти</a>"
                    )
                    bot.send_message(CHAT_ID, msg, parse_mode="HTML", disable_web_page_preview=True)
            
            known_boards[board_id] = current_tasks

# Запуск слухача бази даних (він працює в окремому фоновому потоці автоматично)
collection_name = "boards"
collection_ref = db.collection(collection_name)
query_watch = collection_ref.on_snapshot(on_snapshot)

# Запуск циклу бота для обробки кнопок (блокує головний потік, що утримує Railway від закриття)
print("Бот готовий до роботи та очікує на команди...", flush=True)
bot.infinity_polling()
