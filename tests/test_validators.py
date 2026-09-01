"""Тесты валидаторов."""

from __future__ import annotations

import pytest

from app.utils.validators import (
    ValidationError,
    normalize_photos_text,
    validate_slides_count,
    validate_topic,
)


def test_topic_ok() -> None:
    assert validate_topic("  Квантовые компьютеры  ") == "Квантовые компьютеры"


def test_topic_empty() -> None:
    with pytest.raises(ValidationError):
        validate_topic("   ")


def test_topic_too_long() -> None:
    with pytest.raises(ValidationError):
        validate_topic("a" * 501)


def test_slides_default() -> None:
    assert validate_slides_count("по умолчанию", 8, 10, 3, 25) == 9


def test_slides_number() -> None:
    assert validate_slides_count("10", 8, 10, 3, 25) == 10


def test_slides_invalid() -> None:
    with pytest.raises(ValidationError):
        validate_slides_count("100", 8, 10, 3, 25)


def test_slides_garbage() -> None:
    with pytest.raises(ValidationError):
        validate_slides_count("abc", 8, 10, 3, 25)


def test_normalize_photos() -> None:
    assert normalize_photos_text("без фото") is True
    assert normalize_photos_text("Пропустить") is True
    assert normalize_photos_text("привет") is False