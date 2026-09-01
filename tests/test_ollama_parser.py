"""Тесты парсинга ответа Ollama."""

from __future__ import annotations

import pytest

from app.services.ollama_client import OllamaClient


def test_parse_clean_json() -> None:
    raw = '{"slides":[{"title":"T1","bullets":["a","b"]}]}'
    slides = OllamaClient(api_key="x", model="m", base_url="http://x")._parse_strict(raw, 1)  # noqa: SLF001
    assert len(slides) == 1
    assert slides[0].title == "T1"
    assert slides[0].bullets == ["a", "b"]


def test_parse_with_markdown_wrapper() -> None:
    raw = "Вот структура:\n```json\n{\"slides\":[{\"title\":\"T\",\"bullets\":[\"x\"]}]}\n```\nГотово."
    slides = OllamaClient(api_key="x", model="m", base_url="http://x")._parse_strict(raw, 1)  # noqa: SLF001
    assert slides[0].title == "T"


def test_parse_invalid_raises() -> None:
    with pytest.raises(ValueError):
        OllamaClient(api_key="x", model="m", base_url="http://x")._parse_strict("not json", 1)  # noqa: SLF001


def test_parse_truncates_to_count() -> None:
    raw = '{"slides":[{"title":"A","bullets":["1"]},{"title":"B","bullets":["2"]}]}'
    slides = OllamaClient(api_key="x", model="m", base_url="http://x")._parse_strict(raw, 1)  # noqa: SLF001
    assert len(slides) == 1
    assert slides[0].title == "A"