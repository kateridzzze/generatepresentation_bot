"""Состояния FSM для сценария бота."""

from enum import StrEnum


class State(StrEnum):
    """Состояния конечного автомата сценария."""

    IDLE = "idle"
    WAITING_TOPIC = "waiting_topic"
    WAITING_SLIDES_COUNT = "waiting_slides_count"
    WAITING_PHOTOS = "waiting_photos"
    CONFIRM_GENERATION = "confirm_generation"
    GENERATING = "generating"


__all__ = ["State"]