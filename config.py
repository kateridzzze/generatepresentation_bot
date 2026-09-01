"""Загрузка конфигурации из переменных окружения."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки бота, загружаемые из .env / окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")

    # Ollama Cloud
    ollama_api_key: str = Field(..., alias="OLLAMA_API_KEY")
    ollama_model: str = Field("minimax-m3:cloud", alias="OLLAMA_MODEL")
    ollama_base_url: str = Field("https://ollama.com/api", alias="OLLAMA_BASE_URL")
    ollama_timeout: int = Field(60, alias="OLLAMA_TIMEOUT")

    # Лимиты
    daily_generation_limit: int = Field(2, alias="DAILY_GENERATION_LIMIT")
    min_slides: int = Field(3, alias="MIN_SLIDES")
    max_slides: int = Field(25, alias="MAX_SLIDES")
    default_slides_min: int = Field(8, alias="DEFAULT_SLIDES_MIN")
    default_slides_max: int = Field(10, alias="DEFAULT_SLIDES_MAX")
    max_photos: int = Field(7, alias="MAX_PHOTOS")
    max_photo_size_mb: int = Field(10, alias="MAX_PHOTO_SIZE_MB")
    max_output_size_mb: int = Field(50, alias="MAX_OUTPUT_SIZE_MB")

    # Хранилище
    sqlite_path: str = Field("./data/bot.db", alias="SQLITE_PATH")
    tmp_dir: str = Field("./tmp", alias="TMP_DIR")
    templates_dir: str = Field("./templates", alias="TEMPLATES_DIR")

    # Прочее
    tz: str = Field("Europe/Moscow", alias="TZ")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    def ensure_dirs(self) -> None:
        """Создаёт каталоги data/, tmp/, templates/, если их нет."""
        for path in (
            Path(self.sqlite_path).parent,
            Path(self.tmp_dir),
            Path(self.tmp_dir) / "photos",
            Path(self.tmp_dir) / "outputs",
            Path(self.templates_dir),
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()  # type: ignore[call-arg]
settings.ensure_dirs()