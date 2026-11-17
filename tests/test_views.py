import json

import requests
import responses
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from helpdesk_client.client import Contact

from .support import (
    LoggedInTestCase,
    MockedApiTestCase,
    config_json,
    error_json,
    list_item,
    page_json,
    ticket_json,
    url,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def custom_contact(request):
    return Contact(external_id=f"cp-{request.user.username}", language="en")


def custom_metadata(request):
    return {"source": "test", "path": request.path}


class LoginRequiredTests(MockedApiTestCase):
    def test_every_page_needs_a_login(self):
        paths = [
            reverse("helpdesk_client:ticket_list"),
            reverse("helpdesk_client:ticket_create"),
            reverse("helpdesk_client:ticket_detail", args=[12]),
            reverse("helpdesk_client:attachment_download", args=[12, 7]),
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/admin/login/?next="))
        for name in ("ticket_reply", "ticket_close"):
            response = self.client.post(reverse(f"helpdesk_client:{name}", args=[12]))
            self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.api.calls), 0)


class TicketListTests(LoggedInTestCase):
    path = reverse("helpdesk_client:ticket_list")

    def test_lists_tickets_of_the_user_in_german(self):
        self.api.get(url("tickets"), json=page_json([list_item(12, unread=True), list_item(9, status="closed")]))
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 id="helpdesk-ticket-list-title">Meine Anfragen</h1>', html=False)
        self.assertContains(response, "WSC-12")
        self.assertContains(response, 'href="/support/12/"')
        self.assertContains(response, "Neue Antwort", count=1)
        self.assertContains(response, "Geschlossen")
        self.assertContains(response, "In Bearbeitung")
        self.assertContains(response, "| Test site</title>")
        call = self.api.calls[0]
        self.assertEqual(call.request.params, {"page": "1", "page_size": "20"})
        self.assertEqual(
            self.contact_headers(call),
            {
                "X-Contact-Id": str(self.user.pk),
                "X-Contact-Email": "anna@example.com",
                "X-Contact-Name": "Anna Müller",
                "X-Contact-Language": "de",
            },
        )

    def test_language_of_the_request_is_passed_on(self):
        self.api.get(url("tickets"), json=page_json([]))
        response = self.client.get(self.path, headers={"Accept-Language": "en"})
        self.assertContains(response, "You have not sent any requests yet.")
        self.assertEqual(self.contact_headers(self.api.calls[0])["X-Contact-Language"], "en")

    def test_pagination(self):
        self.api.get(url("tickets"), json=page_json([list_item(30)], page=2, pages=3, page_size=1))
        response = self.client.get(self.path, {"page": "2"})
        self.assertEqual(self.api.calls[0].request.params["page"], "2")
        self.assertContains(response, 'href="?page=1"')
        self.assertContains(response, 'href="?page=3"')
        self.assertContains(response, "Seite 2 von 3")

    @override_settings(HELPDESK_PAGE_SIZE=5)
    def test_invalid_page_falls_back_to_the_first(self):
        self.api.get(url("tickets"), json=page_json([]))
        self.client.get(self.path, {"page": "abc"})
        self.assertEqual(self.api.calls[0].request.params, {"page": "1", "page_size": "5"})

    @override_settings(HELPDESK_CONTACT_RESOLVER="tests.test_views.custom_contact")
    def test_custom_contact_resolver(self):
        self.api.get(url("tickets"), json=page_json([]))
        self.client.get(self.path)
        self.assertEqual(
            self.contact_headers(self.api.calls[0]), {"X-Contact-Id": "cp-anna", "X-Contact-Language": "en"}
        )


class ErrorPageTests(LoggedInTestCase):
    path = reverse("helpdesk_client:ticket_list")

    def test_blocked_contact(self):
        self.api.get(url("tickets"), status=403, json=error_json("contact_blocked"))
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Der Support ist für Ihr Konto nicht verfügbar", status_code=403)

    def test_rate_limited(self):
        self.api.get(url("tickets"), status=429, json=error_json("rate_limited"), headers={"Retry-After": "10"})
        response = self.client.get(self.path)
        self.assertContains(response, "Zu viele Anfragen", status_code=429)

    def test_unavailable(self):
        self.api.get(url("tickets"), body=requests.ConnectionError("down"))
        with self.assertLogs("helpdesk_client.client", "WARNING"):
            response = self.client.get(self.path)
        self.assertContains(response, "vorübergehend nicht erreichbar", status_code=503)

    def test_wrong_api_key_shows_unavailable_and_logs_an_error(self):
        self.api.get(url("tickets"), status=401, json=error_json("unauthorized"))
        with self.assertLogs("helpdesk_client", "ERROR") as logs:
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(any("unauthorized" in line for line in logs.output))


class TicketCreateTests(LoggedInTestCase):
    path = reverse("helpdesk_client:ticket_create")

    def setUp(self):
        super().setUp()
        self.api.get(url("config"), json=config_json())

    def post_form(self, **data):
        form = {"subject": "Login klappt nicht", "body": "Seit heute **nicht**.", "category": "3"}
        form.update(data)
        return self.client.post(self.path, {k: v for k, v in form.items() if v is not None})

    def test_form_offers_the_categories_in_the_users_language(self):
        response = self.client.get(self.path)
        self.assertContains(response, '<option value="4">Rechnung</option>', html=True)
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.log"')
        self.assertContains(response, "Bis zu 5 Dateien, je bis zu 10,0 MB. Erlaubte Typen: PNG, JPG, JPEG")
        self.assertEqual(self.api.calls[0].request.params, {"language": "de"})
        self.assertEqual(len(self.api.calls), 1)

    def test_without_categories_the_field_is_left_out(self):
        self.api.replace(responses.GET, url("config"), json=config_json(categories=[]))
        response = self.client.get(self.path)
        self.assertNotContains(response, 'name="category"')
        self.api.add(responses.POST, url("tickets"), json=ticket_json(13), status=201)
        response = self.post_form(category=None)
        self.assertRedirects(response, "/support/13/", fetch_redirect_response=False)
        self.assertNotIn("category_id", json.loads(self.api.calls[-1].request.body))

    def test_category_is_required_when_there_are_categories(self):
        response = self.post_form(category="")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].has_error("category"))

    @override_settings(HELPDESK_METADATA_RESOLVER="tests.test_views.custom_metadata")
    def test_creates_the_ticket_and_redirects_to_it(self):
        self.api.add(responses.POST, url("tickets"), json=ticket_json(13), status=201)
        response = self.post_form()
        self.assertRedirects(response, "/support/13/", fetch_redirect_response=False)
        request = self.api.calls[-1].request
        self.assertEqual(
            json.loads(request.body),
            {
                "subject": "Login klappt nicht",
                "body": "Seit heute **nicht**.",
                "category_id": 3,
                "metadata": {"source": "test", "path": "/support/new/"},
            },
        )
        self.assertEqual(self.contact_headers(self.api.calls[-1])["X-Contact-Id"], str(self.user.pk))
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)], ["Ihre Anfrage WSC-13 wurde gesendet."])

    def test_attachments_are_sent_as_multipart(self):
        self.api.add(responses.POST, url("tickets"), json=ticket_json(13), status=201)
        files = [
            SimpleUploadedFile("screen.png", PNG, content_type="image/png"),
            SimpleUploadedFile("log.txt", b"line", content_type="text/plain"),
        ]
        response = self.post_form(attachments=files)
        self.assertEqual(response.status_code, 302)
        body = self.api.calls[-1].request.body
        self.assertIn(b'name="payload"', body)
        self.assertIn(b'filename="screen.png"', body)
        self.assertIn(b'filename="log.txt"', body)

    def test_local_checks_of_attachments_spare_the_api_call(self):
        cases = [
            ([SimpleUploadedFile("tool.exe", b"MZ")], "tool.exe: Dieser Dateityp ist nicht erlaubt."),
            ([SimpleUploadedFile("empty.txt", b"")], "empty.txt ist leer."),
            ([SimpleUploadedFile(f"{i}.txt", b"x") for i in range(6)], "Sie können höchstens 5 Dateien anhängen."),
        ]
        for files, message in cases:
            with self.subTest(message=message):
                response = self.post_form(attachments=files)
                self.assertContains(response, message)
        self.assertFalse([c for c in self.api.calls if c.request.method == "POST"])

    def test_large_attachment_is_refused_locally(self):
        with self.settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1):
            big = SimpleUploadedFile("big.pdf", b"%PDF" + b"0" * (10 * 1024 * 1024))
            response = self.post_form(attachments=[big])
        self.assertContains(response, "big.pdf ist größer als 10,0")

    def test_attachment_refused_by_the_api_names_the_file(self):
        details = [
            {"loc": ["file", "attachments", "1"], "message": "Content does not match", "type": "invalid_attachment"}
        ]
        self.api.add(responses.POST, url("tickets"), status=422, json=error_json("invalid_attachment", "x", details))
        files = [SimpleUploadedFile("ok.png", PNG), SimpleUploadedFile("fake.jpg", PNG)]
        response = self.post_form(attachments=files)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fake.jpg wurde nicht angenommen")
        self.assertContains(response, 'value="Login klappt nicht"')

    def test_api_validation_errors_land_on_the_fields(self):
        details = [{"loc": ["body", "payload", "subject"], "message": "too long", "type": "string_too_long"}]
        self.api.add(responses.POST, url("tickets"), status=422, json=error_json("validation_error", "x", details))
        response = self.post_form()
        form = response.context["form"]
        self.assertEqual(form.errors["subject"], ["Dieser Wert wurde nicht angenommen."])

    def test_invalid_input_and_too_large_requests(self):
        cases = [
            (
                {"status": 422, "json": error_json("invalid_input", "Unknown category")},
                None,
                "Ihre Eingaben konnten nicht gespeichert werden",
            ),
            ({"status": 413, "body": "<html>", "content_type": "text/html"}, "attachments", "zusammen zu groß"),
            ({"status": 422, "json": error_json("too_many_attachments")}, "attachments", "Zu viele Dateien"),
            ({"status": 422, "json": error_json("attachment_too_large")}, "attachments", "Eine Datei ist zu groß."),
        ]
        for kwargs, field, message in cases:
            with self.subTest(message=message):
                self.api.upsert(responses.POST, url("tickets"), **kwargs)
                response = self.post_form()
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].has_error(field or "__all__"))
                self.assertContains(response, message)

    def test_unexpected_api_error_is_not_shown_as_a_form_error(self):
        self.api.add(responses.POST, url("tickets"), status=409, json=error_json("invalid_status_change"))
        with self.assertLogs("helpdesk_client", "ERROR"):
            response = self.post_form()
        self.assertEqual(response.status_code, 503)


class TicketDetailTests(LoggedInTestCase):
    path = reverse("helpdesk_client:ticket_detail", args=[12])

    def setUp(self):
        super().setUp()
        self.api.get(url("config"), json=config_json())

    def test_shows_history_attachments_and_forms(self):
        self.api.get(url("tickets/12"), json=ticket_json(12))
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<p>Bitte <strong>Cache</strong> leeren.</p>", html=True)
        self.assertContains(response, 'href="/support/12/attachments/7/"')
        self.assertContains(response, "screen.png")
        self.assertContains(response, "2,0")  # filesizeformat of 2048 bytes in German
        self.assertContains(response, 'action="/support/12/reply/"')
        self.assertContains(response, 'action="/support/12/close/"')
        self.assertContains(response, "Technik")
        self.assertContains(response, "Christian")
        # Read already: not marked again. The config is for the reply form.
        self.assertEqual([c.request.url for c in self.api.calls], [url("tickets/12"), url("config?language=de")])

    def test_opening_an_unread_ticket_marks_it_read(self):
        self.api.get(url("tickets/12"), json=ticket_json(12, unread=True))
        self.api.post(url("tickets/12/read"), json={"unread_count": 0})
        self.client.get(self.path)
        self.assertEqual(
            [c.request.url for c in self.api.calls],
            [url("tickets/12"), url("tickets/12/read"), url("config?language=de")],
        )
        self.assertEqual(self.contact_headers(self.api.calls[1])["X-Contact-Id"], str(self.user.pk))

    def test_failing_mark_read_does_not_break_the_page(self):
        self.api.get(url("tickets/12"), json=ticket_json(12, unread=True))
        self.api.post(url("tickets/12/read"), status=503, body="busy")
        with self.assertLogs("helpdesk_client", "WARNING"):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)

    def test_unknown_or_foreign_ticket_is_404(self):
        self.api.get(url("tickets/99"), status=404, json=error_json("not_found"))
        response = self.client.get(reverse("helpdesk_client:ticket_detail", args=[99]))
        self.assertEqual(response.status_code, 404)

    def test_closed_ticket_past_reopen_window(self):
        ticket = ticket_json(12, status="closed", can_reply=False, can_close=False)
        self.api.get(url("tickets/12"), json=ticket)
        response = self.client.get(self.path)
        self.assertContains(response, "kann nicht mehr beantwortet werden")
        self.assertContains(response, 'href="/support/new/"')
        self.assertNotContains(response, 'action="/support/12/reply/"')
        self.assertNotContains(response, 'action="/support/12/close/"')

    def test_closed_ticket_within_reopen_window(self):
        ticket = ticket_json(12, status="closed", can_close=False, reopen_until="2026-12-10T09:00:00Z")
        self.api.get(url("tickets/12"), json=ticket)
        response = self.client.get(self.path)
        self.assertContains(response, "Eine Antwort bis zum 10.12.2026 öffnet sie wieder.")
        self.assertContains(response, 'action="/support/12/reply/"')


class TicketReplyTests(LoggedInTestCase):
    path = reverse("helpdesk_client:ticket_reply", args=[12])

    def setUp(self):
        super().setUp()
        self.api.get(url("config"), json=config_json())

    def test_reply_is_sent_and_redirects(self):
        self.api.post(url("tickets/12/messages"), json=ticket_json(12), status=201)
        response = self.client.post(self.path, {"body": "Danke, geht wieder."})
        self.assertRedirects(response, "/support/12/", fetch_redirect_response=False)
        self.assertEqual(json.loads(self.api.calls[-1].request.body), {"body": "Danke, geht wieder."})

    def test_reply_with_attachment(self):
        self.api.post(url("tickets/12/messages"), json=ticket_json(12), status=201)
        self.client.post(self.path, {"body": "Anbei", "attachments": [SimpleUploadedFile("a.pdf", b"%PDF-1.7")]})
        self.assertIn(b'filename="a.pdf"', self.api.calls[-1].request.body)

    def test_empty_reply_shows_the_ticket_with_the_error(self):
        self.api.get(url("tickets/12"), json=ticket_json(12))
        response = self.client.post(self.path, {"body": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["reply_form"].has_error("body"))
        self.assertContains(response, "Seit heute komme ich nicht rein.")

    def test_reply_after_reopen_window_offers_a_new_ticket(self):
        self.api.post(url("tickets/12/messages"), status=409, json=error_json("reopen_window_expired"))
        self.api.get(url("tickets/12"), json=ticket_json(12, status="closed"))
        response = self.client.post(self.path, {"body": "Noch eine Frage"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kann nicht mehr beantwortet werden")
        self.assertContains(response, "Neue Anfrage stellen")

    def test_attachment_refused_by_the_api(self):
        self.api.post(url("tickets/12/messages"), status=422, json=error_json("attachment_too_large"))
        self.api.get(url("tickets/12"), json=ticket_json(12))
        response = self.client.post(self.path, {"body": "x", "attachments": [SimpleUploadedFile("a.pdf", b"%PDF")]})
        self.assertTrue(response.context["reply_form"].has_error("attachments"))

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.path).status_code, 405)


class TicketCloseTests(LoggedInTestCase):
    path = reverse("helpdesk_client:ticket_close", args=[12])

    def test_close(self):
        self.api.post(url("tickets/12/close"), json=ticket_json(12, status="closed"))
        response = self.client.post(self.path)
        self.assertRedirects(response, "/support/12/", fetch_redirect_response=False)
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)], ["Die Anfrage wurde geschlossen."])

    def test_close_of_foreign_ticket_is_404(self):
        self.api.post(url("tickets/12/close"), status=404, json=error_json("not_found"))
        self.assertEqual(self.client.post(self.path).status_code, 404)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.path).status_code, 405)
        self.assertEqual(len(self.api.calls), 0)


class AttachmentDownloadTests(LoggedInTestCase):
    path = reverse("helpdesk_client:attachment_download", args=[12, 7])

    def test_streams_the_file_with_safe_headers(self):
        self.api.get(
            url("tickets/12/attachments/7"),
            body=PNG,
            content_type="image/png",
            headers={
                "Content-Disposition": "attachment; filename=\"Bild.png\"; filename*=utf-8''B%C3%BCld.png",
                "Content-Length": str(len(PNG)),
            },
        )
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertEqual(b"".join(response.streaming_content), PNG)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(
            response["Content-Disposition"], "attachment; filename=\"Bild.png\"; filename*=utf-8''B%C3%BCld.png"
        )
        self.assertEqual(response["Content-Length"], str(len(PNG)))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Security-Policy"], "default-src 'none'; sandbox")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(self.contact_headers(self.api.calls[0])["X-Contact-Id"], str(self.user.pk))
        response.close()

    def test_unknown_attachment_is_404(self):
        self.api.get(url("tickets/12/attachments/7"), status=404, json=error_json("not_found"))
        self.assertEqual(self.client.get(self.path).status_code, 404)
