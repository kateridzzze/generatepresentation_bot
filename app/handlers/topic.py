"""Приём темы и числа слайдов."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message

from app.handlers.fsm import State
from app.storage.sqlite import SQLiteStorage
from app.utils.logging import logger
from app.utils.validators import (
    ValidationError,
    validate_slides_count,
    validate_topic,
)
from config import settings

router = Router(name="topic")


@router.message(StateFilter(State.WAITING_TOPIC), F.text)
async def on_topic(message: Message, storage: SQLiteStorage) -> None:
    user_id = message.from_user.id if message.from_user else 0
    text = message.text or ""

    try:
        topic = validate_topic(text)
    except ValidationError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await storage.set_state(
        user_id,
        State.WAITING_SLIDES_COUNT,
        {"topic": topic, "photos": []},
    )
    logger.info("user {} topic accepted: {}", user_id, topic[:60])

    await message.answer(
        f"📝 Тема: <b>{topic}</b>\n\n"
        f"Сколько слайдов сделать? От {settings.min_slides} до {settings.max_slides} "
        f"(или напишите <b>«по умолчанию»</b> — будет "
        f"{settings.default_slides_min}–{settings.default_slides_max})."
    )


@router.message(StateFilter(State.WAITING_SLIDES_COUNT), F.text)
async def on_slides_count(message: Message, storage: SQLiteStorage) -> None:
    user_id = message.from_user.id if message.from_user else 0
    _, data = await storage.get_state(user_id)
    topic = data.get("topic", "Презентация")

    try:
        n = validate_slides_count(
            message.text or "",
            settings.default_slides_min,
            settings.default_slides_max,
            settings.min_slides,
            settings.max_slides,
        )
    except ValidationError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await storage.set_state(
        user_id,
        State.WAITING_PHOTOS,
        {**data, "slides_n": n, "photos": []},
    )
    logger.info("user {} slides_n={}", user_id, n)

    await message.answer(
        f"📊 Принято: {n} слайдов.\n\n"
        f"📷 Пришлите до {settings.max_photos} фотографий для презентации.\n"
        "Можно по одной или альбомом.\n"
        "Если фото не нужны — напишите <b>«без фото»</b>.\n"
        "Когда закончите — напишите <b>«готово»</b>."
    )