import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-trade-ai-mudar-em-producao")
    UPLOAD_FOLDER = str(ROOT / "uploads")
    DATABASE_PATH = str(ROOT / "data" / "trade_ai.db")
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024
    ALLOWED_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif"})
    LLM_MODE = os.getenv("LLM_MODE", "mock").strip().lower()
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
    # Azure OpenAI (alternativa a api.openai.com)
    OPENAI_PROVIDER = os.getenv("OPENAI_PROVIDER", "").strip().lower()
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
    AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview").strip()

    @classmethod
    def use_azure_openai(cls) -> bool:
        if cls.OPENAI_PROVIDER == "openai":
            return False
        if cls.OPENAI_PROVIDER == "azure":
            return True
        return bool(cls.AZURE_OPENAI_ENDPOINT)
