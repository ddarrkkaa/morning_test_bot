import os
import json
import calendar
from datetime import datetime, timedelta, date
import pytz
from telebot import TeleBot, types
from apscheduler.schedulers.background import BackgroundScheduler

# --- Configurations ---
TOKEN = os.getenv('TELEGRAM_TOKEN', '*')
DATA_FILE = 'data.json'
DEFAULT_REMINDER_TIME = {'hour': 20, 'minute': 0}
TIMEZONE = 'Europe/Kyiv'

# Initialize bot and scheduler
bot = TeleBot(TOKEN)
scheduler = BackgroundScheduler(timezone=pytz.timezone(TIMEZONE))
scheduler.start()

# Pending exchange requests
exchange_requests = {}

# --- Data Persistence ---
def load_data():
    if not os.path.exists(DATA_FILE):
        return {'users': {}, 'schedule_current': {}, 'schedule_next': {}}
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --- Constants ---
MONTHS_UK = [None, 'Січень', 'Лютий', 'Березень', 'Квітень', 'Травень', 'Червень',
             'Липень', 'Серпень', 'Вересень', 'Жовтень', 'Листопад', 'Грудень']
DAYS_UK = {
    'Monday': 'Понеділок', 'Tuesday': 'Вівторок', 'Wednesday': 'Середа',
    'Thursday': 'Четвер', "Friday": "П'ятниця", 'Saturday': 'Субота', 'Sunday': 'Неділя'
}

# --- Helper Functions ---
def build_main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    options = [
        'Зареєструватися', 'Генерація поточного місяця', 'Генерація наступного місяця',
        'Перегляд поточного місяця', 'Перегляд наступного місяця',
        'Відпустка', 'Змінити час нагадування', 'Помінятись', 'Скасувати'
    ]
    kb.add(*[types.KeyboardButton(opt) for opt in options])
    return kb


def generate_schedule(year, month, users):
    _, days = calendar.monthrange(year, month)
    dates = [datetime(year, month, d) for d in range(1, days + 1)]
    availability = {}
    for uid, info in users.items():
        excluded = set()
        for vac in info.get('vacation', []):
            start = datetime.fromisoformat(vac['from']).date()
            end = datetime.fromisoformat(vac['to']).date()
            for dt in dates:
                if start <= dt.date() <= end:
                    excluded.add(dt)
        availability[uid] = [dt for dt in dates if dt not in excluded]
    sched = {}
    uids = list(users.keys())
    idx = 0
    for dt in dates:
        for _ in uids:
            uid = uids[idx % len(uids)]; idx += 1
            if dt in availability.get(uid, []):
                sched[dt.date().isoformat()] = uid
                break
    return sched


def format_schedule(schedule, users, year, month):
    lines = [f"Розклад на {MONTHS_UK[month]} {year}\n"]
    for iso, uid in sorted(schedule.items()):
        dt = datetime.fromisoformat(iso)
        day = DAYS_UK[dt.strftime('%A')]
        date_str = dt.strftime('%d.%m')
        info = users.get(uid, {})
        lines.append(f"{day}, {date_str}: {info.get('emoji','')} {info.get('name','')}")
    return "\n".join(lines)


def schedule_reminders():
    data = load_data()
    sched = data.get('schedule_current', {})
    for job in scheduler.get_jobs():
        if job.id.startswith('reminder_'):
            scheduler.remove_job(job.id)
    for iso, uid in sched.items():
        rt = data['users'][uid].get('reminder_time', DEFAULT_REMINDER_TIME)
        def job(u=uid, d_iso=iso):
            tz = pytz.timezone(TIMEZONE)
            tomorrow = datetime.now(tz).date() + timedelta(days=1)
            if d_iso == tomorrow.isoformat():
                bot.send_message(int(u), f"Нагадування: завтра ({tomorrow.strftime('%d.%m')}) у вас чергування")
        scheduler.add_job(job, 'cron', hour=rt['hour'], minute=rt['minute'], id=f"reminder_{iso}")

# --- Bot Handlers ---
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    bot.send_message(msg.chat.id, "Привіт! Я бот для чергувань. Оберіть опцію меню.", reply_markup=build_main_menu())

@bot.message_handler(func=lambda m: m.text == 'Скасувати')
def cmd_cancel(msg):
    try:
        bot.clear_step_handler_by_chat_id(msg.chat.id)
    except:
        pass
    data = load_data()
    uid = str(msg.chat.id)
    data['users'].get(uid, {}).pop('vac_temp', None)
    data['users'].get(uid, {}).pop('ex_temp', None)
    save_data(data)
    bot.send_message(msg.chat.id, "Команду скасовано.", reply_markup=build_main_menu())

@bot.message_handler(func=lambda m: m.text == 'Зареєструватися')
def cmd_register(msg):
    sent = bot.send_message(msg.chat.id, "Введіть ваше ім'я:")
    bot.register_next_step_handler(sent, process_name)

def process_name(msg):
    if msg.text == 'Скасувати': return cmd_cancel(msg)
    data = load_data(); uid = str(msg.chat.id)
    data['users'].setdefault(uid, {})['name'] = msg.text.strip()
    save_data(data)
    sent = bot.send_message(msg.chat.id, "Введіть емоджі для чергування:")
    bot.register_next_step_handler(sent, process_emoji)

def process_emoji(msg):
    if msg.text == 'Скасувати': return cmd_cancel(msg)
    data = load_data(); uid = str(msg.chat.id)
    data['users'][uid]['emoji'] = msg.text.strip()
    data['users'][uid]['reminder_time'] = DEFAULT_REMINDER_TIME
    data['users'][uid].setdefault('vacation', [])
    save_data(data)
    bot.send_message(msg.chat.id, "Реєстрація завершена!", reply_markup=build_main_menu())

@bot.message_handler(func=lambda m: m.text in [
    'Генерація поточного місяця','Генерація наступного місяця',
    'Перегляд поточного місяця','Перегляд наступного місяця'
])
def cmd_schedule(msg):
    data = load_data(); now = datetime.now(pytz.timezone(TIMEZONE)); cmd = msg.text
    if 'Генерація' in cmd:
        if 'поточн' in cmd: year, month, key = now.year, now.month, 'schedule_current'
        else: nxt = now + timedelta(days=31); year, month, key = nxt.year, nxt.month, 'schedule_next'
        sched = generate_schedule(year, month, data['users'])
        data[key] = sched; save_data(data); schedule_reminders()
        bot.send_message(msg.chat.id, f"{cmd} виконано.", reply_markup=build_main_menu())
    else:
        if 'поточн' in cmd:
            sched = data.get('schedule_current', {}); year, month = now.year, now.month
        else:
            sched = data.get('schedule_next', {}); nxt = now + timedelta(days=31)
            year, month = nxt.year, nxt.month
        text = format_schedule(sched, data['users'], year, month) if sched else 'Розклад порожній.'
        bot.send_message(msg.chat.id, text, reply_markup=build_main_menu())

@bot.message_handler(func=lambda m: m.text == 'Відпустка')
def cmd_vacation(msg):
    sent = bot.send_message(msg.chat.id, "Вкажіть початок відпустки (YYYY-MM-DD):")
    bot.register_next_step_handler(sent, process_vac_start)

def process_vac_start(msg):
    if msg.text == 'Скасувати': return cmd_cancel(msg)
    try:
        start = datetime.fromisoformat(msg.text.strip()).date()
        data = load_data(); uid = str(msg.chat.id)
        data['users'][uid]['vac_temp'] = {'from': start.isoformat()}
        save_data(data)
        sent = bot.send_message(msg.chat.id, "Вкажіть кінець відпустки (YYYY-MM-DD):")
        bot.register_next_step_handler(sent, process_vac_end)
    except ValueError:
        sent = bot.send_message(msg.chat.id, "Невірний формат YYYY-MM-DD:")
        bot.register_next_step_handler(sent, process_vac_start)

def process_vac_end(msg):
    if msg.text == 'Скасувати': return cmd_cancel(msg)
    try:
        end = datetime.fromisoformat(msg.text.strip()).date()
        data = load_data(); uid = str(msg.chat.id)
        vac = data['users'][uid].pop('vac_temp')
        vac['to'] = end.isoformat()
        data['users'][uid].setdefault('vacation', []).append(vac)
        save_data(data)
        bot.send_message(msg.chat.id, "Період відпустки збережено.", reply_markup=build_main_menu())
    except ValueError:
        sent = bot.send_message(msg.chat.id, "Невірний формат YYYY-MM-DD:")
        bot.register_next_step_handler(sent, process_vac_end)

@bot.message_handler(func=lambda m: m.text == 'Змінити час нагадування')
def cmd_change(msg):
    sent = bot.send_message(msg.chat.id, "Введіть час нагадування ГГ:ХХ:")
    bot.register_next_step_handler(sent, process_reminder_time)

def process_reminder_time(msg):
    if msg.text == 'Скасувати': return cmd_cancel(msg)
    try:
        h, mi = map(int, msg.text.strip().split(':'))
        if not (0 <= h <= 23 and 0 <= mi <= 59): raise ValueError
        data = load_data(); uid = str(msg.chat.id)
        data['users'].setdefault(uid, {})['reminder_time'] = {'hour': h, 'minute': mi}
        save_data(data); schedule_reminders()
        bot.send_message(msg.chat.id, f"Нагадування: {h:02d}:{mi:02d}", reply_markup=build_main_menu())
    except Exception:
        sent = bot.send_message(msg.chat.id, "Невірний формат ГГ:ХХ:")
        bot.register_next_step_handler(sent, process_reminder_time)

@bot.message_handler(func=lambda m: m.text == 'Помінятись')
def cmd_ex(msg):
    uid = str(msg.chat.id)
    data = load_data()
    data['users'].setdefault(uid, {})['ex_temp'] = {}  # скидання попередніх даних
    save_data(data)

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("📅 Поточний місяць", callback_data='ex_month_current'),
        types.InlineKeyboardButton("📅 Наступний місяць", callback_data='ex_month_next')
    )
    bot.send_message(msg.chat.id, "Оберіть місяць для обміну чергуванням:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('ex_month_'))
def handle_month_selection(c):
    uid = str(c.from_user.id)
    month_key = c.data.split('_')[2]
    data = load_data()
    data['users'][uid]['ex_temp'] = {'month_key': month_key}
    save_data(data)

    sched = data.get(f'schedule_{month_key}', {})
    user_dates = [d for d, u in sched.items() if u == uid]
    if not user_dates:
        bot.answer_callback_query(c.id)
        bot.send_message(uid, "У вас немає чергувань у цьому місяці.")
        return

    kb = types.InlineKeyboardMarkup()
    for d in user_dates:
        d_obj = datetime.fromisoformat(d)
        label = d_obj.strftime('%d.%m')
        kb.add(types.InlineKeyboardButton(label, callback_data=f'ex_mydate_{d}'))

    #kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data='ex_restart'))
    bot.edit_message_text("Оберіть своє чергування:", uid, c.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('ex_mydate_'))
def handle_mydate_selection(c):
    uid = str(c.from_user.id)
    selected_date = c.data.replace('ex_mydate_', '')
    data = load_data()
    data['users'][uid]['ex_temp']['from'] = selected_date
    save_data(data)

    sched = data.get(f"schedule_{data['users'][uid]['ex_temp']['month_key']}", {})
    available_uids = set(sched.values()) - {uid}

    kb = types.InlineKeyboardMarkup()
    for other_uid in available_uids:
        user_info = data['users'].get(other_uid, {})
        name = f"{user_info.get('emoji', '')} {user_info.get('name', f'UID {other_uid}')}"

        kb.add(types.InlineKeyboardButton(name, callback_data=f'ex_user_{other_uid}'))

    #kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data='ex_month_back'))
    bot.edit_message_text("Оберіть колегу для обміну:", uid, c.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('ex_user_'))
def handle_colleague_selection(c):
    uid = str(c.from_user.id)
    target_uid = c.data.replace('ex_user_', '')
    data = load_data()

    sched = data.get(f"schedule_{data['users'][uid]['ex_temp']['month_key']}", {})
    target_dates = [d for d, u in sched.items() if u == target_uid]

    if not target_dates:
        bot.answer_callback_query(c.id)
        bot.send_message(uid, "У цього колеги немає чергувань у цьому місяці.")
        return

    data['users'][uid]['ex_temp']['target'] = target_uid
    save_data(data)

    kb = types.InlineKeyboardMarkup()
    for d in target_dates:
        d_obj = datetime.fromisoformat(d)
        label = d_obj.strftime('%d.%m')
        kb.add(types.InlineKeyboardButton(label, callback_data=f'ex_targetdate_{d}'))

   # kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data='ex_user_back'))
    bot.edit_message_text("Оберіть дату чергування колеги:", uid, c.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith('ex_targetdate_'))
def handle_target_date_selection(c):
    uid = str(c.from_user.id)
    to_date = c.data.replace('ex_targetdate_', '')
    data = load_data()
    ex = data['users'][uid].pop('ex_temp', {})
    from_date = ex['from']
    target_uid = ex['target']

    exchange_requests[uid] = {'from': from_date, 'to': to_date, 'target': target_uid}

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Так", callback_data=f"ex_yes_{uid}"),
        types.InlineKeyboardButton("❌ Ні", callback_data=f"ex_no_{uid}")
    )

    bot.send_message(
        int(target_uid),
        f"{data['users'][uid]['name']} пропонує обмін:\n"
        f"🔁 Ваше чергування {to_date[8:]}.{to_date[5:7]} "
        f"↔ його(її) {from_date[8:]}.{from_date[5:7]}\nПогоджуєтесь?",
        reply_markup=kb
    )
    bot.send_message(uid, "Запит надіслано колезі.")

@bot.callback_query_handler(func=lambda c: c.data == 'ex_restart')
def handle_restart(c):
    uid = str(c.from_user.id)
    data = load_data()
    data['users'][uid]['ex_temp'] = {}
    save_data(data)

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("📅 Поточний місяць", callback_data='ex_month_current'),
        types.InlineKeyboardButton("📅 Наступний місяць", callback_data='ex_month_next')
    )
    bot.edit_message_text("Оберіть місяць для обміну чергуванням:", uid, c.message.message_id, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == 'ex_month_back')
def handle_back_to_month(c):
    return handle_restart(c)

@bot.callback_query_handler(func=lambda c: c.data == 'ex_user_back')
def handle_back_to_user_dates(c):
    uid = str(c.from_user.id)
    data = load_data()
    ex_temp = data['users'][uid].get('ex_temp', {})
    from_date = ex_temp.get('from')
    if not from_date:
        return handle_restart(c)

    sched = data.get(f"schedule_{ex_temp['month_key']}", {})
    available_uids = set(sched.values()) - {uid}
    kb = types.InlineKeyboardMarkup()
    for other_uid in available_uids:
        name = data['users'].get(other_uid, {}).get('name', f"UID {other_uid}")
        kb.add(types.InlineKeyboardButton(name, callback_data=f'ex_user_{other_uid}'))
    #kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data='ex_month_back'))

    bot.edit_message_text("Оберіть колегу для обміну:", uid, c.message.message_id, reply_markup=kb)



def process_exchange_from(msg):
    if msg.text == 'Скасувати': return cmd_cancel(msg)
    uid = str(msg.chat.id)
    try:
        day, mon = map(int, msg.text.strip().split('.'))
        data = load_data()
        sched = data.get('schedule_current', {})
        if not sched:
            bot.send_message(msg.chat.id, "Спочатку згенеруйте розклад.", reply_markup=build_main_menu())
            return
        first_iso = next(iter(sched))
        sched_dt = datetime.fromisoformat(first_iso)
        year, month = sched_dt.year, sched_dt.month
        dt_obj = date(year, month, day)
        frm = dt_obj.isoformat()
        if sched.get(frm) != uid:
            bot.send_message(msg.chat.id, "У вас немає чергування на цю дату.", reply_markup=build_main_menu())
            return
        data['users'][uid].setdefault('ex_temp', {})['from'] = frm
        save_data(data)
        sent = bot.send_message(msg.chat.id, "Введіть дату колеги (dd.mm):")
        bot.register_next_step_handler(sent, process_exchange_to)
    except ValueError:
        sent = bot.send_message(msg.chat.id, "Невірний формат dd.mm:")
        bot.register_next_step_handler(sent, process_exchange_from)

def process_exchange_to(msg):
    if msg.text == 'Скасувати': return cmd_cancel(msg)
    uid = str(msg.chat.id)
    try:
        day, mon = map(int, msg.text.strip().split('.'))
        data = load_data()
        sched = data.get('schedule_current', {})
        first_iso = next(iter(sched))
        sched_dt = datetime.fromisoformat(first_iso)
        year, month = sched_dt.year, sched_dt.month
        dt_obj = date(year, month, day)
        to_dt = dt_obj.isoformat()
        ex = data['users'][uid].pop('ex_temp', {})
        ex['to'] = to_dt
        tgt = sched.get(to_dt)
        if not tgt or tgt == uid:
            bot.send_message(msg.chat.id, "Немає колеги на цю дату.", reply_markup=build_main_menu())
            return
        exchange_requests[uid] = {'from': ex['from'], 'to': to_dt, 'target': tgt}
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("Так", callback_data=f"ex_yes_{uid}"),
            types.InlineKeyboardButton("Ні", callback_data=f"ex_no_{uid}")
        )
        bot.send_message(
            int(tgt),
            f"{data['users'][uid]['name']} пропонує обмін: ваш {to_dt[8:]} ↔ його(її) {ex['from'][8:]}. Погоджуєтесь?",
            reply_markup=kb
        )
        bot.send_message(msg.chat.id, "Запит відправлено.", reply_markup=build_main_menu())
    except ValueError:
        sent = bot.send_message(msg.chat.id, "Невірний формат dd.mm:")
        bot.register_next_step_handler(sent, process_exchange_to)

@bot.callback_query_handler(func=lambda c: c.data.startswith('ex_'))
def handle_exchange_callback(c):
    _, action, uid = c.data.split('_')
    req = exchange_requests.pop(uid, None)

    if not req:
        bot.answer_callback_query(c.id, "Запит не знайдено.")
        return

    data = load_data()
    sched = data.get('schedule_current', {})
    data['schedule_current'] = sched  # гарантія оновлення

    fr, to_dt, tgt = req['from'], req['to'], req['target']

    if action == 'yes':
        # Обмін місцями
        sched[fr], sched[to_dt] = tgt, uid

        save_data(data)  # обов’язкове збереження після оновлення
        bot.send_message(int(uid), f"✅ Обмін підтверджено! Ваш новий день чергування: {to_dt[8:]}")
        bot.send_message(int(tgt), f"✅ Ви погодились на обмін. Ваш новий день чергування: {fr[8:]}")
    else:
        bot.send_message(int(uid), "❌ Колега відхилив обмін.")
        bot.send_message(int(tgt), "Ви відхилили запит на обмін.")

    bot.answer_callback_query(c.id)


if __name__ == '__main__':
    bot.infinity_polling()
