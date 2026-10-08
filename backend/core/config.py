from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:admin@127.0.0.1:5432/ppe_new"
    JWT_SECRET: str = "core-sentry-super-secret-jwt-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    MEDIA_ROOT: str = str(Path(__file__).resolve().parent.parent.parent / "media")
    BASE_DIR: str = str(Path(__file__).resolve().parent.parent.parent)
    HR_API_BASE: str = "https://hr-payroll.deskbook.app"
    HR_STORAGE_BASE: str = "https://hr-payroll.deskbook.app/storage/media"
    HR_PAYROLL_BASE_URL: str = "https://hr-payroll.deskbook.app"
    ATTENDANCE_KEY: str = ""
    HR_SYNC_INTERVAL_MINUTES: int = 15

        # Chatbot / Agent
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash"

    # Vision (reads alert snapshots). Blank model = use GEMINI_MODEL.
    GEMINI_SCAN_MODEL: str = ""      # light model that screens MANY snapshots (set a lite/flash-lite one)
    GEMINI_VISION_MODEL: str = ""    # stronger model for counting / detail on a FEW snapshots
    VISION_CONCURRENCY: int = 4      # parallel image calls - keep under your Gemini rate limit
    VISION_MAX_SCAN: int = 60        # default snapshots examined per scan_alerts_for call
    VISION_SCAN_BUDGET_S: int = 60   # stop starting new images after this many seconds

    # RAG Configuration
    RAG_EMBED_MODEL: str = "default"
    RAG_TOP_K: int = 4
    RAG_MIN_SCORE: float = 0.2


    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()
