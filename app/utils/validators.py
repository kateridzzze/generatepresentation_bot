"""Валидаторы пользовательского ввода."""

from __future__ import annotations

import re

MAX_TOPIC_LENGTH = 500
MAX_TOPIC_LEN_HARD = 4000  # абсолютный лимит Telegram

_SLIDE_NUM_RE = re.compile(r"^\s*(\d{1,3})\s*$")
_DEFAULT_KEYWORDS = {"по умолчанию", "по-умолчанию", "default", "стандарт", "8-10", "8–10"}


class ValidationError(ValueError):
    """Ошибка валидации пользовательского ввода."""


def validate_topic(raw: str) -> str:
    """Нормализует тему: trim, проверка длины, отсечение лишнего."""
    if raw is None:
        raise ValidationError("Тема не может быть пустой.")
    topic = raw.strip()
    if not topic:
        raise ValidationError("Тема не может быть пустой. Введите текстом, например: «Квантовые компьютеры».")
    if len(topic) > MAX_TOPIC_LENGTH:
        raise ValidationError(
            f"Тема слишком длинная ({len(topic)} симв.). Максимум — {MAX_TOPIC_LENGTH}."
        )
    return topic


def validate_slides_count(raw: str, default_min: int, default_max: int, min_n: int, max_n: int) -> int:
    """Парсит число слайдов или команду «по умолчанию».

    Для «по умолчанию» возвращает среднее значение диапазона.
    """
    text = (raw or "").strip().lower()
    if text in _DEFAULT_KEYWORDS:
        return (default_min + default_max) // 2

    match = _SLIDE_NUM_RE.match(raw or "")
    if not match:
        raise ValidationError(
            f"Введите целое число слайдов от {min_n} до {max_n} или «по умолчанию»."
        )
    n = int(match.group(1))
    if n < min_n or n > max_n:
        raise ValidationError(f"Число слайдов должно быть от {min_n} до {max_n}.")
    return n


def normalize_photos_text(text: str | None) -> bool:
    """Возвращает True, если текст означает «пропустить фото»."""
    if not text:
        return False
    t = text.strip().lower()
    return t in {"без фото", "без_фото", "пропустить", "skip", "нет", "no", "-"}


__all__ = [
    "ValidationError",
    "MAX_TOPIC_LENGTH",
    "validate_topic",
    "validate_slides_count",
    "normalize_photos_text",
]