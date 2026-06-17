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
FIREBASE_CREDS_STR = os.getenv("FIREBASE_CREDENTIALS")

# 🔥 ОТРИМУЄМО СПИСОК ДОЗВОЛЕНИХ ID
# В Railway це буде рядок з ID через кому, наприклад: 12345678,98765432,11122233
allowed_users_raw = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = [int(uid.strip()) for uid in allowed_users_raw.split(",") if uid.strip()]

bot = telebot.TeleBot(TELEGRAM_TOKEN)

COLUMN_MAPPING = {
    "left": "на публікацію",
    "center": "на тайп",
    "right": "на редактуру"
}

# 🧠 Тепер це словник для зберігання фільтрів КОЖНОГО користувача окремо
# Структура: { user_id: "all" / "left" / "center" / "right" }
user_filters = {}

# Ініціалізація Firebase
try:
    creds_dict = json.loads(FIREBASE_CREDS_STR)
    cred = credentials.Certificate(creds_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase успішно ініціалізовано!", flush=True)
except Exception as e:
    print(f"КРИТИЧНА ПОМИЛКА ініціалізації Firebase: {e}", flush=True)

known_boards = {}
is_initial_load = True

def extract_tasks(doc_data):
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

# --- ДЕКОРАТОР ДЛЯ ПЕРЕВІРКИ ДОСТУПУ (WHITELIST) ---
def is_authorized(user_id):
    return user_id in ALLOWED_USERS

# --- ХЕНДЛЕРИ TELEGRAM ---

def get_filter_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(text="✍️ На редактуру", callback_data="set_filter_right"))
    keyboard.add(types.InlineKeyboardButton(text="⌨️ На тайп", callback_data="set_filter_center"))
    keyboard.add(types.InlineKeyboardButton(text="📢 На публікацію", callback_data="set_filter_left"))
    keyboard.add(types.InlineKeyboardButton(text="🔄 Усі сповіщення", callback_data="set_filter_all"))
    return keyboard

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    user_id = message.chat.id
    
    # 🛑 Перевірка на доступ
    if not is_authorized(user_id):
        bot.send_message(user_id, "🔒 <b>Доступ обмежено.</b> Ви не перебуваєте в списку дозволених користувачів.", parse_mode="HTML")
        print(f"Спроба доступу від заблокованого користувача ID: {user_id}", flush=True)
        return

    bot.send_message(
        user_id, 
        "👋 Вітаю! Оберіть, які саме сповіщення про завдання ви хочете отримувати:", 
        reply_markup=get_filter_keyboard()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('set_filter_'))
def handle_filter_buttons(call):
    user_id = call.message.chat.id
    
    if not is_authorized(user_id):
        bot.answer_callback_query(call.id, text="Доступ відхилено!", show_alert=True)
        return
        
    selected_mode = call.data.replace('set_filter_', '')
    
    # Зберігаємо вибір конкретно для цього user_id
    user_filters[user_id] = selected_mode
    
    names_ukr = {
        "all": "усі сповіщення",
        "right": "на редактуру",
        "center": "на тайп",
        "left": "на публікацію"
    }
    
    bot.answer_callback_query(call.id, text=f"Режим змінено: {names_ukr[selected_mode]}")
    
    bot.edit_message_text(
        chat_id=user_id,
        message_id=call.message.message_id,
        text=f"✅ <b>Налаштування збережено!</b>\n\nТепер ви отримуватимете сповіщення лише про завдання із призначенням: <b>{names_ukr[selected_mode]}</b>.\n\nЩоб змінити вибір, введіть /menu",
        parse_mode="HTML",
        reply_markup=get_filter_keyboard()
    )

# --- СЛУХАЧ FIRESTORE ---

def on_snapshot(col_snapshot, changes, read_time):
    global known_boards, is_initial_load, user_filters
    
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
                task_col = task_info['column']
                current_col_clean = COLUMN_MAPPING.get(task_col, task_col)

                # Шаблони повідомлень
                msg_new = (
                    f"🆕 <b>Нова задача на тайтлі:</b> \"{board_name}\"\n"
                    f"<b>Номер розділу:</b> {task_info['title']}\n"
                    f"<b>Призначення:</b> {current_col_clean}\n"
                    f"🔗 <a href=\"{task_info['url']}\">Перейти</a>"
                )
                
                msg_moved = None
                if task_id in old_tasks and old_tasks[task_id]['column'] != task_col:
                    old_col_clean = COLUMN_MAPPING.get(old_tasks[task_id]['column'], old_tasks[task_id]['column'])
                    msg_moved = (
                        f"🔄 <b>Завдання переміщено на тайтлі:</b> \"{board_name}\"\n"
                        f"<b>Номер розділу:</b> {task_info['title']}\n"
                        f"<b>Нове призначення:</b> {current_col_clean}\n"
                        f"<b>Було:</b> {old_col_clean}\n"
                        f"🔗 <a href=\"{task_info['url']}\">Перейти</a>"
                    )

                # 🔥 НАДСИЛАЄМО ПОВІДОМЛЕННЯ КОЖНОМУ ДОЗВОЛЕНОМУ КОРИСТУВАЧУ ОКРЕМО ЗГІДНО З ЙОГО ФІЛЬТРОМ
                for user_id in ALLOWED_USERS:
                    # Якщо користувач ще не вибрав фільтр, за замовчуванням ставимо 'all'
                    user_filter = user_filters.get(user_id, "all")
                    
                    # Перевіряємо фільтр користувача
                    if user_filter != "all" and task_col != user_filter:
                        continue  # Пропускаємо, якщо користувач не хоче бачити цю колонку

                    try:
                        if task_id not in old_tasks:
                            bot.send_message(user_id, msg_new, parse_mode="HTML", disable_web_page_preview=True)
                        elif msg_moved:
                            bot.send_message(user_id, msg_moved, parse_mode="HTML", disable_web_page_preview=True)
                    except Exception as send_error:
                        # Користувач міг заблокувати бота або ще не написав /start
                        print(f"Не вдалося надіслати повідомлення користувачу {user_id}: {send_error}", flush=True)
            
            known_boards[board_id] = current_tasks

collection_name = "boards"
collection_ref = db.collection(collection_name)
query_watch = collection_ref.on_snapshot(on_snapshot)

print("Бот готовий до роботи в багатокористувацькому режимі...", flush=True)
bot.infinity_polling()
