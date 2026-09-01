"""/start, /help, /cancel."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.handlers.fsm import State
from app.storage.sqlite import SQLiteStorage
from app.utils.logging import logger

router = Router(name="start")


@router.message(Command("start"))
async def cmd_start(message: Message, storage: SQLiteStorage) -> None:
    user_id = message.from_user.id if message.from_user else 0
    username = message.from_user.username if message.from_user else None
    await storage.touch_user(user_id, username)
    await storage.clear_state(user_id)

    text = (
        "👋 Привет! Я — <b>Автогенератор презентаций</b>.\n\n"
        "За несколько шагов соберу для тебя готовую презентацию в формате .pptx.\n\n"
        "Что я сделаю:\n"
        "1️⃣ Спрошу тему\n"
        "2️⃣ Уточню количество слайдов (по умолчанию 8–10)\n"
        "3️⃣ Приму до 7 фотографий (или можно без них)\n"
        "4️⃣ Сгенерирую структуру и пришлю готовый файл\n\n"
        "Команды:\n"
        "/start — начать сценарий\n"
        "/help — справка\n"
        "/cancel — прервать сценарий\n\n"
        "Пришли тему презентации текстом 👇"
    )
    await message.answer(text)
    await storage.set_state(user_id, State.WAITING_TOPIC)
    logger.info("user {} entered WAITING_TOPIC", user_id)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "ℹ️ <b>Справка</b>\n\n"
        "• /start — начать новый сценарий\n"
        "• /cancel — прервать текущий сценарий\n\n"
        "Лимиты:\n"
        "• до 2 презентаций в сутки\n"
        "• от 3 до 25 слайдов\n"
        "• до 7 фотографий\n\n"
        "Фото можно отправить по одному или альбомом (до 10 в одном сообщении)."
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, storage: SQLiteStorage) -> None:
    user_id = message.from_user.id if message.from_user else 0
    await storage.clear_state(user_id)
    await message.answer("↩️ Сценарий прерван. Нажмите /start, чтобы начать заново.")
    logger.info("user {} cancelled", user_id)