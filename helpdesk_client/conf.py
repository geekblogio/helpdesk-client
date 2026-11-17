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
}


def get(name: str):
    return getattr(settings, name, DEFAULTS[name])
