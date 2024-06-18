import telebot
from telebot import types
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
import logging
import pytz
import sqlite3
import time
from datetime import datetime, timedelta

# Токен Telegram бота
TOKEN = '6885850558:AAG0P1jhG9FLqoKc1tI-3NJLMIitxuN0rm8'
bot = telebot.TeleBot(TOKEN)

perm_tz = pytz.timezone('Asia/Yekaterinburg')

def init_db():
    conn = sqlite3.connect('tasks.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tasks
                (chat_id INTEGER, task_id INTEGER, time TEXT, text TEXT, PRIMARY KEY (chat_id, task_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS notes
                (chat_id INTEGER, note_id INTEGER, text TEXT, PRIMARY KEY (chat_id, note_id))''')
    conn.commit()
    conn.close()

def add_task(chat_id, task_id, time, text):
    try:
        conn = sqlite3.connect('tasks.db')
        c = conn.cursor()
        c.execute("INSERT INTO tasks (chat_id, task_id, time, text) VALUES (?, ?, ?, ?)", (chat_id, task_id, time, text))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка при добавлении задачи в базу данных: {e}")
    finally:
        conn.close()

def update_task(chat_id, task_id, time=None, text=None):
    try:
        conn = sqlite3.connect('tasks.db')
        c = conn.cursor()
        if time and text:
            c.execute("UPDATE tasks SET time = ?, text = ? WHERE chat_id = ? AND task_id = ?", (time, text, chat_id, task_id))
        elif time:
            c.execute("UPDATE tasks SET time = ? WHERE chat_id = ? AND task_id = ?", (time, chat_id, task_id))
        elif text:
            c.execute("UPDATE tasks SET text = ? WHERE chat_id = ? AND task_id = ?", (text, chat_id, task_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка при обновлении задачи в базе данных: {e}")
    finally:
        conn.close()

def get_tasks(chat_id):
    try:
        conn = sqlite3.connect('tasks.db')
        c = conn.cursor()
        c.execute("SELECT task_id, time, text FROM tasks WHERE chat_id = ?", (chat_id,))
        tasks = c.fetchall()
        return tasks
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении задач из базы данных: {e}")
        return []
    finally:
        conn.close()

def delete_task(chat_id, task_id):
    try:
        conn = sqlite3.connect('tasks.db')
        c = conn.cursor()
        c.execute("DELETE FROM tasks WHERE chat_id = ? AND task_id = ?", (chat_id, task_id))
        conn.commit()
        
        # Удаление задачи из планировщика
        if chat_id in tasks:
            task = next((task for task in tasks[chat_id] if task['id'] == task_id), None)
            if task and 'job_id' in task:
                scheduler.remove_job(task['job_id'])
        
        logger.info(f"Задача {task_id} удалена для chat_id {chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при удалении задачи из базы данных: {e}")
    except Exception as e:
        logger.error(f"Ошибка при удалении задачи из планировщика: {e}")
    finally:
        conn.close()

# Методы для работы с заметками
def add_note(chat_id, note_id, text):
    try:
        conn = sqlite3.connect('tasks.db')
        c = conn.cursor()
        c.execute("INSERT INTO notes (chat_id, note_id, text) VALUES (?, ?, ?)", (chat_id, note_id, text))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка при добавлении заметки в базу данных: {e}")
    finally:
        conn.close()

def update_note(chat_id, note_id, text):
    try:
        conn = sqlite3.connect('tasks.db')
        c = conn.cursor()
        c.execute("UPDATE notes SET text = ? WHERE chat_id = ? AND note_id = ?", (text, chat_id, note_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Ошибка при обновлении заметки в базе данных: {e}")
    finally:
        conn.close()

def get_notes(chat_id):
    try:
        conn = sqlite3.connect('tasks.db')
        c = conn.cursor()
        c.execute("SELECT note_id, text FROM notes WHERE chat_id = ?", (chat_id,))
        notes = c.fetchall()
        return notes
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении заметок из базы данных: {e}")
        return []
    finally:
        conn.close()

def delete_note(chat_id, note_id):
    try:
        conn = sqlite3.connect('tasks.db')
        c = conn.cursor()
        c.execute("DELETE FROM notes WHERE chat_id = ? AND note_id = ?", (chat_id, note_id))
        conn.commit()
        logger.info(f"Заметка {note_id} удалена для chat_id {chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при удалении заметки из базы данных: {e}")
    finally:
        conn.close()

# Настройки для планировщика задач
executors = {
    'default': ThreadPoolExecutor(10),
    'processpool': ProcessPoolExecutor(5)
}

job_defaults = {
    'coalesce': False,
    'max_instances': 3
}

# Инициализация планировщика с заданными настройками
scheduler = BackgroundScheduler(executors=executors, job_defaults=job_defaults, timezone=perm_tz)
scheduler.start()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Словари для хранения состояний диалогов и задач пользователей
user_states = {}
tasks = {}  # Словарь для хранения задач по chat_id

# Константы состояний для управления потоком диалога
STATE_AWAITING_TASK = 1
STATE_AWAITING_TIME = 2
STATE_EDITING_TASK = 4
STATE_EDITING_TASK_TEXT = 5
STATE_EDITING_TASK_TIME = 6
STATE_CHOOSING_EDIT_ACTION = 7
STATE_CONFIRM_DELETE = 8
STATE_POSTPONING_TASK = 9
STATE_AWAITING_POSTPONE_TIME = 10
STATE_AWAITING_NOTE = 11
STATE_EDITING_NOTE = 12
STATE_CONFIRM_DELETE_NOTE = 13
STATE_CHOOSING_NOTE_EDIT_ACTION = 14

def sync_tasks_with_db():
    try:
        conn = sqlite3.connect('tasks.db')
        c = conn.cursor()
        c.execute("SELECT chat_id, task_id, time, text FROM tasks")
        db_tasks = c.fetchall()
        conn.close()
        current_time = datetime.now(perm_tz)
        for chat_id, task_id, time, text in db_tasks:
            reminder_time = perm_tz.localize(datetime.strptime(time, '%Y-%m-%d %H:%M:%S'))
            if reminder_time > current_time:
                if chat_id not in tasks:
                    tasks[chat_id] = []
                tasks[chat_id].append({'id': task_id, 'time': reminder_time, 'text': text})
                try:
                    job = scheduler.add_job(send_reminder, 'date', run_date=reminder_time, args=[chat_id, text, task_id])
                    tasks[chat_id][-1]['job_id'] = job.id
                except Exception as e:
                    logger.error(f"Ошибка при добавлении задачи в планировщик при синхронизации: {e}")
            else:
                delete_task(chat_id, task_id)  # Удаление задач с истекшим временем
                logger.info(f"Удалена задача с истекшим временем: chat_id={chat_id}, task_id={task_id}, time={time}, text={text}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при синхронизации задач с базой данных: {e}")

def schedule_reminder(reminder_time, chat_id, task_text):
    task_id = int(time.time())
    if chat_id not in tasks:
        tasks[chat_id] = []
    tasks[chat_id].append({'id': task_id, 'time': reminder_time, 'text': task_text})
    
    add_task(chat_id, task_id, reminder_time.strftime('%Y-%m-%d %H:%M:%S'), task_text)

    try:
        job = scheduler.add_job(send_reminder, 'date', run_date=reminder_time, args=[chat_id, task_text, task_id])
        tasks[chat_id][-1]['job_id'] = job.id  # Сохраняем ID задания в задаче
        logger.info(f"Запланированное напоминание: {task_text} на {reminder_time} для chat_id {chat_id}, job id {job.id}")
    except Exception as e:
        logger.error(f"Ошибка при планировании напоминания: {e}")

def update_scheduled_task(chat_id, task_index, new_time=None, new_text=None):
    task = tasks[chat_id][task_index]
    if new_time:
        task['time'] = new_time
        update_task(chat_id, task['id'], time=new_time.strftime('%Y-%m-%d %H:%M:%S'))
    if new_text:
        task['text'] = new_text
        update_task(chat_id, task['id'], text=new_text)
    if 'job_id' in task:
        try:
            scheduler.remove_job(task['job_id'])
        except Exception as e:
            logger.error(f"Ошибка при удалении задачи из планировщика: {e}")
    try:
        job = scheduler.add_job(send_reminder, 'date', run_date=task['time'], args=[chat_id, task['text'], task['id']])
        task['job_id'] = job.id
        logger.info(f"Задача {task['id']} перепланирована на {task['time']}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении задачи в планировщик: {e}")

def send_reminder(chat_id, text, task_id):
    try:
        bot.send_message(chat_id, f"🔔 Напоминание: {text}")
        logger.info(f"Напоминание отправлено в чат {chat_id}")

        # Удаление задачи из списка задач
        if chat_id in tasks:
            tasks[chat_id] = [task for task in tasks[chat_id] if task['id'] != task_id]
        
        # Удаление задачи из базы данных
        delete_task(chat_id, task_id)
    except Exception as e:
        logger.error(f"Ошибка при отправке напоминания: {e}")

@bot.message_handler(commands=['start'])
def handle_start_command(message):
    chat_id = message.chat.id
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add('/ntask', '/tasks', '/edit', '/delete', '/postpone', '/nnote', '/notes', '/editnote', '/deletenote')
    bot.send_message(chat_id, 
                     "*Добро пожаловать!* \n\nВыберите команду:\nntask - создать задачу и напоминание \ntasks - посмотреть текущие задачи \nedit - редактировать текущие задачи \npostpone - отложить задачу \nnnote - создать заметку \nnotes - посмотреть текущие заметки \neditnote - редактировать текущие заметки",
                      reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['tasks'])
def show_tasks(message):
    chat_id = message.chat.id
    user_tasks = get_tasks(chat_id)
    if user_tasks:
        tasks_message = "*Ваши текущие задачи:*\n\n"
        for i, (task_id, time, text) in enumerate(user_tasks, start=1):
            time_str = perm_tz.localize(datetime.strptime(time, '%Y-%m-%d %H:%M:%S')).strftime('%d-%m-%Y %H:%M:%S %Z')
            tasks_message += (f"*{i}. {text}*\n  _на {time_str}_\n")
        bot.send_message(chat_id, tasks_message, parse_mode='Markdown')
    else:
        bot.send_message(chat_id, "_У вас пока нет активных задач._", parse_mode='Markdown')

@bot.message_handler(commands=['ntask'])
def handle_new_task_command(message):
    chat_id = message.chat.id
    bot.send_message(chat_id, "Пожалуйста, введите задачу для напоминания.")
    user_states[chat_id] = {'state': STATE_AWAITING_TASK, 'task_planned': False}

@bot.message_handler(commands=['edit'])
def edit_task_command(message):
    chat_id = message.chat.id
    user_tasks = get_tasks(chat_id)
    if user_tasks:
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
        for i, (task_id, time, text) in enumerate(user_tasks, start=1):
            markup.add(types.KeyboardButton(f"Редактировать задачу {i}"))
            markup.add(types.KeyboardButton(f"Удалить задачу {i}"))
        bot.send_message(chat_id, "Выберите задачу для редактирования или удаления:", reply_markup=markup)
        user_states[chat_id] = {'state': STATE_EDITING_TASK}
    else:
        bot.send_message(chat_id, "_У вас нет активных задач для редактирования или удаления._", parse_mode='Markdown')

@bot.message_handler(commands=['delete'])
def delete_task_command(message):
    chat_id = message.chat.id
    user_tasks = get_tasks(chat_id)
    if user_tasks:
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
        for i, (task_id, time, text) in enumerate(user_tasks, start=1):
            markup.add(types.KeyboardButton(f"Удалить задачу {i}"))
        bot.send_message(chat_id, "Выберите задачу для удаления:", reply_markup=markup)
    else:
        bot.send_message(chat_id, "_У вас нет активных задач для удаления._", parse_mode='Markdown')

@bot.message_handler(commands=['postpone'])
def postpone_task_command(message):
    chat_id = message.chat.id
    user_tasks = get_tasks(chat_id)
    if user_tasks:
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
        for i, (task_id, time, text) in enumerate(user_tasks, start=1):
            markup.add(types.KeyboardButton(f"Отложить задачу {i}"))
        bot.send_message(chat_id, "Выберите задачу для отсрочки:", reply_markup=markup)
        user_states[chat_id] = {'state': STATE_POSTPONING_TASK}
    else:
        bot.send_message(chat_id, "_У вас нет активных задач для отсрочки._", parse_mode='Markdown')

@bot.message_handler(commands=['nnote'])
def handle_new_note_command(message):
    chat_id = message.chat.id
    bot.send_message(chat_id, "Пожалуйста, введите текст заметки.")
    user_states[chat_id] = {'state': STATE_AWAITING_NOTE}

@bot.message_handler(commands=['notes'])
def show_notes(message):
    chat_id = message.chat.id
    user_notes = get_notes(chat_id)
    if user_notes:
        notes_message = "*Ваши текущие заметки:*\n\n"
        for i, (note_id, text) in enumerate(user_notes, start=1):
            notes_message += (f"*{i}. {text}*\n")
        bot.send_message(chat_id, notes_message, parse_mode='Markdown')
    else:
        bot.send_message(chat_id, "_У вас пока нет активных заметок._", parse_mode='Markdown')

@bot.message_handler(commands=['editnote'])
def edit_note_command(message):
    chat_id = message.chat.id
    user_notes = get_notes(chat_id)
    if user_notes:
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
        for i, (note_id, text) in enumerate(user_notes, start=1):
            markup.add(types.KeyboardButton(f"Редактировать заметку {i}"))
            markup.add(types.KeyboardButton(f"Удалить заметку {i}"))
        bot.send_message(chat_id, "Выберите заметку для редактирования или удаления:", reply_markup=markup)
        user_states[chat_id] = {'state': STATE_EDITING_NOTE}
    else:
        bot.send_message(chat_id, "_У вас нет активных заметок для редактирования или удаления._", parse_mode='Markdown')

@bot.message_handler(func=lambda message: user_states.get(message.chat.id, {}).get('state') == STATE_AWAITING_NOTE)
def handle_new_note_text(message):
    chat_id = message.chat.id
    note_id = int(time.time())
    add_note(chat_id, note_id, message.text)
    bot.send_message(chat_id, "*Заметка успешно добавлена.*", parse_mode='Markdown')
    user_states.pop(chat_id, None)

@bot.message_handler(commands=['deletenote'])
def delete_note_command(message):
    chat_id = message.chat.id
    user_notes = get_notes(chat_id)
    if user_notes:
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
        for i, (note_id, text) in enumerate(user_notes, start=1):
            markup.add(types.KeyboardButton(f"Удалить заметку {i}"))
        bot.send_message(chat_id, "Выберите заметку для удаления:", reply_markup=markup)
    else:
        bot.send_message(chat_id, "_У вас нет активных заметок для удаления._", parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text.startswith("Удалить заметку"))
def confirm_delete_note(message):
    chat_id = message.chat.id
    note_number = int(message.text.split(" ")[-1]) - 1
    user_notes = get_notes(chat_id)
    if 0 <= note_number < len(user_notes):
        note_id = user_notes[note_number][0]
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
        markup.add(types.KeyboardButton("Да"), types.KeyboardButton("Нет"))
        bot.send_message(chat_id, f"*Вы уверены, что хотите удалить заметку {note_number + 1}?*", reply_markup=markup, parse_mode='Markdown')
        user_states[chat_id] = {'state': STATE_CONFIRM_DELETE_NOTE, 'note_id': note_id}
    else:
        bot.send_message(chat_id, "_Некорректный номер заметки. Пожалуйста, попробуйте снова._", parse_mode='Markdown')

@bot.message_handler(func=lambda message: user_states.get(message.chat.id, {}).get('state') == STATE_CONFIRM_DELETE_NOTE)
def handle_confirm_delete(message):
    chat_id = message.chat.id
    if message.text == "Да":
        note_id = user_states[chat_id]['note_id']
        # Удаляем заметку из базы данных
        delete_note(chat_id, note_id)
        bot.send_message(chat_id, "*Заметка успешно удалена.*", parse_mode='Markdown')
    else:
        bot.send_message(chat_id, "Удаление заметки отменено._", parse_mode='Markdown')
    user_states.pop(chat_id, None)

@bot.message_handler(func=lambda message: user_states.get(message.chat.id, {}).get('state') == STATE_EDITING_NOTE and 'note_id' in user_states[message.chat.id])
def handle_edit_note_text(message):
    chat_id = message.chat.id
    note_id = user_states[chat_id]['note_id']
    update_note(chat_id, note_id, message.text)
    bot.send_message(chat_id, "*Текст заметки успешно изменен.*", parse_mode='Markdown')
    user_states.pop(chat_id, None)

@bot.message_handler(func=lambda message: message.text.startswith("Редактировать заметку") or message.text.startswith("Удалить заметку"))
def handle_note_selection(message):
    chat_id = message.chat.id
    note_number = int(message.text.split(" ")[-1]) - 1
    user_notes = get_notes(chat_id)
    if 0 <= note_number < len(user_notes):
        note_id = user_notes[note_number][0]
        if message.text.startswith("Редактировать заметку"):
            bot.send_message(chat_id, "Введите новый текст заметки:")
            user_states[chat_id] = {'state': STATE_EDITING_NOTE, 'note_id': note_id}
        elif message.text.startswith("Удалить заметку"):
            markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
            markup.add(types.KeyboardButton("Да"), types.KeyboardButton("Нет"))
            bot.send_message(chat_id, f"*Вы уверены, что хотите удалить заметку {note_number + 1}?*", reply_markup=markup, parse_mode='Markdown')
            user_states[chat_id] = {'state': STATE_CONFIRM_DELETE_NOTE, 'note_id': note_id}
    else:
        bot.send_message(chat_id, "_Некорректный номер заметки. Пожалуйста, попробуйте снова._", parse_mode='Markdown')

@bot.message_handler(func=lambda message: user_states.get(message.chat.id, {}).get('state') == STATE_EDITING_NOTE and 'note_id' in user_states[message.chat.id])

@bot.message_handler(func=lambda message: user_states.get(message.chat.id, {}).get('state') == STATE_EDITING_NOTE and 'note_id' in user_states[message.chat.id])
def handle_edit_note_text(message):
    chat_id = message.chat.id
    note_id = user_states[chat_id]['note_id']
    update_note(chat_id, note_id, message.text)
    bot.send_message(chat_id, "*Текст заметки успешно изменен.*", parse_mode='Markdown')
    user_states.pop(chat_id, None)


@bot.message_handler(func=lambda message: message.text.startswith("Удалить задачу"))
def confirm_delete_task(message):
    chat_id = message.chat.id
    task_number = int(message.text.split(" ")[-1]) - 1
    user_tasks = get_tasks(chat_id)
    if 0 <= task_number < len(user_tasks):
        task_id = user_tasks[task_number][0]
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
        markup.add(types.KeyboardButton("Да"), types.KeyboardButton("Нет"))
        bot.send_message(chat_id, f"*Вы уверены, что хотите удалить задачу {task_number + 1}?*", reply_markup=markup, parse_mode='Markdown')
        user_states[chat_id] = {'state': STATE_CONFIRM_DELETE, 'task_id': task_id}
    else:
        bot.send_message(chat_id, "_Некорректный номер задачи. Пожалуйста, попробуйте снова._", parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text.startswith("Отложить задачу"))
def handle_postpone_task(message):
    chat_id = message.chat.id
    task_number = int(message.text.split(" ")[-1]) - 1
    user_tasks = get_tasks(chat_id)
    if 0 <= task_number < len(user_tasks):
        task_id = user_tasks[task_number][0]
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
        markup.add(types.KeyboardButton("Отложить на 5 минут"), types.KeyboardButton("Отложить на 10 минут"))
        markup.add(types.KeyboardButton("Отложить на 15 минут"), types.KeyboardButton("Отложить на 30 минут"))
        markup.add(types.KeyboardButton("Отложить на 1 час"), types.KeyboardButton("Отложить на 1 день"))
        bot.send_message(chat_id, f"Выберите время отсрочки для задачи {task_number + 1}:", reply_markup=markup)
        user_states[chat_id] = {'state': STATE_AWAITING_POSTPONE_TIME, 'task_id': task_id, 'task_number': task_number}
    else:
        bot.send_message(chat_id, "Некорректный номер задачи. Пожалуйста, попробуйте снова._", parse_mode='Markdown')

@bot.message_handler(func=lambda message: user_states.get(message.chat.id, {}).get('state') == STATE_CONFIRM_DELETE)
def handle_confirm_delete(message):
    chat_id = message.chat.id
    if message.text == "Да":
        task_id = user_states[chat_id]['task_id']
        # Удаляем задачу из базы данных и из планировщика
        delete_task(chat_id, task_id)
        if chat_id in tasks:
            tasks[chat_id] = [task for task in tasks[chat_id] if task['id'] != task_id]
            logger.info(f"Задача {task_id} успешно удалена для chat_id {chat_id}")
        bot.send_message(chat_id, "*Задача успешно удалена.*", parse_mode='Markdown')
    else:
        bot.send_message(chat_id, "Удаление задачи отменено._", parse_mode='Markdown')
    user_states.pop(chat_id, None)

@bot.message_handler(func=lambda message: user_states.get(message.chat.id, {}).get('state') == STATE_AWAITING_POSTPONE_TIME)
def handle_postpone_time_selection(message):
    chat_id = message.chat.id
    task_id = user_states[chat_id]['task_id']
    task_number = user_states[chat_id]['task_number']
    user_tasks = get_tasks(chat_id)
    original_time = perm_tz.localize(datetime.strptime(user_tasks[task_number][1], '%Y-%m-%d %H:%M:%S'))
    postpone_time = None

    if message.text == "Отложить на 5 минут":
        postpone_time = timedelta(minutes=5)
    elif message.text == "Отложить на 10 минут":
        postpone_time = timedelta(minutes=10)
    elif message.text == "Отложить на 15 минут":
        postpone_time = timedelta(minutes=15)
    elif message.text == "Отложить на 30 минут":
        postpone_time = timedelta(minutes=30)
    elif message.text == "Отложить на 1 час":
        postpone_time = timedelta(hours=1)
    elif message.text == "Отложить на 1 день":
        postpone_time = timedelta(days=1)
    else:
        bot.send_message(chat_id, "_Некорректный выбор времени отсрочки. Пожалуйста, попробуйте снова._", parse_mode='Markdown')
        return

    new_time = original_time + postpone_time
    update_scheduled_task(chat_id, task_number, new_time=new_time)
    bot.send_message(chat_id, f"Задача успешно отложена на {new_time.strftime('%d-%m-%Y %H:%M %Z')}", parse_mode='Markdown')
    logger.info(f"Задача {task_id} успешно отложена на {new_time}")
    reset_user_state(chat_id)

def send_time_options(chat_id):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True, selective=True)
    button_row1 = [types.KeyboardButton("Через 5 минут"), types.KeyboardButton("Через 15 минут"), types.KeyboardButton("Через 30 минут")]
    button_row2 = [types.KeyboardButton("Через 45 минут"), types.KeyboardButton("Через час"), types.KeyboardButton("Через день")]
    button_row3 = [types.KeyboardButton("Через неделю"), types.KeyboardButton("Введите дату вручную")]
    markup.row(*button_row1)
    markup.row(*button_row2)
    markup.row(*button_row3)
    bot.send_message(chat_id, "Выберите, когда напомнить:", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    chat_id = message.chat.id
    text = message.text
    user_state = user_states.get(chat_id, {})
    if text.startswith('/'):
        return

    if user_state.get('state') == STATE_AWAITING_TASK:
        user_states[chat_id] = {
            'state': STATE_AWAITING_TIME,
            'task': text,
            'task_planned': False,
            'awaiting_manual_time': False
        }
        send_time_options(chat_id)

    elif user_state.get('state') == STATE_EDITING_TASK_TIME:
        if text in ["Через 5 минут", "Через 15 минут", "Через 30 минут", "Через 45 минут", "Через час", "Через день", "Через неделю"]:
            process_time_selection(message, chat_id, is_editing=True)
        elif text == "Введите дату вручную":
            bot.send_message(chat_id, "Введите дату и время в формате ДД ММ ГГ ЧЧ:ММ. Например: 09 02 24 10 18")
        else:
            reminder_time, error_message = parse_custom_time(text)
            if reminder_time:
                task_index = user_state['editing_task_index']
                update_scheduled_task(chat_id, task_index, new_time=reminder_time)
                bot.send_message(chat_id, "*Время задачи успешно изменено.*", parse_mode='Markdown')
                reset_user_state(chat_id)
            else:
                bot.send_message(chat_id, error_message or "_Произошла ошибка при изменении времени задачи._", parse_mode='Markdown')
        return

    elif user_state.get('state') == STATE_AWAITING_TIME:
        if text == "Введите дату вручную":
            user_states[chat_id]['awaiting_manual_time'] = True
            bot.send_message(chat_id, "Введите дату и время в формате ДД ММ ГГ ЧЧ:ММ. Например: 09 02 24 10 18")
        elif user_state.get('awaiting_manual_time'):
            reminder_time, error_message = parse_custom_time(text)
            if reminder_time:
                schedule_reminder(reminder_time, chat_id, user_states[chat_id]['task'])
                bot.send_message(chat_id, f"Напоминание установлено на {reminder_time.strftime('%d-%m-%Y %H:%M')} (Yekaterinburg Time)", parse_mode='Markdown')
                reset_user_state(chat_id)
            else:
                bot.send_message(chat_id, error_message)
        else:
            process_time_selection(message, chat_id, is_editing=False)

    elif user_state.get('state') == STATE_EDITING_TASK:
        if text.startswith("Редактировать задачу "):
            try:
                task_number = int(text.split(" ")[-1]) - 1
                user_tasks = get_tasks(chat_id)
                if 0 <= task_number < len(user_tasks):
                    user_states[chat_id] = {'state': STATE_CHOOSING_EDIT_ACTION, 'editing_task_index': task_number}
                    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
                    markup.add("Изменить текст", "Изменить время")
                    bot.send_message(chat_id, "Что вы хотите отредактировать?", reply_markup=markup, parse_mode='Markdown')
                else:
                    bot.send_message(chat_id, "Некорректный выбор задачи. Пожалуйста, попробуйте снова._", parse_mode='Markdown')
            except ValueError:
                bot.send_message(chat_id, "Пожалуйста, введите номер задачи для редактирования._", parse_mode='Markdown')

    elif user_state.get('state') == STATE_CHOOSING_EDIT_ACTION:
        task_index = user_state['editing_task_index']
        if text == "Изменить текст":
            user_states[chat_id] = {'state': STATE_EDITING_TASK_TEXT, 'editing_task_index': task_index}
            bot.send_message(chat_id, "Введите новый текст задачи:")
        elif text == "Изменить время":
            user_states[chat_id] = {'state': STATE_EDITING_TASK_TIME, 'editing_task_index': task_index}
            send_time_options(chat_id)

    elif user_state.get('state') == STATE_EDITING_TASK_TEXT:
        task_index = user_state['editing_task_index']
        update_scheduled_task(chat_id, task_index, new_text=text)
        bot.send_message(chat_id, "*Текст задачи успешно изменен.*", parse_mode='Markdown')
        reset_user_state(chat_id)

    else:
        bot.send_message(chat_id, "*Чтобы начать, пожалуйста, используйте команду /ntask.*", parse_mode='Markdown')

def reset_user_state(chat_id):
    user_states[chat_id] = {'state': 0, 'task_planned': False, 'awaiting_manual_time': False}

def parse_custom_time(text):
    now = datetime.now(perm_tz)
    try:
        if "завтра" in text.lower():
            parts = text.lower().replace("завтра", "").strip().split(' ')
            if len(parts) == 2:
                hour, minute = parts
                reminder_time = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0) + timedelta(days=1)
            else:
                return None, ("Не удалось распознать время. Пожалуйста, используйте формат.")
        else:
            parts = text.split(' ')
            if len(parts) == 5:
                day, month, year, hour, minute = parts
                full_year = int('20' + year)
                reminder_time = datetime(year=full_year, month=int(month), day=int(day), hour=int(hour), minute=int(minute))
                reminder_time = perm_tz.localize(reminder_time)
            else:
                return None, ("Не удалось распознать время. Пожалуйста, используйте формат 'ДД ММ ГГ ЧЧ ММ'.")
        if reminder_time <= now:
            return None, ("Указанное время уже прошло. Пожалуйста, укажите будущее время.")
        return reminder_time, None
    except ValueError as е:
        return None, (f"Ошибка при разборе времени: {е}. Пожалуйста, используйте формат 'ЧЧ ММ завтра' или 'ДД ММ ГГ ЧЧ ММ'.")

def process_time_selection(message, chat_id, is_editing=False):
    text = message.text
    now = datetime.now(perm_tz)
    reminder_time = None
    if text == "Через 5 минут":
        reminder_time = now + timedelta(minutes=5)
    elif text == "Через 15 минут":
        reminder_time = now + timedelta(minutes=15)
    elif text == "Через 30 минут":
        reminder_time = now + timedelta(minutes=30)
    elif text == "Через 45 минут":
        reminder_time = now + timedelta(minutes=45)
    elif text == "Через час":
        reminder_time = now + timedelta(hours=1)
    elif text == "Через день":
        reminder_time = now + timedelta(days=1)
    elif text == "Через неделю":
        reminder_time = now + timedelta(weeks=1)
    else:
        bot.send_message(chat_id, "Пожалуйста, используйте кнопки для выбора времени напоминания или введите дату вручную.")
        return

    if reminder_time:
        if is_editing:
            task_index = user_states[chat_id]['editing_task_index']
            update_scheduled_task(chat_id, task_index, new_time=reminder_time)
            bot.send_message(chat_id, f"*Время задачи успешно изменено на {reminder_time.strftime('%d-%m-%Y %H:%M')} (Yekaterinburg Time)*", parse_mode='Markdown')
        else:
            schedule_reminder(reminder_time, chat_id, user_states[chat_id]['task'])
            bot.send_message(chat_id, f"*Напоминание установлено на {reminder_time.strftime('%d-%m-%Y %H:%M')} (Yekaterinburg Time)*", parse_mode='Markdown')
        reset_user_state(chat_id)

if __name__ == "__main__":
    init_db()
    sync_tasks_with_db()
    bot.remove_webhook()
    time.sleep(1)
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        logger.error(f"Ошибка при выполнении bot.polling: {e}")
