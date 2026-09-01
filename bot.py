"""Точка входа Telegram-бота для генерации презентаций."""

from __future__ import annotations

import asyncio
import sys

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.handlers import photos as photos_module
from app.handlers import start as start_module
from app.handlers import topic as topic_module
from app.handlers.fsm import State
from app.services.ollama_client import OllamaClient
from app.storage.sqlite import SQLiteStorage
from app.utils.logging import logger, setup_logging
from config import settings


async def main() -> None:
    setup_logging(level=settings.log_level)

    storage = SQLiteStorage(settings.sqlite_path, tz_name=settings.tz)
    await storage.init()

    # Очищаем зависшие генерации и FSM-стейты после перезапуска бота,
    # чтобы пользователи не застряли в состоянии «генерирую».
    cleaned = await storage.cleanup_stale_fsm_states()
    if cleaned:
        logger.info("Cleaned up {} abandoned states/generations", cleaned)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    ollama = OllamaClient(
        api_key=settings.ollama_api_key,
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        timeout=settings.ollama_timeout,
    )
    photos_module.bind_runtime(bot, storage)

    dp = Dispatcher()
    dp.workflow_data.update(storage=storage, bot=bot, ollama=ollama)

    # У нас FSM-стейт живёт в собственном SQLite (см. SQLiteStorage).
    # Встроенный aiogram-овский FSM-мидлварь смотрит в свой MemoryStorage и
    # кладёт в data["raw_state"] то, что найдёт там — то есть None, потому что
    # мы туда ничего не пишем. Из-за этого любой @router.message(StateFilter(...))
    # всегда видит raw_state=None и отвергает сообщение.
    #
    # Решение: наш middleware перезаписывает data["state"] и data["raw_state"]
    # значениями из SQLite ДО того, как обработчики/фильтры их прочитают.
    # Тем самым StateFilter начинает видеть актуальное состояние.
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.types import CallbackQuery

    def _build_fsm_context(user_id: int, chat_id: int) -> FSMContext:
        # Ключ должен совпадать с тем, что использует встроенный FSM
        # middleware (USER_IN_CHAT стратегия), иначе StateFilter будет
        # сравнивать с состоянием, которое лежит в чужом ключе.
        return FSMContext(
            storage=dp.fsm.storage,
            key=StorageKey(
                bot_id=bot.id,
                chat_id=chat_id,
                user_id=user_id,
            ),
        )

    def _resolve_chat_id(event, fallback_user_id: int) -> int:
        """Возвращает chat_id для Message и для CallbackQuery единообразно.

        Message всегда имеет .chat; CallbackQuery — .message.chat, но не
        имеет собственного .chat (getattr вернёт None, и сработает fallback
        на user.id — для лички это эквивалент chat.id).
        """
        if isinstance(event, CallbackQuery):
            inner = getattr(event, "message", None)
            chat = getattr(inner, "chat", None) if inner is not None else None
        else:
            chat = getattr(event, "chat", None)
        return getattr(chat, "id", None) or fallback_user_id

    async def fsm_middleware(handler, event, data):
        # В aiogram 3 объект пользователя доступен напрямую в событии (event)
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        chat_id = _resolve_chat_id(event, user.id)
        st_name, st_data = await storage.get_state(user.id)

        # Если в БД ничего нет — пользователь в IDLE, явно сбрасываем.
        if not st_name or st_name == State.IDLE:
            st_name = None
            st_data = {}

        # Синхронизируем состояние с внутренним FSM aiogram,
        # чтобы StateFilter в хэндлерах работал корректно.
        data["fsm_context"] = _build_fsm_context(user.id, chat_id)
        data["raw_state"] = st_name

        event_kind = type(event).__name__
        logger.debug(
            "fsm_middleware user={} kind={} state={!r} text={!r}",
            user.id, event_kind, st_name,
            getattr(event, "text", None) or getattr(event, "data", None),
        )
        return await handler(event, data)

    dp.message.outer_middleware(fsm_middleware)
    dp.callback_query.outer_middleware(fsm_middleware)

    # Запускаем фоновую очистку временных буферов альбомов
    asyncio.create_task(photos_module.cleanup_abandoned_albums())

    # Регистрируем роутеры. Fallback должен быть последним, чтобы он
    # срабатывал только если ни один из «прикладных» хэндлеров не подошёл.
    # ВАЖНО: catch-all нельзя вешать прямо на dp.message() — это
    # регистрирует наблюдатель на самом Dispatcher параллельно с роутерами,
    # и тогда он перехватывает даже /start. Поэтому выносим его в отдельный
    # роутер.
    fallback_router = Router(name="fallback")

    @fallback_router.message()
    async def _fallback(message) -> None:
        if (message.text or "") == "/cancel":
            return
        logger.debug(
            "fallback hit: user={} text={!r}",
            message.from_user.id if message.from_user else None,
            message.text,
        )
        await message.answer(
            "Я не понимаю. Нажмите /start, чтобы начать сценарий."
        )

    dp.include_router(start_module.router)
    dp.include_router(topic_module.router)
    dp.include_router(photos_module.router)
    dp.include_router(fallback_router)

    logger.info(
        "Bot starting | model={} limit={}/day tz={}",
        settings.ollama_model,
        settings.daily_generation_limit,
        settings.tz,
    )
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        sys.exit(0)