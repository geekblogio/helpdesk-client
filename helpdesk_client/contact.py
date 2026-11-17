"""Who the helpdesk sees: turn the request's user into a :class:`~helpdesk_client.client.Contact`.

Replace the default with the setting ``HELPDESK_CONTACT_RESOLVER`` (dotted path
to a ``callable(request) -> Contact``), e.g. when the user model has no
``get_full_name``. Another stable id goes into ``HELPDESK_USER_ID_RESOLVER``
(``callable(user) -> str``); the delete hook needs it without a request.
"""

from django.http import HttpRequest
from django.utils import translation
from django.utils.module_loading import import_string

from . import conf
from .client import Contact


def external_id_for_user(user) -> str:
    """Default ``HELPDESK_USER_ID_RESOLVER``: the user's primary key."""
    return str(user.pk)


def get_external_id(user) -> str:
    """The stable ``external_user_id`` of ``user`` (``HELPDESK_USER_ID_RESOLVER``)."""
    return str(import_string(conf.get("HELPDESK_USER_ID_RESOLVER"))(user))


def contact_from_request(request: HttpRequest) -> Contact:
    """Default: ``HELPDESK_USER_ID_RESOLVER`` (the primary key), email, full name and the active language."""
    user = request.user
    get_full_name = getattr(user, "get_full_name", None)
    return Contact(
        external_id=get_external_id(user),
        email=getattr(user, "email", "") or "",
        name=(get_full_name() if callable(get_full_name) else "") or "",
        language=translation.get_language() or "",
    )


def get_contact(request: HttpRequest) -> Contact:
    return import_string(conf.get("HELPDESK_CONTACT_RESOLVER"))(request)


def get_metadata(request: HttpRequest) -> dict:
    """Context data for a new ticket from ``HELPDESK_METADATA_RESOLVER``, or ``{}``."""
    path = conf.get("HELPDESK_METADATA_RESOLVER")
    return dict(import_string(path)(request) or {}) if path else {}
