"""Template tags of the support pages: ``{% load helpdesk_client %}``.

The unread badge (HD-17) belongs here as well.
"""

from django import template
from django.utils.translation import gettext_lazy as _

register = template.Library()

STATUS_LABELS = {
    "new": _("New"),
    "open": _("In progress"),
    "waiting": _("Waiting for your reply"),
    "closed": _("Closed"),
}


@register.filter
def helpdesk_status(status: str) -> str:
    """Label of a ticket status for the person (``new``, ``open``, ``waiting``, ``closed``)."""
    return STATUS_LABELS.get(status, status)
