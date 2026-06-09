"""Central configuration and feature flags for the trend engine.

All toggles are environment driven so the new Gemini engine can be enabled and
the legacy scraping workflow disabled without touching code.

Key flags:
    TREND_ENGINE_PROVIDER   -> "gemini" (default) selects the new engine.
    ENABLE_LEGACY_SCRAPING  -> "false" (default) keeps the old scrapers off.

Google Cloud / Vertex AI auth is reused from the existing image-generation
setup. The new variables (GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION) take
precedence, but we fall back to the legacy VERTEX_PROJECT_ID / VERTEX_LOCATION
names so existing ``.env`` files keep working.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Resolve relative credential paths so imports work regardless of cwd.
_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if _creds and not os.path.isabs(_creds):
    _abs = os.path.abspath(_creds)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _abs


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


# --- Engine selection -------------------------------------------------------

TREND_ENGINE_PROVIDER = os.getenv("TREND_ENGINE_PROVIDER", "gemini").strip().lower()
ENABLE_LEGACY_SCRAPING = _as_bool(os.getenv("ENABLE_LEGACY_SCRAPING"), default=False)


def is_gemini_engine() -> bool:
    return TREND_ENGINE_PROVIDER == "gemini"


def legacy_scraping_enabled() -> bool:
    return ENABLE_LEGACY_SCRAPING


# --- Google Cloud / Vertex AI ----------------------------------------------

def get_project_id() -> str | None:
    """Project ID, preferring the new var, falling back to the legacy one."""
    return os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("VERTEX_PROJECT_ID")


def get_location() -> str:
    """Vertex region, preferring the new var, falling back to the legacy one."""
    return (
        os.getenv("GOOGLE_CLOUD_LOCATION")
        or os.getenv("VERTEX_LOCATION")
        or "us-central1"
    )


def get_credentials_path() -> str | None:
    return os.getenv("GOOGLE_APPLICATION_CREDENTIALS")


def get_api_key() -> str | None:
    """Google AI Studio / Gemini API key (non-Vertex)."""
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")


# --- Models -----------------------------------------------------------------

GEMINI_TREND_MODEL = os.getenv("GEMINI_TREND_MODEL", "gemini-2.0-flash").strip()


# --- Security ---------------------------------------------------------------

def get_cron_secret() -> str | None:
    return os.getenv("CRON_SECRET")


# --- Storage ----------------------------------------------------------------

# SQLite file used by the trend engine. Lives under data/ which is gitignored.
DATABASE_PATH = os.getenv(
    "TREND_DB_PATH",
    os.path.join(os.getcwd(), "data", "trends.db"),
)
