"""Клиент Ollama Cloud: запрос генерации, парсинг JSON, retry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.utils.logging import logger


@dataclass(slots=True)
class Slide:
    """Один слайд презентации."""

    title: str
    bullets: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "bullets": list(self.bullets)}


class OllamaError(RuntimeError):
    """Ошибка клиента Ollama."""


class OllamaClient:
    """Минималистичный клиент Ollama Cloud API.

    Документация: https://ollama.com/api
    Метод: POST {base_url}/generate
    """

    def __init__(self, api_key: str, model: str, base_url: str, timeout: int = 60) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def generate_structure(
        self,
        topic: str,
        slides_count: int,
        *,
        max_retries: int = 2,
    ) -> list[Slide]:
        """Запрашивает у LLM структуру презентации и возвращает список Slide.

        Делает до ``max_retries`` повторных попыток при невалидном JSON.
        """
        prompt = self._build_prompt(topic, slides_count)

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                raw = await self._call_generate(prompt, strict=attempt > 1)
                slides = self._parse_strict(raw, slides_count)
                logger.info("Ollama: got {} slides (attempt={})", len(slides), attempt)
                return slides
            except (OllamaError, ValueError) as exc:
                last_error = exc
                logger.warning("Ollama attempt {} failed: {}", attempt, exc)

        raise OllamaError(f"Ollama не вернул валидный JSON: {last_error}")

    def _build_prompt(self, topic: str, slides_count: int) -> str:
        return (
            f"Сгенерируй структуру презентации на тему: «{topic}».\n"
            f"Количество слайдов: {slides_count}.\n"
            f"Язык: русский.\n\n"
            "СТРОГО следуй формату JSON без markdown-обёрток, пояснений и комментариев:\n"
            '{"slides":[{"title":"...","bullets":["...","..."]}]}\n\n'
            "Требования:\n"
            f"- Ровно {slides_count} элементов в массиве slides.\n"
            "- У каждого слайда 2-5 тезисов, каждый ≤ 120 символов.\n"
            "- Никакого текста вне JSON."
        )

    async def _call_generate(self, prompt: str, *, strict: bool) -> str:
        url = f"{self._base_url}/generate"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.4 if strict else 0.7},
        }
        if strict:
            body["system"] = (
                "Ты — ассистент, который возвращает СТРОГО валидный JSON. "
                "Никаких пояснений, markdown-обёрток и комментариев. "
                "Только JSON, начинающийся с { и заканчивающийся }."
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise OllamaError(f"Сетевая ошибка Ollama: {exc}") from exc

        if resp.status_code >= 400:
            raise OllamaError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise OllamaError("Ollama вернул не-JSON ответ") from exc

        # Ollama /generate возвращает {"response": "...", ...}
        text = payload.get("response") or payload.get("message", {}).get("content") or ""
        if not text:
            raise OllamaError("Ollama вернул пустой ответ")
        return text

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Достаёт первый полный JSON-объект из произвольного текста."""
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("JSON-объект не найден в ответе")
        return raw[start : end + 1]

    def _parse_strict(self, raw: str, slides_count: int) -> list[Slide]:
        candidate = self._extract_json(raw)
        # Защита от «грязного» JSON: убираем хвостовые запятые
        candidate = re.sub(r",\s*([\]}])", r"\1", candidate)
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Невалидный JSON: {exc}") from exc

        slides_raw = data.get("slides")
        if not isinstance(slides_raw, list):
            raise ValueError("Нет поля 'slides' или оно не массив")

        slides: list[Slide] = []
        for item in slides_raw:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            bullets_raw = item.get("bullets") or []
            if not isinstance(bullets_raw, list):
                continue
            bullets = [str(b).strip() for b in bullets_raw if str(b).strip()]
            if title and bullets:
                slides.append(Slide(title=title, bullets=bullets))

        if not slides:
            raise ValueError("Не удалось извлечь ни одного слайда")
        if len(slides) < slides_count:
            logger.warning("Ollama вернул {} слайдов, запрошено {}", len(slides), slides_count)
        return slides[:slides_count]


__all__ = ["OllamaClient", "OllamaError", "Slide"]