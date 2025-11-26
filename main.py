import aiosqlite
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import random
import re
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PeerIdInvalidError
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.types import InputReportReasonSpam
from telebot.async_telebot import AsyncTeleBot
from telebot import types
import time
from pyCryptoPayAPI import pyCryptoPayAPI
import pytz
import psutil

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('bot.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

user_states = {}
active_freezes = {}

class Config:
    TOKEN = os.getenv("BOT_TOKEN", "@FuckAnarche")
    ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",")]
    API_ID = int(os.getenv("API_ID", "@FuckAnarche"))
    API_HASH = os.getenv("API_HASH", "@FuckAnarche")
    LOG_CHAT = int(os.getenv("LOG_CHAT", "@FuckAnarche"))
    CHANNEL_ID = int(os.getenv("CHANNEL_ID", "@FuckAnarche"))
    BOT_NAME = os.getenv("BOT_NAME", "Vand Freeze")
    BOT_TAG = os.getenv("BOT_TAG", "@Vandfrezzebot")
    CHANNEL_LINK = os.getenv("CHANNEL_LINK", "@FuckAnarche")
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@anrch3")
    DOCUMENTATION = os.getenv("DOCUMENTATION", "@FuckAnarche")
    CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "407794:AAhwGNoLJmylU0tjuUaZugVq1Mk4mmyRQbf")
    PRICES = {
        '1_day': float(os.getenv("PRICE_1_DAY", "2")),
        '7_days': float(os.getenv("PRICE_7_DAYS", "4")),
        '14_days': float(os.getenv("PRICE_14_DAYS", "5")),
        '30_days': float(os.getenv("PRICE_30_DAYS", "4")),
        '365_days': float(os.getenv("PRICE_365_DAYS", "4.5")),
        'infinity': float(os.getenv("PRICE_INFINITY", "5"))
    }
    SESSIONS_DIR = os.getenv("SESSIONS_DIR", "sessions")
    MAX_CONCURRENT_SESSIONS = int(os.getenv("MAX_CONCURRENT_SESSIONS", "20000"))
    FREEZE_DELAY = {
        'min': float(os.getenv("FREEZE_MIN_DELAY", "0.5")),
        'max': float(os.getenv("FREEZE_MAX_DELAY", "1.5"))
    }
    FREEZE_COOLDOWN = int(os.getenv("FREEZE_COOLDOWN", "300"))

config = Config()
bot = AsyncTeleBot(config.TOKEN)
crypto = pyCryptoPayAPI(api_token=config.CRYPTOBOT_TOKEN)

promocodeki = {}  # Переменная для промокодов (для совместимости, хотя используется база)

async def init_db():
    async with aiosqlite.connect('users.db', timeout=10) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users
                          (user_id INTEGER PRIMARY KEY,
                           subscribe TEXT,
                           freezes INTEGER DEFAULT 0,
                           last_freeze INTEGER DEFAULT 0)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS payments
                          (invoice_id TEXT PRIMARY KEY,
                           user_id INTEGER,
                           amount REAL,
                           status TEXT,
                           timestamp TEXT,
                           days INTEGER)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS freezes
                          (id INTEGER PRIMARY KEY AUTOINCREMENT,
                           user_id INTEGER,
                           target TEXT,
                           success INTEGER,
                           timestamp TEXT)''')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON users(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_invoice_id ON payments(invoice_id)")
        await db.execute('''CREATE TABLE IF NOT EXISTS settings
                          (id INTEGER PRIMARY KEY,
                           freeze_min_delay REAL,
                           freeze_max_delay REAL,
                           freeze_cooldown INTEGER)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS promocodes
                          (code TEXT PRIMARY KEY,
                           uses_left INTEGER,
                           days INTEGER)''')
        cursor = await db.execute("SELECT COUNT(*) FROM settings")
        count = (await cursor.fetchone())[0]
        if count == 0:
            await db.execute('''INSERT INTO settings (id, freeze_min_delay, freeze_max_delay, freeze_cooldown)
                             VALUES (?, ?, ?, ?)''',
                            (1, config.FREEZE_DELAY['min'], config.FREEZE_DELAY['max'], config.FREEZE_COOLDOWN))
        await db.commit()
        logger.info("База данных инициализирована 📊")

async def load_freeze_delays():
    async with aiosqlite.connect('users.db', timeout=10) as db:
        cursor = await db.execute("SELECT freeze_min_delay, freeze_max_delay, freeze_cooldown FROM settings WHERE id = 1")
        result = await cursor.fetchone()
        if result:
            config.FREEZE_DELAY['min'] = result[0]
            config.FREEZE_DELAY['max'] = result[1]
            config.FREEZE_COOLDOWN = result[2]
        logger.info(f"Загружены задержки: freeze={config.FREEZE_DELAY}, cooldown={config.FREEZE_COOLDOWN} ⏱️")

async def check_channel_subscription(user_id):
    try:
        member = await bot.get_chat_member(config.CHANNEL_ID, user_id)
        logger.info(f"Проверка подписки для user_id={user_id}: статус={member.status}")
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка при проверке подписки для user_id={user_id}: {e}")
        return False

async def prompt_subscription(message, callback_data=None):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("Подписаться на канал 📢", url=config.CHANNEL_LINK),
        types.InlineKeyboardButton("Проверить подписку ✅", callback_data=f"check_sub_{callback_data or 'menu'}")
    )
    try:
        if callback_data:
            await bot.edit_message_text(
                f"Для использования бота подпишитесь на канал: {config.CHANNEL_LINK} 📢",
                message.chat.id,
                message.message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
        else:
            with open("vandfreeze.jpg", "rb") as photo:
                await bot.send_photo(
                    message.chat.id,
                    photo,
                    caption=f"Для использования бота подпишитесь на канал: {config.CHANNEL_LINK} 📢",
                    parse_mode="Markdown",
                    reply_markup=markup
                )
        logger.info(f"Отправлен запрос на подписку для user_id={message.from_user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке запроса на подписку для user_id={message.from_user.id}: {e}")

async def validate_session(session_file):
    try:
        async with asyncio.timeout(5):
            client = TelegramClient(session_file, config.API_ID, config.API_HASH)
            await client.connect()
            authorized = await client.is_user_authorized()
            logger.info(f"Сессия {session_file} авторизована: {authorized}")
            await client.disconnect()
            if not authorized:
                os.remove(session_file)
                logger.info(f"Удалена невалидная сессия: {session_file}")
                return False
            return True
    except SessionPasswordNeededError:
        logger.error(f"Сессия {session_file} требует двухфакторной аутентификации 🔐")
        return False
    except asyncio.TimeoutError:
        logger.error(f"Тайм-аут при подключении к сессии {session_file} ⏳")
        return False
    except Exception as e:
        logger.error(f"Ошибка при валидации сессии {session_file}: {e}")
        return False

async def load_valid_sessions():
    sessions = []
    if not os.path.exists(config.SESSIONS_DIR):
        os.makedirs(config.SESSIONS_DIR)
        logger.warning(f"Создана директория {config.SESSIONS_DIR}, но сессии не найдены 📁")
        return sessions
    files = [f for f in os.listdir(config.SESSIONS_DIR) if f.endswith('.session')]
    logger.info(f"Найдено {len(files)} файлов сессий 📄")
    max_sessions = config.MAX_CONCURRENT_SESSIONS
    for i, file in enumerate(files[:max_sessions]):
        session_path = os.path.join(config.SESSIONS_DIR, file)
        logger.info(f"Валидация сессии {session_path} ({i+1}/{max_sessions})")
        try:
            async with asyncio.timeout(5):
                if await validate_session(session_path):
                    sessions.append(session_path)
        except asyncio.TimeoutError:
            logger.warning(f"Тайм-аут при валидации сессии {session_path} ⏳")
        except Exception as e:
            logger.error(f"Ошибка при валидации сессии {session_path}: {e}")
        if len(sessions) >= max_sessions:
            break
    logger.info(f"Загружено {len(sessions)} валидных сессий ✅")
    return sessions

async def count_users():
    async with aiosqlite.connect('users.db', timeout=10) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cursor.fetchone())[0]
    return total_users

async def count_subscribed_users():
    async with aiosqlite.connect('users.db', timeout=10) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users WHERE subscribe > ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        subscribed_users = (await cursor.fetchone())[0]
    return subscribed_users

async def count_freezes():
    async with aiosqlite.connect('users.db', timeout=10) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM freezes WHERE success > 0")
        successful_freezes = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM freezes WHERE success = 0")
        failed_freezes = (await cursor.fetchone())[0]
    return successful_freezes, failed_freezes

main_menu = types.InlineKeyboardMarkup(row_width=2)
main_menu.add(
    types.InlineKeyboardButton("Мой профиль 👤", callback_data='profile'),
    types.InlineKeyboardButton("Магазин 🛒", callback_data='shop'),
    types.InlineKeyboardButton("Запустить 🚀", callback_data='regular_freeze'),
    types.InlineKeyboardButton("Информация ℹ️", callback_data='info'),
    types.InlineKeyboardButton("Промокоды 🎟️", callback_data='promocodes')
)

back_button = types.InlineKeyboardButton("Назад ⬅️", callback_data='back_to_menu')
back_markup = types.InlineKeyboardMarkup()
back_markup.add(back_button)

info_markup = types.InlineKeyboardMarkup(row_width=1)
info_markup.add(
    types.InlineKeyboardButton("Канал 📢", url=config.CHANNEL_LINK),
    types.InlineKeyboardButton("Администрация 👮", url="https://t.me/FuckAnarche"),
    types.InlineKeyboardButton("Поддержка 🛠️", url="https://t.me/FuckAnarche"),
    types.InlineKeyboardButton("Отзывы ⭐", url="https://t.me/+c2oDgNzJ8HM3MDA9"),
    back_button
)

shop_markup = types.InlineKeyboardMarkup(row_width=2)
shop_markup.add(
    types.InlineKeyboardButton(f"1 день — {config.PRICES['1_day']}$ 💰", callback_data='sub_1'),
    types.InlineKeyboardButton(f"7 дней — {config.PRICES['7_days']}$ 💰", callback_data='sub_7'),
    types.InlineKeyboardButton(f"14 дней — {config.PRICES['14_days']}$ 💰", callback_data='sub_14'),
    types.InlineKeyboardButton(f"30 дней — {config.PRICES['30_days']}$ 💰", callback_data='sub_30'),
    types.InlineKeyboardButton(f"365 дней — {config.PRICES['365_days']}$ 💰", callback_data='sub_365'),
    types.InlineKeyboardButton(f"Навсегда — {config.PRICES['infinity']}$ 💰", callback_data='sub_inf')
)
shop_markup.add(back_button)

admin_markup = types.InlineKeyboardMarkup(row_width=2)
admin_markup.add(
    types.InlineKeyboardButton("Рассылка 📬", callback_data='admin_broadcast'),
    types.InlineKeyboardButton("Выдать подписку 🎁", callback_data='admin_give_sub'),
    types.InlineKeyboardButton("Сбросить подписку 🗑️", callback_data='admin_remove_sub'),
    types.InlineKeyboardButton("Настроить задержки ⏱️", callback_data='admin_set_delays'),
    types.InlineKeyboardButton("Статистика 📊", callback_data='admin_stats'),
    types.InlineKeyboardButton("Сессии 🔗", callback_data='admin_sessions'),
    types.InlineKeyboardButton("Создать промокод 🎟️", callback_data='admin_create_promo')
)
admin_markup.add(back_button)

async def check_subscription(user_id):
    async with aiosqlite.connect('users.db', timeout=10) as db:
        cursor = await db.execute("SELECT subscribe FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        if not result:
            return False
        try:
            subscribe_date = datetime.strptime(result[0], "%Y-%m-%d %H:%M:%S")
            is_active = subscribe_date > datetime.now()
        except (ValueError, TypeError):
            is_active = False
        return is_active

async def can_freeze(user_id):
    async with aiosqlite.connect('users.db', timeout=10) as db:
        cursor = await db.execute("SELECT last_freeze FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        if not result:
            return False
        last_freeze = result[0]
        return time.time() - last_freeze > config.FREEZE_COOLDOWN

async def update_last_freeze(user_id):
    async with aiosqlite.connect('users.db', timeout=10) as db:
        await db.execute("UPDATE users SET last_freeze = ? WHERE user_id = ?", (int(time.time()), user_id))
        await db.commit()

async def log_freeze(user_id, target, success):
    sessions = await load_valid_sessions()
    total_sessions = len(sessions)
    failed = total_sessions - success

    # Разделяем target на username и post_id
    target_parts = target.split('/')
    target_username = target_parts[0]
    post_id = target_parts[1] if len(target_parts) > 1 else "N/A"
    
    # Определяем, является ли target_username настоящим username или ID чата
    target_display = target_username if target_username.startswith('@') else "@Нет username"
    target_id = user_id  # Для лога используем user_id как ID цели, если нет другой информации

    # Получаем username пользователя
    try:
        user = await bot.get_chat(user_id)
        user_username = user.username if user.username else "@None"
    except Exception as e:
        user_username = "@None"
        logger.error(f"Ошибка при получении username пользователя user_id={user_id}: {e}")

    log_msg = (
        f"📈 Бот завершил работу\n"
        f"└─📂 Метод: B0tN3t-method\n\n"
        f"🎯 Таргет\n"
        f"└─ ID: {target_id}\n"
        f"└─ USERNAME: {target_display}\n\n"
        f"🟢 Успешно отправлено: {success}\n"
        f"🔴 Не удалось отправить: {failed}\n\n"
        f"⛓️‍💥 Ссылка: [тык](https://t.me/{target})\n"
        f"👤 Пользователь: {user_id} ({user_username})"
    )
    
    async with aiosqlite.connect('users.db', timeout=10) as db:
        await db.execute(
            "INSERT INTO freezes (user_id, target, success, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, f"https://t.me/{target}", success, datetime.now(pytz.timezone('Europe/Moscow')).strftime("%Y-%m-%d %H:%M:%S MSK"))
        )
        await db.commit()
    
    try:
        await bot.send_message(config.LOG_CHAT, log_msg, parse_mode="Markdown")
        logger.info(f"Отправлен лог заморозки для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке лога заморозки в LOG_CHAT: {e}")

async def send_report(session_file, target, post_id):
    logger.info(f"Попытка отправки жалобы из {session_file} на {target}/{post_id}")
    try:
        delay = random.uniform(config.FREEZE_DELAY['min'], config.FREEZE_DELAY['max'])
        logger.debug(f"Применяется задержка {delay:.2f} секунд для сессии {session_file}")
        await asyncio.sleep(delay)
        
        async with asyncio.timeout(3):
            client = TelegramClient(session_file, config.API_ID, config.API_HASH)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    logger.warning(f"Сессия {session_file} не авторизована")
                    return False, "Сессия не авторизована"
                entity = await client.get_entity(target)
                await client(ReportRequest(
                    peer=entity,
                    id=[int(post_id)],
                    reason=InputReportReasonSpam(),
                    message="Автоматическая жалоба на спам 🚫"
                ))
                logger.info(f"Успешная жалоба из {session_file} на {target}/{post_id}")
                return True, "Успешно ✅"
            except PeerIdInvalidError:
                logger.error(f"Неверный ID цели {target} для сессии {session_file}")
                return False, "Не удалось найти username или ID чата 🚫"
            except FloodWaitError as e:
                logger.warning(f"Сессия {session_file} получила FloodWaitError, ожидание {e.seconds} секунд")
                return False, f"Сессия заблокирована Telegram (FloodWaitError: ожидание {e.seconds} секунд) ⏳"
            except asyncio.TimeoutError:
                logger.error(f"Тайм-аут при обработке {target}/{post_id} для сессии {session_file}")
                return False, "Тайм-аут подключения ⏳"
            except Exception as e:
                logger.error(f"Непредвиденная ошибка в сессии {session_file} для {target}/{post_id}: {e}")
                return False, f"Непредвиденная ошибка: {str(e)} 🚫"
            finally:
                try:
                    await client.disconnect()
                except Exception as disconnect_error:
                    logger.error(f"Ошибка при отключении сессии {session_file}: {disconnect_error}")
    except asyncio.TimeoutError:
        logger.error(f"Тайм-аут при обработке {target}/{post_id} для сессии {session_file}")
        return False, "Тайм-аут подключения ⏳"
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в сессии {session_file} для {target}/{post_id}: {e}")
        return False, f"Непредвиденная ошибка: {str(e)} 🚫"

async def run_freeze(user_id, target, post_id, message_id):
    active_freezes[user_id] = {'running': True, 'message_id': message_id}
    
    sessions = await load_valid_sessions()
    if not sessions:
        try:
            await bot.edit_message_text(
                f"Заморозка не выполнена! 🚫\n\n"
                f"Цель: {target}/{post_id}\n"
                f"Нет доступных сессий 😔",
                user_id,
                message_id,
                parse_mode="Markdown"
            )
            logger.warning(f"Нет доступных сессий для user_id={user_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения об отсутствии сессий для user_id={user_id}: {e}")
        active_freezes.pop(user_id, None)
        return
    
    total_success = 0
    session_statuses = []
    logger.info(f"Начало заморозки для user_id={user_id}: цель={target}/{post_id}, сессий={len(sessions)}")
    
    for i, session_file in enumerate(sessions):
        if user_id not in active_freezes or not active_freezes[user_id]['running']:
            logger.info(f"Заморозка остановлена пользователем user_id={user_id}")
            break
        
        try:
            success, status_message = await send_report(session_file, target, post_id)
            session_statuses.append(f"Сессия {session_file}: {status_message}")
            if success:
                total_success += 1
        except Exception as e:
            session_statuses.append(f"Сессия {session_file}: Не работает (Ошибка: {str(e)}) 🚫")
            logger.error(f"Ошибка обработки сессии {session_file} для user_id={user_id}: {e}")
        
        try:
            progress_percent = min(int((i + 1) / len(sessions) * 100), 100)
            await bot.edit_message_text(
                f"Заморозка в процессе ⏳\n"
                f"Цель: {target}/{post_id}\n"
                f"Сессий: {i + 1}/{len(sessions)}\n"
                f"Успешных: {total_success} ✅\n"
                f"Прогресс: {progress_percent}% 📈",
                user_id,
                message_id,
                parse_mode="Markdown"
            )
            logger.debug(f"Обновлен прогресс для user_id={user_id}: сессия {i + 1}/{len(sessions)}, {progress_percent}%")
        except Exception as e:
            logger.error(f"Ошибка обновления прогресса для user_id={user_id}: {e}")
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"freeze_report_{user_id}_{timestamp}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"Отчет по заморозке для цели: https://t.me/{target}/{post_id} ❄️\n")
        f.write(f"Пользователь: {user_id}\n")
        f.write(f"Время: {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S MSK')} ⏰\n")
        f.write(f"Всего сессий: {len(sessions)} 📄\n")
        f.write(f"Успешных жалоб: {total_success} ✅\n\n")
        f.write("Статус каждой сессии:\n")
        for status in session_statuses:
            f.write(f"{status}\n")
    
    try:
        with open(filename, 'rb') as f:
            await bot.send_document(user_id, f, caption=f"Отчет по заморозке для https://t.me/{target}/{post_id} 📄")
        logger.info(f"Отправлен файл отчета {filename} пользователю user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке файла отчета пользователю user_id={user_id}: {e}")
    finally:
        try:
            os.remove(filename)
            logger.info(f"Удален временный файл {filename}")
        except Exception as e:
            logger.error(f"Ошибка при удалении временного файла {filename}: {e}")
    
    if user_id in active_freezes:
        try:
            await bot.edit_message_text(
                f"Заморозка завершена! 🎉\n\n"
                f"Цель: {target}/{post_id}\n"
                f"Успешных жалоб: {total_success} из {len(sessions)} сессий ✅\n"
                f"Отчет отправлен вам в виде файла 📄",
                user_id,
                message_id,
                parse_mode="Markdown"
            )
            logger.info(f"Заморозка завершена для user_id={user_id}: {total_success} успешных жалоб")
        except Exception as e:
            logger.error(f"Ошибка при отправке финального сообщения для user_id={user_id}: {e}")
        
        try:
            await log_freeze(user_id, f"{target}/{post_id}", total_success)
        except Exception as e:
            logger.error(f"Ошибка при логировании заморозки для user_id={user_id}: {e}")
        
        active_freezes.pop(user_id, None)
    else:
        logger.info(f"Заморозка была прервана для user_id={user_id}")

@bot.message_handler(commands=['start'])
async def start(message):
    user_id = message.from_user.id
    async with aiosqlite.connect('users.db', timeout=10) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, subscribe) VALUES (?, ?)",
                        (user_id, "2000-01-01 00:00:00"))
        await db.commit()
    
    if not await check_channel_subscription(user_id):
        await prompt_subscription(message)
        return
    
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            await bot.send_photo(
                message.chat.id,
                photo,
                caption=f"Vand Freeze — Главное меню 🎉\n\nДобро пожаловать!",
                parse_mode="Markdown",
                reply_markup=main_menu
            )
        logger.info(f"Отправлено приветственное сообщение для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке приветственного сообщения для user_id={user_id}: {e}")

@bot.message_handler(commands=['admin'])
async def admin_panel(message):
    user_id = message.from_user.id
    if user_id not in config.ADMINS:
        await bot.reply_to(message, "У вас нет доступа к админ-панели! 🚫")
        return
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            await bot.send_photo(
                message.chat.id,
                photo,
                caption="Админ-панель ⚙️",
                parse_mode="Markdown",
                reply_markup=admin_markup
            )
        logger.info(f"Открыта админ-панель для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при открытии админ-панели для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_sub_'))
async def check_subscription_callback(call):
    user_id = call.from_user.id
    callback_data = call.data.split('_')[-1]
    if await check_channel_subscription(user_id):
        try:
            if callback_data == 'menu':
                with open("vandfreeze.jpg", "rb") as photo:
                    media = types.InputMediaPhoto(photo, caption=f"Vand Freeze — Главное меню 🎉\n\nДобро пожаловать!", parse_mode="Markdown")
                    await bot.edit_message_media(
                        media=media,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=main_menu
                    )
            elif callback_data == 'profile':
                is_active = await check_subscription(user_id)
                async with aiosqlite.connect('users.db', timeout=10) as db:
                    cursor = await db.execute("SELECT subscribe FROM users WHERE user_id = ?", (user_id,))
                    result = await cursor.fetchone()
                    subscribe_date = result[0] if result else "2000-01-01 00:00:00"
                    try:
                        subscribe_date = datetime.strptime(subscribe_date, "%Y-%m-%d %H:%M:%S")
                        status = f"Активна до {subscribe_date.strftime('%Y-%m-%d')} ✅" if is_active else "Не активна 😔"
                    except (ValueError, TypeError):
                        status = "Не активна 😔"
                with open("vandfreeze.jpg", "rb") as photo:
                    media = types.InputMediaPhoto(photo, caption=f"Ваш профиль 👤\n\nID: `{user_id}`\nПодписка: {status}", parse_mode="Markdown")
                    await bot.edit_message_media(
                        media=media,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=back_markup
                    )
            elif callback_data == 'shop':
                with open("vandfreeze.jpg", "rb") as photo:
                    media = types.InputMediaPhoto(photo, caption=f"Магазин подписок 🛒\n\n1 день — {config.PRICES['1_day']}$\n7 дней — {config.PRICES['7_days']}$\n14 дней — {config.PRICES['14_days']}$\n30 дней — {config.PRICES['30_days']}$\n365 дней — {config.PRICES['365_days']}$\nНавсегда — {config.PRICES['infinity']}$", parse_mode="Markdown")
                    await bot.edit_message_media(
                        media=media,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=shop_markup
                    )
            elif callback_data == 'regular_freeze':
                is_active = await check_subscription(user_id)
                if not is_active:
                    await bot.answer_callback_query(call.id, "Ваша подписка не активна! Купите подписку в магазине. 😔")
                    return
                if not await can_freeze(user_id):
                    await bot.answer_callback_query(call.id, f"Подождите {config.FREEZE_COOLDOWN} секунд перед следующей заморозкой! ⏳")
                    return
                user_states[user_id] = {'action': 'awaiting_freeze'}
                with open("vandfreeze.jpg", "rb") as photo:
                    media = types.InputMediaPhoto(photo, caption=f"Заморозь ❄️\n\nОтправьте ссылку на пост в формате:\n`https://t.me/username/123`", parse_mode="Markdown")
                    await bot.edit_message_media(
                        media=media,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=back_markup
                    )
            elif callback_data == 'info':
                with open("vandfreeze.jpg", "rb") as photo:
                    media = types.InputMediaPhoto(photo, caption=f"Информация ℹ️\n\nВыберите нужную опцию:", parse_mode="Markdown")
                    await bot.edit_message_media(
                        media=media,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=info_markup
                    )
            elif callback_data == 'promocodes':
                full_name = f"{call.from_user.first_name} {call.from_user.last_name or ''}".strip()
                if not full_name:
                    with open("vandfreeze.jpg", "rb") as photo:
                        media = types.InputMediaPhoto(
                            photo,
                            caption=f"Для доступа к промокодам установите отображаемое имя в Telegram и добавьте в него тег бота: `{config.BOT_TAG}` 🚫\n\nНажмите на `{config.BOT_TAG}` для копирования.",
                            parse_mode="Markdown"
                        )
                        await bot.edit_message_media(
                            media=media,
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            reply_markup=back_markup
                        )
                    logger.info(f"Пользователь user_id={user_id} не имеет отображаемого имени, запрошен тег {config.BOT_TAG}")
                    return
                if config.BOT_TAG.lower() not in full_name.lower():
                    with open("vandfreeze.jpg", "rb") as photo:
                        media = types.InputMediaPhoto(
                            photo,
                            caption=f"В вашем отображаемом имени отсутствует тег бота: `{config.BOT_TAG}` 🚫\n\nДобавьте `{config.BOT_TAG}` в ваше отображаемое имя в Telegram и попробуйте снова. Нажмите на `{config.BOT_TAG}` для копирования.",
                            parse_mode="Markdown"
                        )
                        await bot.edit_message_media(
                            media=media,
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            reply_markup=back_markup
                        )
                    logger.info(f"Тег {config.BOT_TAG} отсутствует в отображаемом имени пользователя user_id={user_id}")
                    return
                user_states[user_id] = {'action': 'awaiting_promocode'}
                with open("vandfreeze.jpg", "rb") as photo:
                    media = types.InputMediaPhoto(photo, caption="Введите промокод 🎟️", parse_mode="Markdown")
                    await bot.edit_message_media(
                        media=media,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=back_markup
                    )
                logger.info(f"Запрошен промокод от user_id={user_id}")
            logger.info(f"Подписка подтверждена для user_id={user_id}, действие: {callback_data}")
        except Exception as e:
            logger.error(f"Ошибка после проверки подписки для user_id={user_id}, действие: {callback_data}: {e}")
    else:
        await bot.answer_callback_query(call.id, "Вы не подписаны на канал! Подпишитесь и попробуйте снова. 📢")

@bot.callback_query_handler(func=lambda call: call.data == 'profile')
async def profile(call):
    user_id = call.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(call.message, 'profile')
        return
    is_active = await check_subscription(user_id)
    async with aiosqlite.connect('users.db', timeout=10) as db:
        cursor = await db.execute("SELECT subscribe FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        subscribe_date = result[0] if result else "2000-01-01 00:00:00"
        try:
            subscribe_date = datetime.strptime(subscribe_date, "%Y-%m-%d %H:%M:%S")
            status = f"Активна до {subscribe_date.strftime('%Y-%m-%d')} ✅" if is_active else "Не активна 😔"
        except (ValueError, TypeError):
            status = "Не активна 😔"
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption=f"Ваш профиль 👤\n\nID: `{user_id}`\nПодписка: {status}", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Отправлен профиль для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке профиля для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'shop')
async def shop(call):
    user_id = call.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(call.message, 'shop')
        return
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption=f"Магазин подписок 🛒\n\n1 день — {config.PRICES['1_day']}$\n7 дней — {config.PRICES['7_days']}$\n14 дней — {config.PRICES['14_days']}$\n30 дней — {config.PRICES['30_days']}$\n365 дней — {config.PRICES['365_days']}$\nНавсегда — {config.PRICES['infinity']}$", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=shop_markup
            )
        logger.info(f"Отправлен магазин для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке магазина для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'info')
async def info(call):
    user_id = call.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(call.message, 'info')
        return
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption=f"Информация ℹ️\n\nВыберите нужную опцию:", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=info_markup
            )
        logger.info(f"Отправлена вкладка информации для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке вкладки информации для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_'))
async def process_subscription(call):
    user_id = call.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(call.message, call.data)
        return
    sub_type = call.data.split('_')[1]
    prices = {
        '1': (config.PRICES['1_day'], "1 день", 1),
        '7': (config.PRICES['7_days'], "7 дней", 7),
        '14': (config.PRICES['14_days'], "14 дней", 14),
        '30': (config.PRICES['30_days'], "30 дней", 30),
        '365': (config.PRICES['365_days'], "365 дней", 365),
        'inf': (config.PRICES['infinity'], "Навсегда", 3650)
    }
    price, period, days = prices[sub_type]
    try:
        invoice = crypto.create_invoice(asset='USDT', amount=price)
        invoice_id = invoice['invoice_id']
        pay_url = invoice['pay_url']
        
        async with aiosqlite.connect('users.db', timeout=10) as db:
            await db.execute(
                "INSERT INTO payments (invoice_id, user_id, amount, days, status, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (invoice_id, user_id, price, days, 'created', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            await db.commit()
        
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("Оплатить 💸", url=pay_url),
            types.InlineKeyboardButton("Проверить оплату ✅", callback_data=f'check_{invoice_id}')
        )
        markup.add(back_button)
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption=f"Оплата подписки 💰\n\nТип: {period}\nСумма: {price}$\nID платежа: `{invoice_id}`\n\n1. Нажмите 'Оплатить'\n2. После оплаты нажмите 'Проверить'", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup
            )
        logger.info(f"Отправлен счет на оплату для user_id={user_id}, invoice_id={invoice_id}")
    except Exception as e:
        logger.error(f"Ошибка при создании счета для user_id={user_id}: {e}")
        await bot.answer_callback_query(call.id, "Ошибка при создании платежа! Попробуйте позже. 🚫")

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
async def check_payment(call):
    user_id = call.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(call.message, call.data)
        return
    invoice_id = call.data.split('_')[1]
    async with aiosqlite.connect('users.db', timeout=10) as db:
        cursor = await db.execute("SELECT user_id, days, amount FROM payments WHERE invoice_id = ?", (invoice_id,))
        payment = await cursor.fetchone()
        if not payment:
            await bot.answer_callback_query(call.id, "Платеж не найден! 🚫")
            return
        user_id, days, amount = payment
        try:
            invoice = crypto.get_invoices(invoice_ids=invoice_id)
            status = invoice['items'][0]['status']
            if status == "paid":
                await db.execute("UPDATE payments SET status = ? WHERE invoice_id = ?", ('paid', invoice_id))
                await db.execute("INSERT OR IGNORE INTO users (user_id, subscribe) VALUES (?, ?)",
                                (user_id, "2000-01-01 00:00:00"))
                cursor = await db.execute("SELECT subscribe FROM users WHERE user_id = ?", (user_id,))
                current_sub = (await cursor.fetchone())[0]
                try:
                    current_date = datetime.strptime(current_sub, "%Y-%m-%d %H:%M:%S")
                    new_date = current_date + timedelta(days=days) if current_date > datetime.now() else datetime.now() + timedelta(days=days)
                except (ValueError, TypeError):
                    new_date = datetime.now() + timedelta(days=days)
                await db.execute("UPDATE users SET subscribe = ? WHERE user_id = ?",
                                (new_date.strftime("%Y-%m-%d %H:%M:%S"), user_id))
                await db.commit()
                with open("vandfreeze.jpg", "rb") as photo:
                    media = types.InputMediaPhoto(photo, caption=f"Оплата подтверждена! 🎉\n\nТип: Подписка\nСумма: {amount}$ 💰", parse_mode="Markdown")
                    await bot.edit_message_media(
                        media=media,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=back_markup
                    )
                await bot.send_message(
                    config.LOG_CHAT,
                    f"Новая оплата 💸\n\nПользователь: `{user_id}`\nСумма: {amount}$\nТип: Подписка на {days} дней\nID платежа: `{invoice_id}`",
                    parse_mode="Markdown"
                )
                logger.info(f"Подтверждена оплата для user_id={user_id}, invoice_id={invoice_id}")
            else:
                await bot.answer_callback_query(call.id, "Оплата не получена! Попробуйте снова. 😔")
        except Exception as e:
            logger.error(f"Ошибка при проверке оплаты для user_id={user_id}, invoice_id={invoice_id}: {e}")
            await bot.answer_callback_query(call.id, "Ошибка при проверке платежа! Попробуйте позже. 🚫")

@bot.callback_query_handler(func=lambda call: call.data == 'regular_freeze')
async def start_freeze(call):
    user_id = call.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(call.message, call.data)
        return
    is_active = await check_subscription(user_id)
    if not is_active:
        await bot.answer_callback_query(call.id, "Ваша подписка не активна! Купите подписку в магазине. 😔")
        return
    if not await can_freeze(user_id):
        await bot.answer_callback_query(call.id, f"Подождите {config.FREEZE_COOLDOWN} секунд перед следующей заморозкой! ⏳")
        return
    user_states[user_id] = {'action': 'awaiting_freeze'}
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption=f"Заморозь ❄️\n\nОтправьте ссылку на пост в формате:\n`https://t.me/username/123`", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Запрошена ссылка для заморозки от user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при запросе ссылки для заморозки для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'promocodes')
async def promocodes(call):
    user_id = call.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(call.message, 'promocodes')
        return
    full_name = f"{call.from_user.first_name} {call.from_user.last_name or ''}".strip()
    if not full_name:
        try:
            with open("vandfreeze.jpg", "rb") as photo:
                media = types.InputMediaPhoto(
                    photo,
                    caption=f"Для доступа к промокодам установите отображаемое имя в Telegram и добавьте в него тег бота: `{config.BOT_TAG}` 🚫\n\nНажмите на `{config.BOT_TAG}` для копирования.",
                    parse_mode="Markdown"
                )
                await bot.edit_message_media(
                    media=media,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=back_markup
                )
            logger.info(f"Пользователь user_id={user_id} не имеет отображаемого имени, запрошен тег {config.BOT_TAG}")
        except Exception as e:
            logger.error(f"Ошибка при запросе промокода для user_id={user_id}: {e}")
        return
    if config.BOT_TAG.lower() not in full_name.lower():
        try:
            with open("vandfreeze.jpg", "rb") as photo:
                media = types.InputMediaPhoto(
                    photo,
                    caption=f"В вашем отображаемом имени отсутствует тег бота: `{config.BOT_TAG}` 🚫\n\nДобавьте `{config.BOT_TAG}` в ваше отображаемое имя в Telegram и попробуйте снова. Нажмите на `{config.BOT_TAG}` для копирования.",
                    parse_mode="Markdown"
                )
                await bot.edit_message_media(
                    media=media,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=back_markup
                )
            logger.info(f"Тег {config.BOT_TAG} отсутствует в отображаемом имени пользователя user_id={user_id}")
        except Exception as e:
            logger.error(f"Ошибка при запросе промокода для user_id={user_id}: {e}")
        return
    try:
        user_states[user_id] = {'action': 'awaiting_promocode'}
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption="Введите промокод 🎟️", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Запрошен промокод от user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при запросе промокода для user_id={user_id}: {e}")

@bot.message_handler(func=lambda message: message.from_user.id in user_states and user_states[message.from_user.id]['action'] == 'awaiting_freeze')
async def process_freeze(message):
    user_id = message.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(message)
        return
    user_states.pop(user_id, None)
    
    logger.info(f"Начало обработки заморозки для user_id={user_id}, ссылка={message.text}")
    
    try:
        processing_msg = await bot.reply_to(
            message,
            f"Заморозка начата ❄️\nЦель: {message.text}\nПроверка ссылки... 🔍",
            parse_mode="Markdown"
        )
        logger.info(f"Отправлено начальное сообщение о заморозке для user_id={user_id}, message_id={processing_msg.message_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке начального сообщения о заморозке для user_id={user_id}: {e}")
        return

    try:
        logger.info(f"Проверка формата ссылки: {message.text}")
        link_pattern = r'^https://t\.me/([A-Za-z0-9_]+)/(\d+)$'
        match = re.match(link_pattern, message.text.strip())
        if not match:
            logger.warning(f"Неверный формат ссылки для user_id={user_id}: {message.text}")
            await bot.edit_message_text(
                "Неверный формат ссылки! Используйте: https://t.me/username/123 🚫",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                parse_mode="Markdown"
            )
            return
        
        target, post_id = match.groups()
        logger.info(f"Ссылка валидна: target={target}, post_id={post_id}")

        logger.info(f"Проверка повторной заморозки в базе данных для user_id={user_id}, цель=https://t.me/{target}/{post_id}")
        async with aiosqlite.connect('users.db', timeout=10) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM freezes WHERE user_id = ? AND target = ?",
                (user_id, f"https://t.me/{target}/{post_id}")
            )
            freeze_count = (await cursor.fetchone())[0]
        logger.info(f"Результат проверки повторной заморозки: freeze_count={freeze_count}")

        if freeze_count > 0:
            logger.warning(f"Обнаружена повторная заморозка для user_id={user_id}, цель=https://t.me/{target}/{post_id}")
            async with aiosqlite.connect('users.db', timeout=10) as db:
                await db.execute(
                    "UPDATE users SET subscribe = ? WHERE user_id = ?",
                    ("2000-01-01 00:00:00", user_id)
                )
                await db.commit()
            await bot.edit_message_text(
                "У вас снялась подписка за нарушение правил нашего мануала, можете прочитать в нашем телеграм канале 📜",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                parse_mode="Markdown"
            )
            await bot.send_message(
                config.LOG_CHAT,
                f"Аннулирование подписки 🚫\n\nПользователь: `{user_id}`\nПовторная заморозка на: `https://t.me/{target}/{post_id}`\nПодписка снята",
                parse_mode="Markdown"
            )
            logger.info(f"Подписка аннулирована для user_id={user_id} из-за повторной заморозки")
            return

        logger.info(f"Обновление времени последней заморозки для user_id={user_id}")
        await update_last_freeze(user_id)
        logger.info(f"Время последней заморозки обновлено")

        logger.info(f"Загрузка сессий для user_id={user_id}")
        sessions = await load_valid_sessions()
        logger.info(f"Загружено {len(sessions)} сессий")
        if not sessions:
            logger.warning(f"Нет доступных сессий для user_id={user_id}")
            await bot.edit_message_text(
                "Нет доступных сессий! Обратитесь к администратору. 😔",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                parse_mode="Markdown"
            )
            return
        
        max_sessions = config.MAX_CONCURRENT_SESSIONS
        max_sessions = min(max_sessions, len(sessions))
        
        logger.info(f"Запуск заморозки: max_sessions={max_sessions}")
        await bot.edit_message_text(
            f"Заморозка запущена ❄️\nЦель: {target}/{post_id}\nСессий: {len(sessions)} 📄\nОбработано: 0/{len(sessions)} сессий",
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            parse_mode="Markdown"
        )
        
        await run_freeze(user_id, target, post_id, processing_msg.message_id)
            
    except PeerIdInvalidError:
        logger.error(f"Не удалось получить сущность для цели={target} для user_id={user_id}")
        await bot.edit_message_text(
            "Не удалось найти username или ID чата! Проверьте корректность ссылки. 🚫",
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка в process_freeze для user_id={user_id}: {e}", exc_info=True)
        try:
            await bot.edit_message_text(
                f"Ошибка при запуске заморозки: {str(e).replace('`', '')[:100]} 🚫",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                parse_mode="Markdown"
            )
        except Exception as edit_error:
            logger.error(f"Ошибка при отправке сообщения об ошибке для user_id={user_id}: {edit_error}")

@bot.callback_query_handler(func=lambda call: call.data == 'back_to_menu')
async def back_to_menu(call):
    user_id = call.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(call.message, 'menu')
        return
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption=f"Vand Freeze — Главное меню 🎉\n\nДобро пожаловать!", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_menu
            )
        logger.info(f"Возврат в главное меню для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при возврате в главное меню для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'admin_set_delays')
async def admin_set_delays(call):
    user_id = call.from_user.id
    if user_id not in config.ADMINS:
        await bot.answer_callback_query(call.id, "У вас нет доступа! 🚫")
        return
    user_states[user_id] = {'action': 'admin_set_delays'}
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption=f"Настройка задержек ⏱️\n\nТекущие настройки:\nЗаморозка: {config.FREEZE_DELAY['min']}–{config.FREEZE_DELAY['max']} сек\nЗадержка между заморозками: {config.FREEZE_COOLDOWN} сек\n\nВведите тип задержки и значения в формате:\n`freeze min max` или `cooldown seconds`\nПример: `freeze 0.5 1.5` или `cooldown 60`", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Запрошены настройки задержек от user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при запросе настроек задержек для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'admin_broadcast')
async def admin_broadcast(call):
    user_id = call.from_user.id
    if user_id not in config.ADMINS:
        await bot.answer_callback_query(call.id, "У вас нет доступа! 🚫")
        return
    user_states[user_id] = {'action': 'admin_broadcast'}
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption="Рассылка 📬\n\nВведите сообщение для рассылки:", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Запрошено сообщение для рассылки от user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при запросе сообщения для рассылки для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'admin_give_sub')
async def admin_give_sub(call):
    user_id = call.from_user.id
    if user_id not in config.ADMINS:
        await bot.answer_callback_query(call.id, "У вас нет доступа! 🚫")
        return
    user_states[user_id] = {'action': 'admin_give_sub'}
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption="Выдача подписки 🎁\n\nВведите ID пользователя и количество дней подписки (через пробел):\nПример: `123456789 30`", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Запрошен ID и срок подписки для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при запросе ID для выдачи подписки для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'admin_remove_sub')
async def admin_remove_sub(call):
    user_id = call.from_user.id
    if user_id not in config.ADMINS:
        await bot.answer_callback_query(call.id, "У вас нет доступа! 🚫")
        return
    user_states[user_id] = {'action': 'admin_remove_sub'}
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption="Сброс подписки 🗑️\n\nВведите ID пользователя:", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Запрошен ID для сброса подписки для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при запросе ID для сброса подписки для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'admin_create_promo')
async def admin_create_promo(call):
    user_id = call.from_user.id
    if user_id not in config.ADMINS:
        await bot.answer_callback_query(call.id, "У вас нет доступа! 🚫")
        return
    user_states[user_id] = {'action': 'admin_create_promo'}
    try:
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption="Создание промокода 🎟️\n\nВведите промокод, количество использований и дней (через пробел):\nПример: `ABC123 10 30`", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Запрошены данные для создания промокода от user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при запросе данных для промокода для user_id={user_id}: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'admin_stats')
async def admin_stats(call):
    user_id = call.from_user.id
    if user_id not in config.ADMINS:
        await bot.answer_callback_query(call.id, "У вас нет доступа к статистике! 🚫")
        return
    try:
        total_users = await count_users()
        subscribed_users = await count_subscribed_users()
        successful_freezes, failed_freezes = await count_freezes()
        
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption=f"Статистика бота 📊\n\nВсего пользователей: {total_users} 👥\nПользователей с подпиской: {subscribed_users} ✅\nУспешных заморозок: {successful_freezes} ❄️\nНеуспешных заморозок: {failed_freezes} 🚫", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Отправлена статистика для администратора user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке статистики для user_id={user_id}: {e}")
        await bot.answer_callback_query(call.id, "Ошибка при получении статистики! 🚫")

@bot.callback_query_handler(func=lambda call: call.data == 'admin_sessions')
async def admin_sessions(call):
    user_id = call.from_user.id
    if user_id not in config.ADMINS:
        await bot.answer_callback_query(call.id, "У вас нет доступа к информации о сессиях! 🚫")
        return
    try:
        sessions = await load_valid_sessions()
        total_sessions = len(os.listdir(config.SESSIONS_DIR)) if os.path.exists(config.SESSIONS_DIR) else 0
        valid_sessions = len(sessions)
        invalid_sessions = total_sessions - valid_sessions
        
        with open("vandfreeze.jpg", "rb") as photo:
            media = types.InputMediaPhoto(photo, caption=f"Статистика сессий 🔗\n\nВсего файлов сессий: {total_sessions} 📄\nРабочих сессий: {valid_sessions} ✅\nНерабочих сессий: {invalid_sessions} 🚫\nМаксимум одновременных сессий: {config.MAX_CONCURRENT_SESSIONS}", parse_mode="Markdown")
            await bot.edit_message_media(
                media=media,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_markup
            )
        logger.info(f"Отправлена статистика сессий для администратора user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке статистики сессий для user_id={user_id}: {e}")
        await bot.answer_callback_query(call.id, "Ошибка при получении статистики сессий! 🚫")

@bot.message_handler(func=lambda message: message.from_user.id in user_states and user_states[message.from_user.id]['action'] in ['admin_broadcast', 'admin_give_sub', 'admin_remove_sub', 'admin_set_delays', 'admin_create_promo', 'awaiting_promocode'])
async def process_admin_action(message):
    user_id = message.from_user.id
    action = user_states.get(user_id, {}).get('action')
    if action is None:
        return
    if action.startswith('admin_') and user_id not in config.ADMINS:
        await bot.reply_to(message, "У вас нет доступа! 🚫")
        return
    user_states.pop(user_id, None)
    
    try:
        if action == 'admin_broadcast':
            async with aiosqlite.connect('users.db', timeout=10) as db:
                cursor = await db.execute("SELECT user_id FROM users")
                users = await cursor.fetchall()
            successful_broadcasts = 0
            for user in users:
                try:
                    await bot.send_message(user[0], message.text, parse_mode="Markdown")
                    successful_broadcasts += 1
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Ошибка при отправке сообщения пользователю user_id={user[0]}: {e}")
            await bot.reply_to(message, f"Рассылка отправлена {successful_broadcasts} пользователям 📬")
            logger.info(f"Рассылка выполнена для {successful_broadcasts} пользователей админом user_id={user_id}")
        
        elif action == 'admin_give_sub':
            target_id, days = map(int, message.text.strip().split())
            new_date = datetime.now() + timedelta(days=days)
            async with aiosqlite.connect('users.db', timeout=10) as db:
                await db.execute("INSERT OR IGNORE INTO users (user_id, subscribe) VALUES (?, ?)",
                                (target_id, "2000-01-01 00:00:00"))
                await db.execute("UPDATE users SET subscribe = ? WHERE user_id = ?",
                                (new_date.strftime("%Y-%m-%d %H:%M:%S"), target_id))
                await db.commit()
            await bot.reply_to(message, f"Подписка на {days} дней выдана пользователю `{target_id}` 🎁", parse_mode="Markdown")
            logger.info(f"Подписка на {days} дней выдана пользователю user_id={target_id} админом user_id={user_id}")
        
        elif action == 'admin_remove_sub':
            target_id = int(message.text.strip())
            async with aiosqlite.connect('users.db', timeout=10) as db:
                await db.execute("UPDATE users SET subscribe = ? WHERE user_id = ?",
                                ("2000-01-01 00:00:00", target_id))
                await db.commit()
            await bot.reply_to(message, f"Подписка сброшена для пользователя `{target_id}` 🗑️", parse_mode="Markdown")
            logger.info(f"Подписка сброшена для user_id={target_id} админом user_id={user_id}")
        
        elif action == 'admin_set_delays':
            parts = message.text.strip().split()
            if parts[0] == 'cooldown':
                if len(parts) != 2:
                    await bot.reply_to(message, "Неверный формат! Используйте: `cooldown seconds` 🚫")
                    logger.error(f"Неверный формат ввода для {action} пользователем user_id={user_id}: {message.text}")
                    return
                duration = int(parts[1])
                if duration < 0 or duration > 3600:
                    await bot.reply_to(message, "Задержка должна быть в диапазоне 0–3600 секунд! 🚫")
                    logger.error(f"Недопустимое значение задержки для user_id={user_id}: duration={duration}")
                    return
                async with aiosqlite.connect('users.db', timeout=10) as db:
                    await db.execute("UPDATE settings SET freeze_cooldown = ? WHERE id = ?", (duration, 1))
                    await db.commit()
                config.FREEZE_COOLDOWN = duration
                await bot.reply_to(message, f"Задержка между задачами установлена: {duration} сек ⏱️")
                await bot.send_message(
                    config.LOG_CHAT,
                    f"Обновлена задержка между задачами ⏱️\n\nАдмин: `{user_id}`\nЗадержка: {duration} сек",
                    parse_mode="Markdown"
                )
                logger.info(f"Обновлена задержка между задачами на {duration} админом user_id={user_id}")
            elif parts[0] == 'freeze':
                if len(parts) != 3:
                    await bot.reply_to(message, "Неверный формат! Используйте: `freeze min max` 🚫")
                    logger.error(f"Неверный формат задержки для user_id={user_id}: {message.text}")
                    return
                min_duration, max_duration = float(parts[1]), float(parts[2])
                if min_duration < 0 or max_duration < min_duration or max_duration > 10:
                    await bot.reply_to(message, "Задержки должны быть в диапазоне 0–10 сек, и min <= max! 🚫")
                    logger.error(f"Недопустимые значения задержки для user_id={user_id}: min={min_duration}, max={max_duration}")
                    return
                
                async with aiosqlite.connect('users.db', timeout=10) as db:
                    await db.execute(
                        "UPDATE settings SET freeze_min_delay = ?, freeze_max_delay = ? WHERE id = ?",
                        (min_duration, max_duration, 1)
                    )
                    await db.commit()
                
                config.FREEZE_DELAY['min'] = min_duration
                config.FREEZE_DELAY['max'] = max_duration
                await bot.reply_to(
                    message,
                    f"Задержки для заморозки установлены: {min_duration}–{max_duration} сек ⏱️",
                    parse_mode="Markdown"
                )
                await bot.send_message(
                    config.LOG_CHAT,
                    f"Обновлены задержки заморозки ❄️\n\nАдмин: `{user_id}`\nТип: Заморозка\nДиапазон: {min_duration}–{max_duration} сек",
                    parse_mode="Markdown"
                )
                logger.info(f"Обновлены задержки заморозки на {min_duration}–{max_duration} сек админом user_id={user_id}")
            else:
                await bot.reply_to(message, "Неверный тип задержки! Используйте: `freeze` или `cooldown` 🚫")
                logger.error(f"Неверный тип задержки для user_id={user_id}: {message.text}")
        elif action == 'admin_create_promo':
            parts = message.text.strip().split()
            if len(parts) != 3:
                await bot.reply_to(message, "Неверный формат! Используйте: `промокод uses days` 🚫")
                return
            code, uses_left, days = parts[0], int(parts[1]), int(parts[2])
            async with aiosqlite.connect('users.db', timeout=10) as db:
                await db.execute("INSERT OR REPLACE INTO promocodes (code, uses_left, days) VALUES (?, ?, ?)",
                                (code, uses_left, days))
                await db.commit()
            promocodeki[code] = {'uses_left': uses_left, 'days': days}
            await bot.reply_to(message, f"Промокод `{code}` создан: {uses_left} использований, {days} дней 🎟️")
            logger.info(f"Промокод {code} создан админом user_id={user_id}")
        elif action == 'awaiting_promocode':
            full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
            if not full_name:
                await bot.reply_to(
                    message,
                    f"Для доступа к промокодам установите отображаемое имя в Telegram и добавьте в него тег бота: `{config.BOT_TAG}` 🚫\n\nНажмите на `{config.BOT_TAG}` для копирования.",
                    parse_mode="Markdown"
                )
                logger.info(f"Пользователь user_id={user_id} не имеет отображаемого имени, запрошен тег {config.BOT_TAG}")
                return
            if config.BOT_TAG.lower() not in full_name.lower():
                await bot.reply_to(
                    message,
                    f"В вашем отображаемом имени отсутствует тег бота: `{config.BOT_TAG}` 🚫\n\nДобавьте `{config.BOT_TAG}` в ваше отображаемое имя в Telegram и попробуйте снова. Нажмите на `{config.BOT_TAG}` для копирования.",
                    parse_mode="Markdown"
                )
                logger.info(f"Тег {config.BOT_TAG} отсутствует в отображаемом имени пользователя user_id={user_id}")
                return
            code = message.text.strip()
            async with aiosqlite.connect('users.db', timeout=10) as db:
                cursor = await db.execute("SELECT uses_left, days FROM promocodes WHERE code = ?", (code,))
                result = await cursor.fetchone()
                if not result or result[0] <= 0:
                    await bot.reply_to(message, "Неверный или исчерпанный промокод! 🚫")
                    return
                uses_left, days = result
                new_uses = uses_left - 1
                await db.execute("UPDATE promocodes SET uses_left = ? WHERE code = ?", (new_uses, code))
                await db.execute("INSERT OR IGNORE INTO users (user_id, subscribe) VALUES (?, ?)",
                                (user_id, "2000-01-01 00:00:00"))
                cursor = await db.execute("SELECT subscribe FROM users WHERE user_id = ?", (user_id,))
                current_sub = (await cursor.fetchone())[0]
                try:
                    current_date = datetime.strptime(current_sub, "%Y-%m-%d %H:%M:%S")
                    new_date = current_date + timedelta(days=days) if current_date > datetime.now() else datetime.now() + timedelta(days=days)
                except (ValueError, TypeError):
                    new_date = datetime.now() + timedelta(days=days)
                await db.execute("UPDATE users SET subscribe = ? WHERE user_id = ?",
                                (new_date.strftime("%Y-%m-%d %H:%M:%S"), user_id))
                await db.commit()
            username = message.from_user.username  # Оставляем username для лога, если нужно
            await bot.reply_to(message, f"Промокод активирован! 🎉 Подписка на {days} дней добавлена.", parse_mode="Markdown")
            await bot.send_message(
                config.LOG_CHAT,
                f"Промокод активирован 🎟️\n\nПользователь: `{user_id}` ({username or '@None'})\nКод: `{code}`\nДней: {days}\nОсталось использований: {new_uses}",
                parse_mode="Markdown"
            )
            logger.info(f"Промокод {code} активирован пользователем user_id={user_id}, добавлено {days} дней подписки")
    except Exception as e:
        logger.error(f"Ошибка при обработке действия {action} для user_id={user_id}: {e}")
        await bot.reply_to(message, f"Ошибка при обработке: {str(e).replace('`', '')[:100]} 🚫", parse_mode="Markdown")

@bot.message_handler(content_types=['text'])
async def handle_text(message):
    user_id = message.from_user.id
    if not await check_channel_subscription(user_id):
        await prompt_subscription(message)
        return
    if user_id in user_states and user_states[user_id]['action'] == 'awaiting_freeze':
        await process_freeze(message)
    elif user_id in user_states and user_states[user_id]['action'] == 'awaiting_promocode':
        await process_admin_action(message)
    else:
        await bot.reply_to(message, "Используйте кнопки меню для взаимодействия с ботом! 📋", parse_mode="Markdown")

async def main():
    await init_db()
    await load_freeze_delays()
    logger.info("Бот запущен 🚀")
    try:
        await bot.polling(none_stop=True, interval=0)
    except Exception as e:
        logger.error(f"Ошибка в polling: {e}")
        await asyncio.sleep(5)
        await main()

if __name__ == '__main__':
    asyncio.run(main())
