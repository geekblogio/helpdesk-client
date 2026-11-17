"""Settings of the app with their defaults.

Read the values through :func:`get` at call time, never at import time, so that
``override_settings`` in tests and late configuration work.
"""

from django.conf import settings

DEFAULTS = {
    # Base URL of API v1, e.g. "https://help.webservercenter.de/api/v1/". Required.
    "HELPDESK_API_URL": "",
    # API key of this website ("hdk_..."). Required. Never leaves the server.
    "HELPDESK_API_KEY": "",
    # Seconds to establish the connection.
    "HELPDESK_CONNECT_TIMEOUT": 5.0,
    # Seconds to wait for an answer.
    "HELPDESK_TIMEOUT": 15.0,
    # Seconds to wait for an answer to a request with attachments (up to 5 x 10 MB).
    "HELPDESK_UPLOAD_TIMEOUT": 120.0,
    # Template the pages extend; it needs the blocks "title" and "content".
    "HELPDESK_BASE_TEMPLATE": "base.html",
    # Dotted path to a callable(request) -> helpdesk_client.client.Contact.
    "HELPDESK_CONTACT_RESOLVER": "helpdesk_client.contact.contact_from_request",
    # Dotted path to a callable(request) -> dict with context data for new tickets, or "".
    "HELPDESK_METADATA_RESOLVER": "",
    # Tickets per page in "My requests".
    "HELPDESK_PAGE_SIZE": 20,
    # Dotted path to a callable(user) -> str: the stable external_user_id of a user. The default
    # contact resolver and the delete hook both use it, so they always agree on the id.
    "HELPDESK_USER_ID_RESOLVER": "helpdesk_client.contact.external_id_for_user",
    # Alias in CACHES for the unread counts, the site config and seen webhook ids.
    "HELPDESK_CACHE": "default",
    # Seconds an unread count is cached for the badge (a webhook clears it earlier).
    "HELPDESK_UNREAD_CACHE_TIMEOUT": 60,
    # Seconds the badge waits for the API (connect and answer); it must not slow down pages.
    "HELPDESK_UNREAD_TIMEOUT": 2.0,
    # Seconds the site config (categories, attachment rules) from GET /config is cached.
    "HELPDESK_CONFIG_CACHE_TIMEOUT": 300,
    # Anonymize the user's contact in the helpdesk when the user is deleted.
    "HELPDESK_DELETE_HOOK": True,
    # Secret of the site's webhook in the helpdesk; "" disables the webhook receiver (404).
    "HELPDESK_WEBHOOK_SECRET": "",
}


def get(name: str):
    return getattr(settings, name, DEFAULTS[name])
