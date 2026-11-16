"""
Configuration manager for Autodesk Inventor 2025 + LLM integration.
Handles loading and persisting settings to .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base paths
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
DRAWINGS_DIR_DEFAULT = ROOT_DIR / "drawings"

# Load environment variables
load_dotenv(dotenv_path=ENV_PATH)


class Config:
    @staticmethod
    def get_provider() -> str:
        return os.getenv("LLM_PROVIDER", "gemini").lower()

    @staticmethod
    def get_gemini_api_key() -> str:
        return os.getenv("GEMINI_API_KEY", "").strip()

    @staticmethod
    def get_gemini_model() -> str:
        return os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    @staticmethod
    def get_lm_studio_url() -> str:
        return os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1").strip()

    @staticmethod
    def get_lm_studio_model() -> str:
        return os.getenv("LM_STUDIO_MODEL", "local-model").strip()

    @staticmethod
    def get_drawings_dir() -> Path:
        custom_dir = os.getenv("DRAWINGS_DIR", "").strip()
        if custom_dir:
            p = Path(custom_dir)
            if not p.is_absolute():
                p = ROOT_DIR / p
            return p
        return DRAWINGS_DIR_DEFAULT

    @staticmethod
    def get_lm_studio_timeout() -> int:
        try:
            return int(os.getenv("LM_STUDIO_TIMEOUT", "600"))
        except Exception:
            return 600

    @classmethod
    def save_settings(
        cls,
        provider: str = None,
        gemini_api_key: str = None,
        gemini_model: str = None,
        lm_studio_url: str = None,
        lm_studio_model: str = None,
        lm_studio_timeout: int = None,
    ) -> None:
        """Save updated settings to .env file."""
        current = {
            "LLM_PROVIDER": provider if provider is not None else cls.get_provider(),
            "GEMINI_API_KEY": gemini_api_key if gemini_api_key is not None else cls.get_gemini_api_key(),
            "GEMINI_MODEL": gemini_model if gemini_model is not None else cls.get_gemini_model(),
            "LM_STUDIO_URL": lm_studio_url if lm_studio_url is not None else cls.get_lm_studio_url(),
            "LM_STUDIO_MODEL": lm_studio_model if lm_studio_model is not None else cls.get_lm_studio_model(),
            "LM_STUDIO_TIMEOUT": str(lm_studio_timeout if lm_studio_timeout is not None else cls.get_lm_studio_timeout()),
            "DRAWINGS_DIR": "drawings",
        }

        # Update environment in memory
        for k, v in current.items():
            os.environ[k] = str(v)

        # Write to .env
        content = (
            f"# Настройки интеграции Autodesk Inventor 2025 + LLM\n"
            f"LLM_PROVIDER={current['LLM_PROVIDER']}\n"
            f"GEMINI_API_KEY={current['GEMINI_API_KEY']}\n"
            f"GEMINI_MODEL={current['GEMINI_MODEL']}\n"
            f"LM_STUDIO_URL={current['LM_STUDIO_URL']}\n"
            f"LM_STUDIO_MODEL={current['LM_STUDIO_MODEL']}\n"
            f"DRAWINGS_DIR={current['DRAWINGS_DIR']}\n"
        )
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write(content)
