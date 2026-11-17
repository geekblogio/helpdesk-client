import time
from unittest import mock

import requests
import responses
from django.contrib.auth.models import AnonymousUser
from django.core.cache import caches
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from django.urls import reverse

from helpdesk_client.cache import unread_key
from helpdesk_client.client import HelpdeskClient

from .support import LoggedInTestCase, error_json, ticket_json, url

BADGE = Template("{% load helpdesk_client %}[{% helpdesk_unread_badge %}]")
COUNT = Template("{% load helpdesk_client %}{% helpdesk_unread_count as count %}<{{ count }}>")


class BadgeTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.request = RequestFactory().get("/")
        self.request.user = self.user
        self.request.LANGUAGE_CODE = "de"

    def render(self, template=BADGE, request=None):
        return template.render(Context({"request": request or self.request})).strip()

    def test_shows_the_unread_count(self):
        self.api.get(url("tickets/unread-count"), json={"unread_count": 3})
        self.assertEqual(self.render(), '[<span class="helpdesk-badge" title="3 ungelesene Antworten">3</span>]')
        call = self.api.calls[0]
        self.assertEqual(self.contact_headers(call)["X-Contact-Id"], str(self.user.pk))

    def test_zero_shows_nothing(self):
        self.api.get(url("tickets/unread-count"), json={"unread_count": 0})
        self.assertEqual(self.render(), "[]")
        self.assertEqual(self.render(COUNT), "<0>")

    def test_anonymous_user_shows_nothing_without_asking(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        self.assertEqual(self.render(request=request), "[]")
        self.assertEqual(self.render(COUNT, request=request), "<None>")
        self.assertEqual(BADGE.render(Context({})), "[]")
        self.assertEqual(len(self.api.calls), 0)

    def test_cache_hit_asks_the_api_once(self):
        self.api.get(url("tickets/unread-count"), json={"unread_count": 2})
        self.assertIn(">2<", self.render())
        self.assertEqual(self.render(COUNT), "<2>")
        self.assertEqual(len(self.api.calls), 1)

    @override_settings(HELPDESK_UNREAD_CACHE_TIMEOUT=30)
    def test_cached_count_expires(self):
        self.api.get(url("tickets/unread-count"), json={"unread_count": 1})
        now = time.time()
        with mock.patch("time.time", return_value=now):
            self.assertEqual(self.render(COUNT), "<1>")
        self.api.replace(responses.GET, url("tickets/unread-count"), json={"unread_count": 4})
        with mock.patch("time.time", return_value=now + 29):
            self.assertEqual(self.render(COUNT), "<1>")
        with mock.patch("time.time", return_value=now + 31):
            self.assertEqual(self.render(COUNT), "<4>")
        self.assertEqual(len(self.api.calls), 2)

    def test_unavailable_api_shows_nothing_and_is_not_asked_again_at_once(self):
        self.api.get(url("tickets/unread-count"), status=503, body="<html>")
        with self.assertLogs("helpdesk_client", "ERROR"):
            self.assertEqual(self.render(), "[]")
        self.assertEqual(self.render(COUNT), "<None>")
        self.assertEqual(len(self.api.calls), 1)

    def test_blocked_contact_and_odd_answers_show_nothing(self):
        answers = [
            {"status": 403, "json": error_json("contact_blocked")},
            {"json": {"unread_count": "3"}},
            {"json": {}},
        ]
        for kwargs in answers:
            with self.subTest(answer=kwargs):
                self.api.upsert(responses.GET, url("tickets/unread-count"), **kwargs)
                self.assertEqual(self.render(), "[]")
                caches["default"].clear()

    def test_timeout_and_connection_errors_show_nothing(self):
        self.api.get(url("tickets/unread-count"), body=requests.ConnectTimeout())
        with self.assertLogs("helpdesk_client", "WARNING"):
            self.assertEqual(self.render(), "[]")

    @override_settings(HELPDESK_UNREAD_TIMEOUT=1.5)
    def test_badge_uses_its_own_short_timeout(self):
        self.api.get(url("tickets/unread-count"), json={"unread_count": 0})
        with mock.patch("helpdesk_client.badge.HelpdeskClient", wraps=HelpdeskClient) as client:
            self.render()
        client.assert_called_once_with(connect_timeout=1.5, timeout=1.5)

    def test_opening_an_unread_ticket_updates_the_cached_count(self):
        self.api.get(url("tickets/unread-count"), json={"unread_count": 2})
        self.api.get(url("tickets/12"), json=ticket_json(12, unread=True))
        self.api.post(url("tickets/12/read"), json={"unread_count": 1})
        self.api.get(url("config"), status=404, json=error_json("not_found"))
        self.assertEqual(self.render(COUNT), "<2>")
        with self.assertLogs("helpdesk_client", "WARNING"):
            self.client.get(reverse("helpdesk_client:ticket_detail", args=[12]))
        self.assertEqual(self.render(COUNT), "<1>")
        self.assertEqual(len([c for c in self.api.calls if c.request.url.endswith("unread-count")]), 1)

    def test_cache_key_has_no_personal_data(self):
        key = unread_key("anna@example.com")
        self.assertNotIn("anna", key)
        self.assertEqual(key, unread_key(" anna@example.com "))
