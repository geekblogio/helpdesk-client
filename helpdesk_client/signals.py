"""Signals the website can connect to.

``webhook_received``
    Sent once per verified webhook event of the helpdesk (duplicates with the same
    ``id`` are dropped), after the badge cache of the person was cleared. Keyword
    arguments: ``event`` (e.g. ``"ticket.replied"``; unknown values may come and
    should be ignored), ``payload`` (the event as a dict, see ``docs/api.md``) and
    ``request``. The sender is :class:`helpdesk_client.webhooks.WebhookView`.

    Receivers run while the helpdesk waits for the answer (5 s timeout): keep them
    quick and move real work into the background. An exception answers 500, and
    the helpdesk delivers the event again later.
"""

from django.dispatch import Signal

webhook_received = Signal()
