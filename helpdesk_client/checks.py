"""System checks: fail early when the website is not configured for the helpdesk."""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.utils.module_loading import import_string

from . import conf


@register(Tags.compatibility)
def check_settings(app_configs=None, **kwargs):
    errors = []
    url = conf.get("HELPDESK_API_URL")
    if not url:
        errors.append(
            Error(
                "HELPDESK_API_URL is not set.",
                hint='Set it to the API base URL, e.g. "https://help.webservercenter.de/api/v1/".',
                id="helpdesk_client.E001",
            )
        )
    else:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            errors.append(
                Error(
                    f"HELPDESK_API_URL is not an http(s) URL: {url!r}.",
                    hint='Use e.g. "https://help.webservercenter.de/api/v1/".',
                    id="helpdesk_client.E002",
                )
            )
        elif parts.scheme == "http" and not settings.DEBUG:
            errors.append(
                Warning(
                    "HELPDESK_API_URL uses plain http; the API key would travel unencrypted.",
                    hint="Use https outside of local development.",
                    id="helpdesk_client.W001",
                )
            )
    if not conf.get("HELPDESK_API_KEY"):
        errors.append(
            Error(
                "HELPDESK_API_KEY is not set.",
                hint="Create a key for this website in the helpdesk (Sites -> API keys).",
                id="helpdesk_client.E003",
            )
        )
    for name in ("HELPDESK_CONNECT_TIMEOUT", "HELPDESK_TIMEOUT", "HELPDESK_UPLOAD_TIMEOUT"):
        value = conf.get(name)
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            errors.append(
                Error(f"{name} must be a positive number of seconds, not {value!r}.", id="helpdesk_client.E004")
            )
    page_size = conf.get("HELPDESK_PAGE_SIZE")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        errors.append(
            Error(f"HELPDESK_PAGE_SIZE must be an integer from 1 to 100, not {page_size!r}.", id="helpdesk_client.E005")
        )
    for name in ("HELPDESK_CONTACT_RESOLVER", "HELPDESK_METADATA_RESOLVER"):
        path = conf.get(name)
        if not path:
            if name == "HELPDESK_CONTACT_RESOLVER":
                errors.append(Error(f"{name} must not be empty.", id="helpdesk_client.E006"))
            continue
        try:
            import_string(path)
        except ImportError as exc:
            errors.append(Error(f"{name} cannot be imported: {exc}", id="helpdesk_client.E006"))
    return errors
