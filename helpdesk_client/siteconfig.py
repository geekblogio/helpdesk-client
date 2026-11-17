"""Site config from ``GET /config``: categories and attachment rules, cached.

The API is the authority for the attachment rules; the forms only mirror them
to show problems before the upload. When the config cannot be loaded (helpdesk
down, an older helpdesk without the endpoint, an odd answer) the forms fall back
to :data:`DEFAULT_ATTACHMENT_RULES`, the rules of API v1 at the time of writing,
and the categories are fetched on their own.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from . import conf
from .cache import UNAVAILABLE, config_key, get_cache
from .client import HelpdeskClient, HelpdeskError

logger = logging.getLogger(__name__)

# A failed load is remembered this long (seconds, at most HELPDESK_CONFIG_CACHE_TIMEOUT).
FAILURE_CACHE_TIMEOUT = 60


@dataclass(frozen=True)
class AttachmentRules:
    max_files: int = 5
    max_file_size: int = 10 * 1024 * 1024
    allowed_extensions: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".log")

    @classmethod
    def from_api(cls, data: Any) -> AttachmentRules:
        """Rules from the API's ``attachments`` object; a missing or odd value keeps its default."""
        data = data if isinstance(data, dict) else {}
        default = cls()
        max_files = data.get("max_files")
        max_file_size = data.get("max_file_size")
        extensions = data.get("allowed_extensions")
        if isinstance(extensions, list) and extensions and all(_is_extension(e) for e in extensions):
            extensions = tuple(e.lower() for e in extensions)
        else:
            extensions = default.allowed_extensions
        return cls(
            max_files=max_files if _is_positive_int(max_files) else default.max_files,
            max_file_size=max_file_size if _is_positive_int(max_file_size) else default.max_file_size,
            allowed_extensions=extensions,
        )


DEFAULT_ATTACHMENT_RULES = AttachmentRules()


@dataclass(frozen=True)
class SiteConfig:
    attachments: AttachmentRules = DEFAULT_ATTACHMENT_RULES
    # None: not known (the config could not be loaded); fetch them with HelpdeskClient.categories.
    categories: list[dict] | None = None
    loaded: bool = False
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: dict) -> SiteConfig:
        categories = data.get("categories")
        return cls(
            attachments=AttachmentRules.from_api(data.get("attachments")),
            categories=categories if isinstance(categories, list) else None,
            loaded=True,
            raw=data,
        )


FALLBACK = SiteConfig()


def get_site_config(language: str | None = None, *, client: HelpdeskClient | None = None) -> SiteConfig:
    """The site config for ``language`` from the cache or the API; :data:`FALLBACK` if unavailable.

    Never raises for problems of the helpdesk or the cache.
    """
    cache = get_cache()
    key = config_key(language)
    try:
        cached = cache.get(key)
    except Exception:
        logger.warning("Could not read the helpdesk config from the cache", exc_info=True)
        cached = None
    if cached == UNAVAILABLE:
        return FALLBACK
    if isinstance(cached, dict):
        return SiteConfig.from_api(cached)

    timeout = conf.get("HELPDESK_CONFIG_CACHE_TIMEOUT")
    try:
        data = (client or HelpdeskClient()).config(language)
        if not isinstance(data, dict):
            raise TypeError("config is not an object")
    except (HelpdeskError, TypeError) as exc:
        logger.warning("Helpdesk config not available, using the defaults: %s", exc.__class__.__name__)
        data, result = UNAVAILABLE, FALLBACK
        timeout = min(timeout, FAILURE_CACHE_TIMEOUT)
    else:
        result = SiteConfig.from_api(data)
    try:
        cache.set(key, data, timeout)
    except Exception:
        logger.warning("Could not write the helpdesk config to the cache", exc_info=True)
    return result


def invalidate_site_config(language: str | None = None) -> None:
    """Forget the cached config for ``language``, e.g. after the API refused a category."""
    try:
        get_cache().delete(config_key(language))
    except Exception:
        logger.warning("Could not clear the helpdesk config in the cache", exc_info=True)


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_extension(value: Any) -> bool:
    return isinstance(value, str) and len(value) > 1 and value.startswith(".")
