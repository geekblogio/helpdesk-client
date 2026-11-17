from unittest import mock

import responses
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from helpdesk_client.client import HelpdeskClient
from helpdesk_client.forms import ReplyForm
from helpdesk_client.siteconfig import DEFAULT_ATTACHMENT_RULES, AttachmentRules, get_site_config

from .support import CATEGORIES, LoggedInTestCase, MockedApiTestCase, config_json, error_json, ticket_json, url


class SiteConfigTests(MockedApiTestCase):
    def test_loads_the_config_once_and_caches_it_per_language(self):
        self.api.get(url("config"), json=config_json(max_files=2, allowed_extensions=[".PDF", ".txt"]))
        config = get_site_config("de")
        self.assertTrue(config.loaded)
        self.assertEqual(config.attachments, AttachmentRules(2, 10 * 1024 * 1024, (".pdf", ".txt")))
        self.assertEqual(config.categories, CATEGORIES)
        self.assertEqual(get_site_config("de"), config)
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.api.calls[0].request.params, {"language": "de"})
        self.assertEqual(self.api.calls[0].request.headers["Authorization"], "Bearer hdk_test_key")
        self.assertNotIn("X-Contact-Id", self.api.calls[0].request.headers)

        get_site_config("en")
        self.assertEqual(len(self.api.calls), 2)
        self.assertEqual(self.api.calls[1].request.params, {"language": "en"})

    @override_settings(HELPDESK_CONFIG_CACHE_TIMEOUT=0)
    def test_cache_timeout_zero_asks_every_time(self):
        self.api.get(url("config"), json=config_json())
        get_site_config()
        get_site_config()
        self.assertEqual(len(self.api.calls), 2)
        self.assertEqual(self.api.calls[0].request.params, {})

    def test_unavailable_api_falls_back_to_the_defaults_and_remembers_it(self):
        self.api.get(url("config"), status=503, body="<html>")
        with self.assertLogs("helpdesk_client", "WARNING") as logs:
            config = get_site_config("de")
        self.assertFalse(config.loaded)
        self.assertIsNone(config.categories)
        self.assertEqual(config.attachments, DEFAULT_ATTACHMENT_RULES)
        self.assertIn("using the defaults: HelpdeskUnavailable", logs.output[-1])
        # The failure is cached for a short while: no second call.
        get_site_config("de")
        self.assertEqual(len(self.api.calls), 1)

    def test_older_helpdesk_without_the_endpoint_falls_back(self):
        self.api.get(url("config"), status=404, json=error_json("not_found"))
        with self.assertLogs("helpdesk_client.siteconfig", "WARNING"):
            self.assertFalse(get_site_config().loaded)

    def test_odd_values_keep_their_defaults(self):
        self.api.get(url("config"), json={"categories": "x", "attachments": {"max_files": "5", "max_file_size": -1}})
        config = get_site_config()
        self.assertTrue(config.loaded)
        self.assertIsNone(config.categories)
        self.assertEqual(config.attachments, DEFAULT_ATTACHMENT_RULES)

    def test_answer_that_is_not_an_object_falls_back(self):
        self.api.get(url("config"), json=[1, 2])
        with self.assertLogs("helpdesk_client.siteconfig", "WARNING"):
            self.assertFalse(get_site_config().loaded)

    def test_a_given_client_is_used(self):
        self.api.get("https://other.test/api/v1/config", json=config_json(max_files=1))
        config = get_site_config(client=HelpdeskClient("https://other.test/api/v1/", "hdk_other"))
        self.assertEqual(config.attachments.max_files, 1)


class FormRulesFromConfigTests(LoggedInTestCase):
    def test_reply_form_follows_the_rules(self):
        rules = AttachmentRules(max_files=1, max_file_size=10, allowed_extensions=(".txt",))
        files = [SimpleUploadedFile("a.txt", b"x"), SimpleUploadedFile("b.png", b"x" * 11)]
        form = ReplyForm({"body": "Hallo"}, {"attachments": files}, attachment_rules=rules)
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors["attachments"],
            [
                "Sie können höchstens 1 Datei anhängen.",
                "b.png: Dieser Dateityp ist nicht erlaubt. Erlaubt sind .txt.",
            ],
        )
        self.assertEqual(
            str(form.fields["attachments"].help_text), "Bis zu 1 Datei mit bis zu 10\xa0Bytes. Erlaubte Typen: TXT."
        )
        self.assertEqual(form.fields["attachments"].widget.attrs["accept"], ".txt")

    def test_detail_page_uses_the_config_of_the_api(self):
        self.api.get(url("tickets/12"), json=ticket_json(12))
        self.api.get(url("config"), json=config_json(max_files=3, allowed_extensions=[".pdf"]))
        response = self.client.get(reverse("helpdesk_client:ticket_detail", args=[12]))
        self.assertContains(response, 'accept=".pdf"')
        self.assertContains(response, "Bis zu 3 Dateien")

    def test_reply_is_checked_against_the_config(self):
        self.api.get(url("tickets/12"), json=ticket_json(12))
        self.api.get(url("config"), json=config_json(allowed_extensions=[".pdf"]))
        response = self.client.post(
            reverse("helpdesk_client:ticket_reply", args=[12]),
            {"body": "Hallo", "attachments": [SimpleUploadedFile("log.txt", b"x")]},
        )
        self.assertContains(response, "log.txt: Dieser Dateityp ist nicht erlaubt. Erlaubt sind .pdf.")
        self.assertFalse([c for c in self.api.calls if c.request.method == "POST"])

    def test_create_page_falls_back_to_the_categories_endpoint(self):
        self.api.get(url("config"), status=502, body="")
        self.api.get(url("categories"), json=CATEGORIES)
        with self.assertLogs("helpdesk_client", "WARNING"):
            response = self.client.get(reverse("helpdesk_client:ticket_create"))
        self.assertContains(response, '<option value="4">Rechnung</option>', html=True)
        self.assertContains(response, 'accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.log"')


class CategoryErrorTests(LoggedInTestCase):
    path = reverse("helpdesk_client:ticket_create")
    form = {"subject": "Rechnung", "body": "Frage zur Rechnung", "category": "4"}

    def test_unknown_category_lands_on_the_field_and_the_categories_are_reloaded(self):
        details = [{"loc": ["body", "payload", "category_id"], "message": "Unknown category", "type": "invalid_input"}]
        self.api.get(url("config"), json=config_json())
        self.api.add(responses.POST, url("tickets"), status=422, json=error_json("invalid_input", "x", details))
        self.client.get(self.path)  # caches the config with category 4
        self.api.replace(responses.GET, url("config"), json=config_json(categories=CATEGORIES[:1]))

        response = self.client.post(self.path, self.form)

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(
            form.errors["category"], ["Diese Kategorie kann nicht gewählt werden. Bitte wählen Sie eine andere."]
        )
        self.assertNotIn("__all__", form.errors)
        self.assertNotContains(response, "Rechnung</option>")
        self.assertContains(response, '<option value="3">Technik</option>', html=True)
        self.assertEqual([c.request.method for c in self.api.calls], ["GET", "POST", "GET"])

    def test_without_categories_left_the_field_disappears(self):
        details = [{"loc": ["body", "payload", "category_id"], "message": "Unknown category", "type": "invalid_input"}]
        self.api.get(url("config"), json=config_json())
        self.api.add(responses.POST, url("tickets"), status=422, json=error_json("invalid_input", "x", details))
        self.client.get(self.path)
        self.api.replace(responses.GET, url("config"), json=config_json(categories=[]))
        response = self.client.post(self.path, self.form)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="category"')
        self.assertContains(response, "Diese Kategorie kann nicht gewählt werden.")

    def test_invalid_input_with_an_unknown_loc_stays_a_form_error(self):
        details = [{"loc": ["body", "payload", "language"], "message": "x", "type": "invalid_input"}, "odd"]
        self.api.get(url("config"), json=config_json())
        self.api.add(responses.POST, url("tickets"), status=422, json=error_json("invalid_input", "x", details))
        response = self.client.post(self.path, self.form)
        self.assertEqual(
            response.context["form"].errors["__all__"],
            ["Ihre Eingaben konnten nicht gespeichert werden. Bitte prüfen Sie sie und versuchen Sie es erneut."],
        )


class CacheDownTests(LoggedInTestCase):
    """A broken cache (e.g. Redis down) costs the caching, not the pages."""

    def setUp(self):
        super().setUp()
        broken = mock.Mock()
        for method in ("get", "set", "add", "delete"):
            getattr(broken, method).side_effect = ConnectionError("cache down")
        patcher = mock.patch("helpdesk_client.cache.caches", {"default": broken})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_config_badge_and_detail_work_without_cache(self):
        from django.template import Context, Template
        from django.test import RequestFactory

        self.api.get(url("config"), json=config_json(max_files=2))
        self.api.get(url("tickets/unread-count"), json={"unread_count": 1})
        self.api.get(url("tickets/12"), json=ticket_json(12, unread=True))
        self.api.post(url("tickets/12/read"), json={"unread_count": 0})
        request = RequestFactory().get("/")
        request.user = self.user
        with self.assertLogs("helpdesk_client", "WARNING"):
            self.assertEqual(get_site_config().attachments.max_files, 2)
            badge = Template("{% load helpdesk_client %}{% helpdesk_unread_count as n %}{{ n }}")
            self.assertEqual(badge.render(Context({"request": request})), "1")
            response = self.client.get(reverse("helpdesk_client:ticket_detail", args=[12]))
        self.assertContains(response, "Bis zu 2 Dateien")
