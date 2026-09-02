"""Environment configuration with loud, specific failures."""

import os


class ConfigError(RuntimeError):
    pass


def env(name: str, *, required: bool = True, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        if required:
            raise ConfigError(
                f"Missing required environment variable {name!r}. "
                "See .env.example and SETUP.md."
            )
        return default
    return value


# --- Supabase --------------------------------------------------------------
def supabase_url() -> str:
    return env("NEXT_PUBLIC_SUPABASE_URL")


def supabase_service_key() -> str:
    return env("SUPABASE_SERVICE_ROLE_KEY")


def bucket() -> str:
    return env("NEXT_PUBLIC_SUPABASE_BUCKET", required=False, default="generated")


# --- Paid APIs -------------------------------------------------------------
def apify_token() -> str:
    return env("APIFY_TOKEN")


def openai_key() -> str:
    return env("OPENAI_API_KEY")


# --- App -------------------------------------------------------------------
def base_url() -> str:
    """
    Public HTTPS origin that QStash calls back to.

    Falls back to Vercel's stable production URL so a fresh deploy works
    before PUBLIC_BASE_URL is set, but setting it explicitly is preferred
    because preview deployment URLs change on every push.
    """
    explicit = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")

    for candidate in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
        host = os.environ.get(candidate, "").strip()
        if host:
            if host.startswith("http"):
                return host.rstrip("/")
            return f"https://{host}".rstrip("/")

    raise ConfigError(
        "Cannot determine the app's public URL. Set PUBLIC_BASE_URL to your "
        "deployment origin (e.g. https://your-project.vercel.app)."
    )


# gpt-image-2 quality. Defaults to `medium` because Vercel's Hobby plan caps
# function duration at a hard 300s, and `auto`/`high` at 1024x1536 has been
# benchmarked near or beyond that. See README "The 300-second ceiling".
_VALID_QUALITIES = {"low", "medium", "high", "auto"}


def image_quality() -> str:
    value = os.environ.get("IMAGE_QUALITY", "").strip().lower() or "medium"
    if value not in _VALID_QUALITIES:
        raise ConfigError(
            f"IMAGE_QUALITY must be one of {sorted(_VALID_QUALITIES)}, got {value!r}."
        )
    return value


# Hard server-side ceiling on posts per job. Enforced in BOTH the Next.js
# route that creates jobs and here in the scraper, so a forged request that
# bypasses the UI still cannot exceed it.
MAX_POSTS_CEILING = 100
