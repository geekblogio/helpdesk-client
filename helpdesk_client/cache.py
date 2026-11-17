"""What the app keeps in Django's cache (alias ``HELPDESK_CACHE``).

- the unread count per contact for the badge (``HELPDESK_UNREAD_CACHE_TIMEOUT``),
- the site config from ``GET /config`` per language (``HELPDESK_CONFIG_CACHE_TIMEOUT``),
- ids of webhook events already handled (one hour).

Keys carry a hash instead of the external id: no personal data in cache keys, and
every id fits the key rules of memcached.
"""

import hashlib
import logging

from django.core.cache import BaseCache, caches

from . import conf

logger = logging.getLogger(__name__)

PREFIX = "helpdesk_client"
WEBHOOK_SEEN_TIMEOUT = 60 * 60

# Stored for a failed lookup, so a helpdesk that is down is not asked again on every page.
UNAVAILABLE = "unavailable"


def get_cache() -> BaseCache:
    return caches[conf.get("HELPDESK_CACHE")]


def _digest(value: str) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:32]


def unread_key(external_id: str) -> str:
    # Stripped as in the X-Contact-Id header, so the badge and a webhook's external_user_id agree.
    return f"{PREFIX}:unread:{_digest(str(external_id).strip())}"


def config_key(language: str | None) -> str:
    return f"{PREFIX}:config:{_digest((language or '').lower())}"


def webhook_key(event_id: str) -> str:
    return f"{PREFIX}:webhook:{_digest(event_id)}"


def set_unread_count(external_id: str, count: int | str) -> None:
    """Cache the unread count (or :data:`UNAVAILABLE`) of a contact for the badge."""
    try:
        get_cache().set(unread_key(external_id), count, conf.get("HELPDESK_UNREAD_CACHE_TIMEOUT"))
    except Exception:
        logger.warning("Could not write the helpdesk unread count to the cache", exc_info=True)


def invalidate_unread_count(external_id: str) -> None:
    """Forget the cached unread count: the next badge asks the API again."""
    try:
        get_cache().delete(unread_key(external_id))
    except Exception:
        logger.warning("Could not clear the helpdesk unread count in the cache", exc_info=True)
