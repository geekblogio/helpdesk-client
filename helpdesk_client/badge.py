"""Unread count for the badge in the website's navigation, cached per contact.

The count is cached for ``HELPDESK_UNREAD_CACHE_TIMEOUT`` seconds. Opening an
unread ticket stores the new count, a webhook of the helpdesk clears it. When
the helpdesk cannot be asked, the badge shows nothing, and that is cached as
well, so a helpdesk that is down does not slow down every page.
"""

import logging

from django.http import HttpRequest

from . import conf
from .cache import UNAVAILABLE, get_cache, set_unread_count, unread_key
from .client import HelpdeskClient, HelpdeskError
from .contact import get_contact

logger = logging.getLogger(__name__)


def unread_count(request: HttpRequest | None) -> int | None:
    """Unread answers of the logged-in user, or None (anonymous, helpdesk unavailable)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    contact = get_contact(request)
    try:
        cached = get_cache().get(unread_key(contact.external_id))
    except Exception:
        logger.warning("Could not read the helpdesk unread count from the cache", exc_info=True)
        cached = None
    if cached == UNAVAILABLE:
        return None
    if isinstance(cached, int):
        return cached

    timeout = conf.get("HELPDESK_UNREAD_TIMEOUT")
    client = HelpdeskClient(connect_timeout=timeout, timeout=timeout)
    try:
        count = client.unread_count(contact)
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("unread_count is not an integer")
    except (HelpdeskError, ValueError, KeyError, TypeError) as exc:
        # The client has logged what went wrong with the API; keep the page quiet.
        logger.info("Helpdesk unread count not available: %s", exc.__class__.__name__)
        set_unread_count(contact.external_id, UNAVAILABLE)
        return None
    finally:
        client.session.close()
    set_unread_count(contact.external_id, count)
    return count
