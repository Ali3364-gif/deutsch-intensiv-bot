import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКИ ==================
DB = "students.db"
TOKEN = os.getenv("BOT_TOKEN")  # Railway Variables -> BOT_TOKEN
TZ = ZoneInfo("Asia/Bishkek")   # Бишкек

# Во сколько отправлять ежедневную проверку (по Бишкеку)
REMINDER_HOUR = 10
REMINDER_MINUTE = 0

# ================== ЛОГИ ==================
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("pay-reminder-bot")


# ================== БАЗА ==================
def db_init():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students(
            chat_id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL,
            start_date TEXT NOT NULL,  -- YYYY-MM-DD
            pay_day INTEGER NOT NULL,  -- 1..28
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def upsert_student(chat_id: int, full_name: str, start_date_iso: str, pay_day: int, active: int = 1):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        INSERT INTO students(chat_id, full_name, start_date, pay_day, active, created_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(chat_id) DO UPDATE SET
            full_name=excluded.full_name,
            start_date=excluded.start_date,
            pay_day=excluded.pay_day,
            active=excluded.active
    """, (chat_id, full_name, start_date_iso, pay_day, active, now))
    con.commit()
    con.close()


def set_active(chat_id: int, active: int):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("UPDATE students SET active=? WHERE chat_id=?", (active, chat_id))
    con.commit()
    con.close()


def update_name(chat_id: int, full_name: str):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("UPDATE students SET full_name=? WHERE chat_id=?", (full_name, chat_id))
    con.commit()
    con.close()


def update_payday(chat_id: int, pay_day: int):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("UPDATE students SET pay_day=? WHERE chat_id=?", (pay_day, chat_id))
    con.commit()
    con.close()


def update_start(chat_id: int, start_date_iso: str, pay_day: int):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("UPDATE students SET start_date=?, pay_day=? WHERE chat_id=?", (start_date_iso, pay_day, chat_id))
    con.commit()
    con.close()


def get_student(chat_id: int):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT chat_id, full_name, start_date, pay_day, active FROM students WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    con.close()
    return row


def get_active_students():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT chat_id, full_name, pay_day FROM students WHERE active=1")
    rows = cur.fetchall()
    con.close()
    return rows


# ================== ПАРСИНГ ДАТ ==================
def parse_ddmmyyyy(text: str) -> str | None:
    # Возвращает ISO YYYY-MM-DD или None
    text = text.strip()
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if not m:
        return None
    dd, mm, yyyy = map(int, m.groups())
    try:
        d = datetime(yyyy, mm, dd)
    except ValueError:
        return None
    return d.strftime("%Y-%m-%d")


def clamp_pay_day(day: int) -> int:
    # Надёжно для всех месяцев: 1..28
    if day < 1:
        return 1
    if day > 28:
        return 28
    return day


# ================== ДИАЛОГ РЕГИСТРАЦИИ ==================
ASK_NAME, ASK_START = range(2)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Давай зарегистрируемся.\n\nВведите *Фамилия Имя* (пример: Абдурахимов Абдулбори).", parse_mode="Markdown")
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_name = (update.message.text or "").strip()
    if len(full_name) < 3 or " " not in full_name:
        await update.message.reply_text("Пожалуйста, введи *Фамилия Имя* одной строкой.\nПример: Иванов Иван", parse_mode="Markdown")
        return ASK_NAME

    context.user_data["full_name"] = full_name
    await update.message.reply_text("Отлично. Теперь введи *дату начала* в формате ДД.ММ.ГГГГ.\nПример: 18.09.2025", parse_mode="Markdown")
    return ASK_START


async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    iso = parse_ddmmyyyy(update.message.text or "")
    if not iso:
        await update.message.reply_text("Неверный формат даты.\nНужно так: *ДД.ММ.ГГГГ*\nПример: 18.09.2025", parse_mode="Markdown")
        return ASK_START

    # pay_day = день(start_date), но ограничиваем 1..28 (стабильно для любых месяцев)
    dd = int(iso[-2:])
    pay_day = clamp_pay_day(dd)

    chat_id = update.message.chat.id
    full_name = context.user_data["full_name"]

    upsert_student(chat_id, full_name, iso, pay_day, active=1)

    await update.message.reply_text(
        "Готово ✅\n"
        f"Имя: {full_name}\n"
        f"Дата начала: {iso}\n"
        f"День оплаты: {pay_day}\n\n"
        "Бот будет напоминать *за 1 день* до оплаты.\n\n"
        "Команды:\n"
        "/setday 18 — изменить день оплаты\n"
        "/setname Фамилия Имя — изменить имя\n"
        "/setstart 18.09.2025 — изменить дату начала (и пересчитать день оплаты)\n"
        "/stop — отключить напоминания\n"
        "/resume — включить обратно",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ок, регистрацию отменил. Напиши /start чтобы начать заново.")
    return ConversationHandler.END


# ================== КОМАНДЫ ИЗМЕНЕНИЯ ==================
async def setday_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    st = get_student(chat_id)
    if not st:
        await update.message.reply_text("Сначала зарегистрируйся: /start")
        return

    if not context.args:
        await update.message.reply_text("Используй: /setday 1..28\nПример: /setday 18")
        return

    try:
        day = int(context.args[0])
    except ValueError:
        await update.message.reply_text("День должен быть числом. Пример: /setday 18")
        return

    if day < 1 or day > 28:
        await update.message.reply_text("День оплаты должен быть 1..28 (так надёжнее для всех месяцев).")
        return

    update_payday(chat_id, day)
    await update.message.reply_text(f"✅ День оплаты обновлён: {day}")


async def setname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    st = get_student(chat_id)
    if not st:
        await update.message.reply_text("Сначала зарегистрируйся: /start")
        return

    name = " ".join(context.args).strip()
    if not name or " " not in name:
        await update.message.reply_text("Используй: /setname Фамилия Имя\nПример: /setname Абдурахимов Абдулбори")
        return

    update_name(chat_id, name)
    await update.message.reply_text(f"✅ Имя обновлено: {name}")


async def setstart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    st = get_student(chat_id)
    if not st:
        await update.message.reply_text("Сначала зарегистрируйся: /start")
        return

    if not context.args:
        await update.message.reply_text("Используй: /setstart ДД.ММ.ГГГГ\nПример: /setstart 18.09.2025")
        return

    iso = parse_ddmmyyyy(context.args[0])
    if not iso:
        await update.message.reply_text("Неверная дата. Пример: /setstart 18.09.2025")
        return

    dd = int(iso[-2:])
    pay_day = clamp_pay_day(dd)

    update_start(chat_id, iso, pay_day)
    await update.message.reply_text(f"✅ Дата начала обновлена: {iso}\n✅ День оплаты пересчитан: {pay_day}")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    st = get_student(chat_id)
    if not st:
        await update.message.reply_text("Сначала зарегистрируйся: /start")
        return
    set_active(chat_id, 0)
    await update.message.reply_text("⛔ Напоминания отключены. Включить снова: /resume")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    st = get_student(chat_id)
    if not st:
        await update.message.reply_text("Сначала зарегистрируйся: /start")
        return
    set_active(chat_id, 1)
    await update.message.reply_text("✅ Напоминания включены.")


# ================== ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ ==================
async def daily_check(app: Application):
    """
    Каждый день в одно и то же время:
    tomorrow_day = (сегодня + 1).day
    если tomorrow_day == pay_day -> отправляем
    """
    now = datetime.now(TZ)
    tomorrow = now + timedelta(days=1)
    tomorrow_day = tomorrow.day

    # мы храним pay_day 1..28 (стабильно для всех месяцев)
    rows = get_active_students()
    sent = 0

    for chat_id, full_name, pay_day in rows:
        try:
            pay_day = int(pay_day)
        except Exception:
            continue

        if tomorrow_day == pay_day:
            text = (
                f"Уважаемый(ая) {full_name},\n"
                f"завтра ({tomorrow.strftime('%d.%m.%Y')}) день оплаты.\n"
                "Пожалуйста, оплатите за курс."
            )
            try:
                await app.bot.send_message(chat_id=chat_id, text=text)
                sent += 1
            except Exception as e:
                log.warning("Send failed to %s: %s", chat_id, e)

    log.info("Daily check done. Tomorrow_day=%s, sent=%s", tomorrow_day, sent)


def setup_scheduler(app: Application):
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        lambda: daily_check(app),
        trigger=CronTrigger(hour=REMINDER_HOUR, minute=REMINDER_MINUTE, timezone=TZ),
        id="daily_reminder",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler started: daily at %02d:%02d %s", REMINDER_HOUR, REMINDER_MINUTE, TZ)


async def post_init(app: Application):
    # запускается 1 раз при старте бота
    setup_scheduler(app)


# ================== MAIN ==================
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Добавь Railway Variables -> BOT_TOKEN")

    db_init()

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start_cmd)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_start)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("setday", setday_cmd))
    app.add_handler(CommandHandler("setname", setname_cmd))
    app.add_handler(CommandHandler("setstart", setstart_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))

    # polling (не webhook)
    log.info("Bot starting (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
