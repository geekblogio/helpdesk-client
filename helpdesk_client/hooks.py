"""Delete hook: anonymize the helpdesk contact of a deleted user (``HELPDESK_DELETE_HOOK``).

On ``post_delete`` of the user model the hook

1. resolves the user's ``external_user_id`` (``HELPDESK_USER_ID_RESOLVER``),
2. records a :class:`~helpdesk_client.models.PendingAnonymization` in the same
   transaction (in a savepoint, so a failure there never rolls back the deletion),
3. calls ``DELETE /contacts/{id}`` after the commit and removes the record when the
   API confirmed. A failed call leaves the record for ``manage.py helpdesk_anonymize``.

Nothing here can make the deletion fail: every error is caught and logged
without the external id (only the record's number, the HTTP status or the error class).
"""

import logging
from functools import partial

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete
from django.utils import timezone

from . import conf
from .cache import invalidate_unread_count
from .client import HelpdeskApiError, HelpdeskClient
from .contact import get_external_id

logger = logging.getLogger(__name__)

DISPATCH_UID = "helpdesk_client.hooks.user_deleted"


def connect() -> None:
    post_delete.connect(user_deleted, sender=settings.AUTH_USER_MODEL, dispatch_uid=DISPATCH_UID)


def user_deleted(sender, instance, using=None, **kwargs) -> None:
    if not conf.get("HELPDESK_DELETE_HOOK"):
        return
    try:
        external_id = get_external_id(instance)
    except Exception as exc:
        logger.error("Delete hook: no helpdesk id for the deleted user (%s)", exc.__class__.__name__)
        return
    pending_pk = _record_pending(external_id)
    transaction.on_commit(partial(anonymize, external_id, pending_pk=pending_pk), using=using, robust=True)


def anonymize(external_id: str, *, pending_pk: int | None = None, client: HelpdeskClient | None = None) -> bool:
    """Anonymize the contact in the helpdesk; on failure keep (or create) the pending record.

    Returns True when the API confirmed. Never raises.
    """
    from .models import PendingAnonymization

    invalidate_unread_count(external_id)
    try:
        result = (client or HelpdeskClient()).anonymize_contact(external_id)
    except Exception as exc:
        reason = _reason(exc)
        pending_pk = _record_failure(external_id, pending_pk, reason)
        logger.warning(
            "Helpdesk contact not anonymized (%s); pending anonymization #%s is retried by manage.py helpdesk_anonymize",
            reason,
            pending_pk if pending_pk is not None else "?",
        )
        return False
    try:
        PendingAnonymization.objects.filter(external_id=external_id).delete()
    except Exception as exc:
        logger.warning("Could not remove pending anonymization #%s (%s)", pending_pk, exc.__class__.__name__)
    tickets = result.get("tickets") if isinstance(result, dict) else None
    logger.info("Helpdesk contact anonymized (%s tickets)", tickets)
    return True


def _record_pending(external_id: str) -> int | None:
    from .models import PendingAnonymization

    try:
        with transaction.atomic():
            pending, _ = PendingAnonymization.objects.get_or_create(external_id=external_id)
    except Exception as exc:
        # E.g. migrations not applied. The call after the commit still runs.
        logger.warning("Could not record the pending anonymization (%s)", exc.__class__.__name__)
        return None
    return pending.pk


def _record_failure(external_id: str, pending_pk: int | None, reason: str) -> int | None:
    from .models import PendingAnonymization

    try:
        pending, _ = PendingAnonymization.objects.get_or_create(external_id=external_id)
        pending.attempts += 1
        pending.last_attempt_at = timezone.now()
        pending.last_error = reason[:100]
        pending.save(update_fields=["attempts", "last_attempt_at", "last_error"])
    except Exception as exc:
        logger.error("Could not record the failed anonymization (%s)", exc.__class__.__name__)
        return pending_pk
    return pending.pk


def _reason(exc: Exception) -> str:
    if isinstance(exc, HelpdeskApiError):
        return f"{exc.status} {exc.code}"
    return exc.__class__.__name__
