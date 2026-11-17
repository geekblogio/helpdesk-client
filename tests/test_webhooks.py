import hashlib
import hmac
import json
import time
from unittest import mock

from django.core.cache import caches
from django.test import Client, override_settings
from django.urls import reverse

from helpdesk_client.cache import set_unread_count, unread_key
from helpdesk_client.signals import webhook_received
from helpdesk_client.webhooks import WebhookView, verify_webhook

from .support import MockedApiTestCase

SECRET = "whsec-test-0123456789"
PATH = reverse("helpdesk_client:webhook")
EVENT_ID = "8c1f5f0e-1d6b-4c5e-9b7a-2f0d3c4e5a61"


def event(**extra) -> dict:
    data = {
        "id": EVENT_ID,
        "event": "ticket.replied",
        "occurred_at": "2026-11-17T11:53:02Z",
        "ticket_id": 42,
        "number": 12,
        "status": "waiting",
        "external_user_id": "1234",
    }
    data.update(extra)
    return data


def sign(body: bytes, timestamp: int, secret: str = SECRET) -> str:
    # Built independently of the code under test, as docs/api.md describes it.
    return "v1=" + hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()


@override_settings(HELPDESK_WEBHOOK_SECRET=SECRET)
class WebhookTests(MockedApiTestCase):
    def setUp(self):
        super().setUp()
        # The helpdesk sends no CSRF token.
        self.client = Client(enforce_csrf_checks=True)
        self.received = []
        webhook_received.connect(self.receiver)
        self.addCleanup(webhook_received.disconnect, self.receiver)

    def receiver(self, sender, event, payload, request, **kwargs):
        self.received.append((sender, event, payload))

    def deliver(self, data=None, *, body=None, timestamp=None, signature=None, headers=None):
        body = body if body is not None else json.dumps(data or event()).encode()
        timestamp = int(time.time()) if timestamp is None else timestamp
        all_headers = {
            "X-Helpdesk-Event": "ticket.replied",
            "X-Helpdesk-Delivery": EVENT_ID,
            "X-Helpdesk-Timestamp": str(timestamp),
            "X-Helpdesk-Signature": sign(body, timestamp) if signature is None else signature,
        }
        all_headers.update(headers or {})
        all_headers = {k: v for k, v in all_headers.items() if v is not None}
        return self.client.post(PATH, body, content_type="application/json", headers=all_headers)

    def test_valid_event_is_accepted_and_signalled(self):
        response = self.deliver()
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.received, [(WebhookView, "ticket.replied", event())])
        self.assertEqual(len(self.api.calls), 0)

    def test_valid_event_clears_the_cached_unread_count(self):
        set_unread_count("1234", 0)
        set_unread_count("999", 5)
        self.assertEqual(self.deliver().status_code, 204)
        cache = caches["default"]
        self.assertIsNone(cache.get(unread_key("1234")))
        self.assertEqual(cache.get(unread_key("999")), 5)

    def test_wrong_signature_is_refused(self):
        cases = {
            "other secret": sign(json.dumps(event()).encode(), int(time.time()), "wrong"),
            "garbage": "v1=deadbeef",
            "other version": "v0=" + sign(json.dumps(event()).encode(), int(time.time()))[3:],
            "no version": sign(json.dumps(event()).encode(), int(time.time()))[3:],
        }
        for name, signature in cases.items():
            with self.subTest(name), self.assertLogs("helpdesk_client.webhooks", "WARNING"):
                self.assertEqual(self.deliver(signature=signature).status_code, 401)
        self.assertEqual(self.received, [])

    def test_changed_body_is_refused(self):
        body = json.dumps(event()).encode()
        signature = sign(body, int(time.time()))
        with self.assertLogs("helpdesk_client.webhooks", "WARNING"):
            response = self.deliver(body=body.replace(b"1234", b"1235"), signature=signature)
        self.assertEqual(response.status_code, 401)

    def test_expired_or_future_timestamp_is_refused(self):
        now = int(time.time())
        for timestamp in (now - 301, now + 301):
            with self.subTest(timestamp=timestamp - now), self.assertLogs("helpdesk_client.webhooks", "WARNING"):
                self.assertEqual(self.deliver(timestamp=timestamp).status_code, 401)
        for timestamp in (now - 299, now + 299):
            with self.subTest(timestamp=timestamp - now):
                caches["default"].clear()
                self.assertEqual(self.deliver(timestamp=timestamp).status_code, 204)
        self.assertEqual(len(self.received), 2)

    def test_missing_headers_are_refused(self):
        for header in ("X-Helpdesk-Signature", "X-Helpdesk-Timestamp"):
            with self.subTest(header), self.assertLogs("helpdesk_client.webhooks", "WARNING"):
                self.assertEqual(self.deliver(headers={header: None}).status_code, 401)
        with self.assertLogs("helpdesk_client.webhooks", "WARNING"):
            self.assertEqual(self.deliver(headers={"X-Helpdesk-Timestamp": "soon"}).status_code, 401)
        self.assertEqual(self.received, [])

    def test_one_of_several_signatures_must_match(self):
        body = json.dumps(event()).encode()
        now = int(time.time())
        response = self.deliver(body=body, timestamp=now, signature=f"v1=0000, {sign(body, now)}")
        self.assertEqual(response.status_code, 204)

    def test_duplicate_event_is_handled_once(self):
        self.assertEqual(self.deliver().status_code, 204)
        # A retry signs again with a new timestamp; the id stays.
        self.assertEqual(self.deliver(timestamp=int(time.time()) + 5).status_code, 204)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.deliver(event(id="other-id")).status_code, 204)
        self.assertEqual(len(self.received), 2)

    def test_failing_receiver_lets_the_helpdesk_retry(self):
        def broken(**kwargs):
            raise RuntimeError("site is busy")

        webhook_received.connect(broken)
        self.addCleanup(webhook_received.disconnect, broken)
        self.client.raise_request_exception = False
        with self.assertLogs("django.request", "ERROR"):
            self.assertEqual(self.deliver().status_code, 500)
        webhook_received.disconnect(broken)
        self.assertEqual(self.deliver().status_code, 204)
        self.assertEqual(len(self.received), 2)

    def test_unknown_events_and_fields_are_passed_on(self):
        data = event(event="ticket.merged", external_user_id=None, extra={"x": 1})
        self.assertEqual(self.deliver(data).status_code, 204)
        self.assertEqual(self.received[0][1], "ticket.merged")

    def test_body_that_is_not_an_event_is_refused(self):
        for body in (b"not json", b"[1, 2]", json.dumps(event(id="")).encode(), json.dumps(event(id=7)).encode()):
            with self.subTest(body=body), self.assertLogs("helpdesk_client.webhooks", "WARNING"):
                self.assertEqual(self.deliver(body=body).status_code, 400)
        self.assertEqual(self.received, [])

    def test_only_post(self):
        self.assertEqual(self.client.get(PATH).status_code, 405)

    @override_settings(HELPDESK_WEBHOOK_SECRET="")
    def test_without_secret_the_receiver_is_off(self):
        self.assertEqual(self.deliver().status_code, 404)
        self.assertEqual(self.received, [])

    def test_works_when_the_cache_is_down(self):
        with (
            mock.patch("helpdesk_client.webhooks.get_cache", side_effect=ConnectionError),
            self.assertLogs("helpdesk_client", "WARNING"),
        ):
            self.assertEqual(self.deliver().status_code, 204)
        self.assertEqual(len(self.received), 1)


class VerifyWebhookTests(MockedApiTestCase):
    def test_matches_the_documented_example_format(self):
        body = b'{"id":"x"}'
        now = int(time.time())
        self.assertTrue(verify_webhook(SECRET, body, str(now), sign(body, now)))
        self.assertFalse(verify_webhook(SECRET, body, str(now), ""))
        self.assertFalse(verify_webhook(SECRET, body, None, sign(body, now)))
        self.assertFalse(verify_webhook(SECRET, body, "", sign(body, now)))
