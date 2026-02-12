import os
import sqlite3
import datetime as dt

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN")
DB = "students.db"

ASK_PAYDAY = 0

def db_init():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students(
            chat_id INTEGER PRIMARY KEY,
            pay_day INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    con.commit()
    con.close()

def db_set_payday(chat_id: int, pay_day: int):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO students(chat_id, pay_day, active)
        VALUES(?, ?, 1)
        ON CONFLICT(chat_id) DO UPDATE SET
            pay_day=excluded.pay_day,
            active=1
    """, (chat_id, pay_day))
    con.commit()
    con.close()

def db_get_students():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT chat_id, pay_day FROM students WHERE active=1")
    rows = cur.fetchall()
    con.close()
    return rows

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здравствуйте!\nВведите день оплаты (1–28):"
    )
    return ASK_PAYDAY

async def save_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Введите число 1–28.")
        return ASK_PAYDAY

    pay_day = int(text)
    if pay_day < 1 or pay_day > 28:
        await update.message.reply_text("Введите число от 1 до 28.")
        return ASK_PAYDAY

    chat_id = update.effective_chat.id
    db_set_payday(chat_id, pay_day)

    await update.message.reply_text(
        f"Готово ✅\n"
        f"Я буду присылать напоминание за 1 день до оплаты."
    )

    return ConversationHandler.END

async def daily_check(app: Application):
    today = dt.date.today()
    tomorrow = today + dt.timedelta(days=1)

    for chat_id, pay_day in db_get_students():
        if pay_day == tomorrow.day:
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text="Напоминаем: завтра оплата курса. Пожалуйста, оплатите."
                )
            except:
                pass

async def on_startup(app: Application):
    db_init()
    scheduler = AsyncIOScheduler(timezone="Asia/Bishkek")
    scheduler.add_job(daily_check, "cron", hour=10, minute=0, args=[app])
    scheduler.start()

def main():
    app = Application.builder().token(TOKEN).post_init(on_startup).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_PAYDAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_day)]
        },
        fallbacks=[],
    )

    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__":
    main()
