"""Приём фото (одиночных и альбомов), сборка и отправка презентации."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    FSInputFile,
)

from app.handlers.fsm import State
from app.services.ollama_client import OllamaClient, OllamaError
from app.services.pptx_builder import build_pptx, make_text_file
from app.storage.sqlite import SQLiteStorage
from app.utils.logging import logger
from app.utils.validators import normalize_photos_text
from config import settings

router = Router(name="photos")

# Простейший in-memory «кэш» собираемых альбомов по media_group_id
_album_buffers: dict[str, dict[str, Any]] = {}
_album_locks: dict[str, asyncio.Lock] = {}


def _kb_done_skip() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Готово", callback_data="photos:done"),
                InlineKeyboardButton(text="⏭ Без фото", callback_data="photos:skip"),
            ]
        ]
    )


def _kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Генерация", callback_data="gen:go"),
                InlineKeyboardButton(text="🔁 Изменить", callback_data="gen:edit"),
            ]
        ]
    )


async def _persist_photo(bot: Bot, message: Message, user_id: int) -> tuple[Path, int] | None:
    """Скачивает фото и возвращает (локальный_путь, размер_в_байтах) или None."""
    if not message.photo:
        return None
    biggest = message.photo[-1]  # самое большое превью
    file_size = biggest.file_size or 0
    max_bytes = settings.max_photo_size_mb * 1024 * 1024
    if file_size > max_bytes:
        return None

    file = await bot.get_file(biggest.file_id)
    target_dir = Path(settings.tmp_dir) / "photos" / str(user_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{int(time.time() * 1000)}_{biggest.file_unique_id}.jpg"
    await bot.download_file(file.file_path, destination=target_path)
    return target_path, file_size


async def _store_photo(
    storage: SQLiteStorage,
    user_id: int,
    path: Path,
) -> int:
    return await storage.add_photos(user_id, [str(path)])


async def _too_many_error(message: Message, storage: SQLiteStorage, user_id: int) -> None:
    # Сбрасываем текущий набор и просим заново
    _, data = await storage.get_state(user_id)
    photos = data.get("photos", [])
    for p in photos:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    await storage.set_state(user_id, State.WAITING_PHOTOS, {**data, "photos": []})
    await message.answer(
        f"⚠️ Слишком много фотографий. Пожалуйста, отправьте не более {settings.max_photos}. "
        f"Текущий набор сброшен, пришлите фото заново."
    )


# ---------- одиночные фото ----------

@router.message(StateFilter(State.WAITING_PHOTOS), F.photo)
async def on_single_photo(
    message: Message,
    bot: Bot,
    storage: SQLiteStorage,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    result = await _persist_photo(bot, message, user_id)
    if result is None:
        await message.answer(f"⚠️ Фото слишком большое (>{settings.max_photo_size_mb} МБ).")
        return

    path, _ = result
    count = await _store_photo(storage, user_id, path)

    if count > settings.max_photos:
        await _too_many_error(message, storage, user_id)
        return

    await message.answer(
        f"📷 Принято: {count}/{settings.max_photos}. "
        f"Отправьте ещё или нажмите «Готово».",
        reply_markup=_kb_done_skip(),
    )


# ---------- альбомы ----------

@router.message(StateFilter(State.WAITING_PHOTOS), F.media_group_id)
async def on_album_start(
    message: Message,
    bot: Bot,
    storage: SQLiteStorage,
) -> None:
    """Первый (и любой) фото из альбома: скачиваем и буферизуем по media_group_id."""
    user_id = message.from_user.id if message.from_user else 0
    mgid = message.media_group_id
    if not mgid:
        return

    lock = _album_locks.setdefault(mgid, asyncio.Lock())
    async with lock:
        buf = _album_buffers.setdefault(mgid, {"user_id": user_id, "photos": [], "timer": None})
        buf["last_updated"] = time.time()

        if buf["timer"]:
            buf["timer"].cancel()

        result = await _persist_photo(bot, message, user_id)
        if result is None:
            return
        path, _ = result
        buf["photos"].append(path)

        loop = asyncio.get_running_loop()
        buf["timer"] = loop.call_later(
            0.8, lambda: asyncio.create_task(_flush_album(mgid))
        )


async def _flush_album(mgid: str) -> None:
    """Через ~0.8s после последнего фото в альбоме финализируем приём."""
    lock = _album_locks.get(mgid)
    if lock is None:
        return

    async with lock:
        buf = _album_buffers.pop(mgid, None)
        _album_locks.pop(mgid, None)
        if not buf:
            return

        user_id = buf["user_id"]
        new_paths: list[str] = buf["photos"]

        try:
            storage = _get_storage()
            total_photos = await storage.add_photos(user_id, new_paths)

            if total_photos > settings.max_photos:
                _, data = await storage.get_state(user_id)
                photos = data.get("photos", [])
                for p in photos:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except OSError:
                        pass
                await storage.set_state(user_id, State.WAITING_PHOTOS, {**data, "photos": []})
                bot = _get_bot()
                try:
                    await bot.send_message(
                        user_id,
                        f"⚠️ Слишком много фотографий (>{settings.max_photos}). Набор сброшен.",
                    )
                except TelegramBadRequest:
                    pass
                return

            bot = _get_bot()
            try:
                await bot.send_message(
                    user_id,
                    f"📷 Принято альбом: всего {total_photos}/{settings.max_photos}. "
                    "Отправьте ещё или нажмите «Готово».",
                    reply_markup=_kb_done_skip(),
                )
            except TelegramBadRequest:
                pass
        except Exception as exc:
            logger.exception("Критическая ошибка при обработке альбома {}: {}", mgid, exc)


# Системные зависимости пробрасываются через bot.py в момент инициализации
_storage_ref: SQLiteStorage | None = None
_bot_ref: Bot | None = None


def bind_runtime(bot: Bot, storage: SQLiteStorage) -> None:
    global _storage_ref, _bot_ref
    _bot_ref = bot
    _storage_ref = storage


async def cleanup_abandoned_albums():
    """Периодическая очистка зависших буферов альбомов."""
    while True:
        try:
            await asyncio.sleep(600)
            now = time.time()
            to_delete = []
            for mgid, buf in _album_buffers.items():
                last_upd = buf.get("last_updated", 0)
                if now - last_upd > 300:
                    to_delete.append(mgid)

            for mgid in to_delete:
                _album_buffers.pop(mgid, None)
                _album_locks.pop(mgid, None)
                logger.debug("Cleaned up abandoned album buffer: {}", mgid)
        except Exception as exc:
            logger.error("Error during album buffer cleanup: {}", exc)


def _get_bot() -> Bot:
    assert _bot_ref is not None, "Bot не инициализирован"
    return _bot_ref


def _get_storage() -> SQLiteStorage:
    assert _storage_ref is not None, "Storage не инициализирован"
    return _storage_ref


# ---------- текстовые команды в режиме фото ----------

@router.message(StateFilter(State.WAITING_PHOTOS), F.text)
async def on_photos_text(message: Message, storage: SQLiteStorage) -> None:
    user_id = message.from_user.id if message.from_user else 0
    text = (message.text or "").strip().lower()

    if normalize_photos_text(message.text):
        await _proceed_to_generate(message, storage)
        return

    if text in {"готово", "done", "далее", "продолжить"}:
        await _proceed_to_generate(message, storage)
        return

    await message.answer(
        "Пришлите фото или напишите «готово» / «без фото».",
        reply_markup=_kb_done_skip(),
    )


# ---------- инлайн-кнопки ----------

@router.callback_query(F.data == "photos:done")
async def cb_done(call, storage: SQLiteStorage) -> None:
    user_id = call.from_user.id
    _, data = await storage.get_state(user_id)
    photos = data.get("photos", [])
    if not photos:
        await call.answer("Сначала пришлите хотя бы одно фото.", show_alert=True)
        return
    await call.message.answer(
        f"Принято {len(photos)} фото. Запускаю генерацию?",
        reply_markup=_kb_confirm(),
    )
    await storage.set_state(user_id, State.CONFIRM_GENERATION, data)
    await call.answer()


@router.callback_query(F.data == "photos:skip")
async def cb_skip(call, storage: SQLiteStorage) -> None:
    user_id = call.from_user.id
    _, data = await storage.get_state(user_id)
    await storage.set_state(user_id, State.CONFIRM_GENERATION, data)
    await call.message.answer("Без фото. Запускаю генерацию?", reply_markup=_kb_confirm())
    await call.answer()


@router.callback_query(F.data == "gen:edit")
async def cb_edit(call, storage: SQLiteStorage) -> None:
    user_id = call.from_user.id
    _, data = await storage.get_state(user_id)
    await storage.set_state(user_id, State.WAITING_TOPIC, {"photos": []})
    await call.message.answer(
        "Ок, начнём заново. Что хотите изменить?\n"
        "1) Тему — пришлите новую\n"
        "2) Число слайдов — /slides\n"
        "3) Фото — пришлите заново\n\n"
        "Пришлите тему заново:"
    )
    await call.answer()


@router.callback_query(F.data == "gen:go")
async def cb_go(call, bot: Bot, storage: SQLiteStorage, ollama: OllamaClient) -> None:
    user_id = call.from_user.id
    await call.answer()
    await _run_generation(
        chat_id=call.message.chat.id if call.message else user_id,
        bot=bot,
        storage=storage,
        ollama=ollama,
        user_id=user_id,
    )


async def _proceed_to_generate(message: Message, storage: SQLiteStorage) -> None:
    user_id = message.from_user.id if message.from_user else 0
    _, data = await storage.get_state(user_id)
    await storage.set_state(user_id, State.CONFIRM_GENERATION, data)
    await message.answer(
        f"Параметры:\n• Тема: {data.get('topic')}\n"
        f"• Слайдов: {data.get('slides_n')}\n"
        f"• Фото: {len(data.get('photos', []))}\n\n"
        "Запускаю генерацию?",
        reply_markup=_kb_confirm(),
    )


# ---------- текстовое подтверждение генерации ----------

@router.message(StateFilter(State.CONFIRM_GENERATION), F.text)
async def on_confirm_text(
    message: Message,
    bot: Bot,
    storage: SQLiteStorage,
    ollama: OllamaClient,
) -> None:
    text = (message.text or "").strip().lower()
    if text not in {"генерация", "запустить", "поехали", "старт", "да", "✅ генерация", "go", "yes"}:
        await message.answer(
            "Нажмите «Генерация» для запуска или «Изменить», чтобы вернуться.",
            reply_markup=_kb_confirm(),
        )
        return
    await _run_generation(
        chat_id=message.chat.id,
        bot=bot,
        storage=storage,
        ollama=ollama,
        user_id=message.from_user.id if message.from_user else 0,
    )


async def _run_generation(
    chat_id: int,
    bot: Bot,
    storage: SQLiteStorage,
    ollama: OllamaClient,
    user_id: int,
) -> None:
    """Общая логика сборки и отправки презентации.
    ДИАГНОСТИЧЕСКАЯ ВЕРСИЯ: отправляет уведомления о каждом шаге.
    """
    logger.info("DEBUG: Starting _run_generation for user {}", user_id)

    _, data = await storage.get_state(user_id)
    topic = data.get("topic", "Презентация")
    slides_n = int(data.get("slides_n", 10))
    photos: list[str] = data.get("photos", [])

    # На всякий случай снимаем зависшие блокировки для этого пользователя
    await storage.force_clear_user_generation(user_id)

    # Диагностика: проверяем, что именно блокирует
    has_active = await storage.get_active_generations(user_id)
    used = await storage.get_generations_today(user_id)
    st_name, st_data = await storage.get_state(user_id)
    logger.warning(
        "DEBUG _run_generation check: user={} has_active={} used={} limit={} "
        "fsm_state={!r} fsm_data_keys={}",
        user_id, has_active, used, settings.daily_generation_limit,
        st_name, list(st_data.keys()),
    )
    if has_active:
        await bot.send_message(
            chat_id,
            "⏳ Ваша презентация уже генерируется. Пожалуйста, подождите завершения."
        )
        return
    if used >= settings.daily_generation_limit:
        await bot.send_message(
            chat_id,
            f"⛔ Вы использовали все {settings.daily_generation_limit} генераций на сегодня. "
            "Сброс — в 00:00 по Москве.",
        )
        await storage.clear_state(user_id)
        return

    gen_id = await storage.log_generation(
        user_id, topic, slides_n, len(photos), "pending"
    )
    await storage.set_state(user_id, State.GENERATING, data)

    # Сразу уведомляем пользователя, что мы начали
    status_msg = await bot.send_message(chat_id, "🚀 Запуск процесса генерации...")

    output = None
    try:
        async with asyncio.timeout(180): # Увеличил до 3 минут
            # ШАГ 1: Структура
            await status_msg.edit_text("📡 Запрос структуры презентации у нейросети...")
            logger.info("DEBUG: Requesting structure for user {}", user_id)
            try:
                slides = await ollama.generate_structure(topic, slides_n)
            except Exception as exc:
                logger.error("Ollama error for user {}: {}", user_id, exc)
                raise RuntimeError(f"Ошибка нейросети: {exc}")

            # ШАГ 2: Сборка
            await status_msg.edit_text("🛠 Сборка .pptx файла (это может занять до минуты)...")
            logger.info("DEBUG: Building PPTX for user {}", user_id)
            out_dir = Path(settings.tmp_dir) / "outputs" / str(user_id)
            out_dir.mkdir(parents=True, exist_ok=True)
            output = out_dir / "presentation.pptx"

            try:
                await asyncio.to_thread(
                    build_pptx, slides, title=topic, photo_paths=photos, output_path=output
                )
            except Exception as exc:
                logger.exception("PPTX build failed: {}", exc)
                raise RuntimeError(f"Ошибка сборки файла: {exc}")

            # ШАГ 3: Отправка .pptx
            await status_msg.edit_text("📤 Отправляю готовый файл...")
            try:
                doc = FSInputFile(str(output), filename=f"{_safe(topic)}.pptx")
                await bot.send_document(chat_id, document=doc, caption=f"✅ Готово: {topic}")
            except Exception as exc:
                logger.error("Send pptx failed: {}", {exc})
                raise RuntimeError(f"Ошибка отправки файла: {exc}")

            # ШАГ 4: Текст
            try:
                txt_bio = make_text_file(slides, title=topic)
                await bot.send_document(
                    chat_id,
                    document=txt_bio,
                    caption="📄 Текстовая версия всех тезисов",
                )
            except Exception:
                pass

            await storage.update_generation_status(gen_id, "ok")
            await status_msg.edit_text(
                f"✅ Презентация успешно создана!\n"
                f"Использовано сегодня: {used + 1}/{settings.daily_generation_limit}."
            )

    except asyncio.TimeoutError:
        logger.error("Generation timed out for user {}", user_id)
        await status_msg.edit_text("⚠️ Превышено время ожидания (180с). Попробуйте снова.")
        await storage.update_generation_status(gen_id, "timeout")
    except Exception as exc:
        logger.exception("Critical error in _run_generation for user {}: {}", user_id, exc)
        await status_msg.edit_text(f"⚠️ Произошла ошибка: {exc}")
        await storage.update_generation_status(gen_id, "error")
    finally:
        # Обязательная разблокировка
        await storage.clear_state(user_id)
        if output:
            try:
                Path(output).unlink(missing_ok=True)
            except OSError:
                pass
        for p in photos:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()[:60] or "presentation"