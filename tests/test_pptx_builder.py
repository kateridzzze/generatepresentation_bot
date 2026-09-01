"""Тесты сборки .pptx без сети."""

from __future__ import annotations

from pathlib import Path

from app.services.ollama_client import Slide
from app.services.pptx_builder import (
    _distribute_photos,
    build_pptx,
    slides_to_text,
)


def test_distribute_no_photos() -> None:
    assert _distribute_photos(5, 0) == [None] * 5


def test_distribute_equal() -> None:
    plan = _distribute_photos(3, 3)
    assert plan == [0, 1, 2]


def test_distribute_more_photos_than_slides() -> None:
    plan = _distribute_photos(2, 5)
    assert plan[:2] == [0, 1]


def test_distribute_less_photos_than_slides() -> None:
    plan = _distribute_photos(5, 2)
    assert plan[0] is None  # титульный пропускаем
    assert sum(1 for x in plan if x is not None) == 2


def test_build_pptx_no_photos(tmp_path: Path) -> None:
    slides = [
        Slide(title="Слайд 1", bullets=["Первый", "Второй"]),
        Slide(title="Слайд 2", bullets=["Третий"]),
    ]
    out = build_pptx(slides, title="Тест", output_path=tmp_path / "test.pptx")
    assert out.exists()
    assert out.stat().st_size > 1000


def test_slides_to_text() -> None:
    slides = [Slide(title="A", bullets=["a", "b"])]
    text = slides_to_text(slides, title="T")
    assert "T" in text and "A" in text and "• a" in text