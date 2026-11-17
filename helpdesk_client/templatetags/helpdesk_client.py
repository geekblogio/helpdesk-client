"""Template tags of the support pages: ``{% load helpdesk_client %}``.

- ``{{ ticket.status|helpdesk_status }}``: label of a ticket status.
- ``{% helpdesk_unread_badge %}``: badge with the number of unread answers, nothing if there are none.
- ``{% helpdesk_unread_count as count %}``: the number itself (None if unknown) for an own badge.
"""

from django import template
from django.utils.translation import gettext_lazy as _

from ..badge import unread_count

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


@register.simple_tag(takes_context=True)
def helpdesk_unread_count(context) -> int | None:
    """Unread answers of the logged-in user (cached); None if anonymous or the helpdesk is unavailable."""
    return unread_count(context.get("request"))


@register.inclusion_tag("helpdesk_client/includes/unread_badge.html", takes_context=True)
def helpdesk_unread_badge(context) -> dict:
    """``<span class="helpdesk-badge">`` with the unread count; renders nothing for 0 or unknown."""
    return {"unread_count": unread_count(context.get("request"))}
