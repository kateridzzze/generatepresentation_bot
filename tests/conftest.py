"""Общие фикстуры для тестов."""

from __future__ import annotations

import sys
from pathlib import Path

# Делаем корень проекта доступным для импорта без установки пакета
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))