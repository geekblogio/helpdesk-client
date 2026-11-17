"""Receiver for the helpdesk's webhooks (optional, ``HELPDESK_WEBHOOK_SECRET``).

Format and verification as in ``docs/api.md`` (section "Webhooks") of the helpdesk:
``X-Helpdesk-Signature: v1=<hex>`` is HMAC-SHA256 with the secret over
``"<X-Helpdesk-Timestamp>.<raw body>"``; the timestamp may differ from our clock by
at most 300 seconds. A verified event clears the person's cached unread count and
sends :data:`helpdesk_client.signals.webhook_received`. An event ``id`` seen in the
last hour is answered with 204 and not handled again.

Answers: 204 handled (or duplicate), 400 no valid JSON event, 401 signature missing,
wrong or expired, 404 receiver not configured, 405 not POST.
"""

import hashlib
import hmac
import json
import logging
import time

from django.http import Http404, HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from . import conf
from .cache import WEBHOOK_SEEN_TIMEOUT, get_cache, invalidate_unread_count, webhook_key
from .signals import webhook_received

logger = logging.getLogger(__name__)

TOLERANCE = 300  # seconds


def verify_webhook(secret: str, body: bytes, timestamp: str, signature: str) -> bool:
    """Signature check exactly as documented in the helpdesk's ``docs/api.md``."""
    try:
        sent_at = int(timestamp)
    except TypeError, ValueError:
        return False
    if abs(time.time() - sent_at) > TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), f"{sent_at}.".encode() + body, hashlib.sha256).hexdigest()
    return any(
        version == "v1" and hmac.compare_digest(value, expected)
        for version, _, value in (part.strip().partition("=") for part in signature.split(","))
    )


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        secret = conf.get("HELPDESK_WEBHOOK_SECRET")
        if not secret:
            raise Http404("The webhook receiver is not configured.")
        body = request.body
        timestamp = request.headers.get("X-Helpdesk-Timestamp", "")
        signature = request.headers.get("X-Helpdesk-Signature", "")
        if not signature or not verify_webhook(secret, body, timestamp, signature):
            logger.warning("Helpdesk webhook rejected: signature missing, invalid or expired")
            return HttpResponse(status=401)
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not payload["id"]:
            logger.warning("Helpdesk webhook rejected: body is not an event")
            return HttpResponse(status=400)

        event_id = payload["id"]
        event = str(payload.get("event") or "")
        if not _first_delivery(event_id):
            logger.info("Helpdesk webhook %s %s: duplicate, ignored", event, event_id)
            return HttpResponse(status=204)
        try:
            external_id = payload.get("external_user_id")
            if isinstance(external_id, str | int) and str(external_id).strip():
                invalidate_unread_count(str(external_id))
            webhook_received.send(sender=WebhookView, event=event, payload=payload, request=request)
        except Exception:
            # Let the helpdesk deliver it again.
            _forget_delivery(event_id)
            raise
        logger.info("Helpdesk webhook %s %s handled", event, event_id)
        return HttpResponse(status=204)


def _first_delivery(event_id: str) -> bool:
    try:
        return get_cache().add(webhook_key(event_id), 1, WEBHOOK_SEEN_TIMEOUT)
    except Exception:
        logger.warning("Could not check the helpdesk webhook id in the cache", exc_info=True)
        return True


def _forget_delivery(event_id: str) -> None:
    try:
        get_cache().delete(webhook_key(event_id))
    except Exception:
        logger.warning("Could not clear the helpdesk webhook id in the cache", exc_info=True)
