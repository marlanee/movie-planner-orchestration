import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

KST = timezone(timedelta(hours=9))


def get_key(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def has_key(name: str) -> bool:
    return get_key(name) is not None


def today_kst() -> date:
    return datetime.now(KST).date()


def _int_setting(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


GMS_MODEL = os.getenv("GMS_MODEL") or "gpt-5.4-nano"
GMS_BASE_URL = os.getenv("GMS_BASE_URL") or "https://gms.ssafy.io/gmsapi/api.openai.com/v1/"
REPLAN_LIMIT = _int_setting("REPLAN_LIMIT", 3)
AD_MINUTES = _int_setting("AD_MINUTES", 10)
