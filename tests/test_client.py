import json
from datetime import UTC, datetime
from io import BytesIO

import requests
import responses
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from helpdesk_client.client import (
    Contact,
    HelpdeskApiError,
    HelpdeskClient,
    HelpdeskUnavailable,
)

from .support import CATEGORIES, MockedApiTestCase, error_json, list_item, page_json, ticket_json, url

ANNA = Contact(external_id="42", email="anna@example.com", name="Anna Müller", language="de")


class ContactHeadersTests(SimpleTestCase):
    def test_values_are_percent_encoded_utf8(self):
        headers = Contact("ä/1", email="anna@example.com", name="Jürgen Groß", language="de-de").headers()
        self.assertEqual(
            headers,
            {
                "X-Contact-Id": "%C3%A4%2F1",
                "X-Contact-Email": "anna%40example.com",
                "X-Contact-Name": "J%C3%BCrgen%20Gro%C3%9F",
                "X-Contact-Language": "de-de",
            },
        )

    def test_empty_values_are_left_out(self):
        self.assertEqual(Contact("7").headers(), {"X-Contact-Id": "7"})

    def test_email_the_api_would_reject_is_left_out(self):
        self.assertNotIn("X-Contact-Email", Contact("7", email="not-an-email").headers())
        self.assertNotIn("X-Contact-Email", Contact("7", email=f"{'a' * 250}@example.com").headers())

    def test_name_is_normalized_and_cut_to_the_api_limit(self):
        headers = Contact("7", name="  Anna \n Muster " + "x" * 300).headers()
        self.assertTrue(headers["X-Contact-Name"].startswith("Anna%20Muster%20xxx"))
        self.assertEqual(len(headers["X-Contact-Name"].replace("%20", " ")), 200)

    def test_external_id_is_required_and_limited(self):
        with self.assertRaises(ValueError):
            Contact("").headers()
        with self.assertRaises(ValueError):
            Contact("x" * 256).headers()


class ConfigurationTests(SimpleTestCase):
    @override_settings(HELPDESK_API_URL="", HELPDESK_API_KEY="hdk_x")
    def test_missing_url_is_a_clear_error(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "HELPDESK_API_URL"):
            HelpdeskClient()

    @override_settings(HELPDESK_API_KEY="")
    def test_missing_key_is_a_clear_error(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "HELPDESK_API_KEY"):
            HelpdeskClient()

    @override_settings(HELPDESK_API_URL="https://helpdesk.test/api/v1", HELPDESK_TIMEOUT=7, HELPDESK_CONNECT_TIMEOUT=2)
    def test_settings_are_read(self):
        client = HelpdeskClient()
        self.assertEqual(client.base_url, "https://helpdesk.test/api/v1/")
        self.assertEqual((client.connect_timeout, client.timeout), (2, 7))
        self.assertEqual(client.session.headers["Authorization"], "Bearer hdk_test_key")


class EndpointTests(MockedApiTestCase):
    def setUp(self):
        super().setUp()
        self.client_ = HelpdeskClient()

    def test_categories_without_contact(self):
        self.api.get(url("categories"), json=CATEGORIES)
        self.assertEqual(self.client_.categories(language="en"), CATEGORIES)
        call = self.api.calls[0]
        self.assertEqual(call.request.url, url("categories?language=en"))
        self.assertEqual(call.request.headers["Authorization"], "Bearer hdk_test_key")
        self.assertEqual(self.contact_headers(call), {})

    def test_tickets_send_contact_and_paging_and_parse_dates(self):
        self.api.get(url("tickets"), json=page_json([list_item(12)]))
        data = self.client_.tickets(ANNA, page=2, page_size=5)
        call = self.api.calls[0]
        self.assertEqual(call.request.params, {"page": "2", "page_size": "5"})
        self.assertEqual(
            self.contact_headers(call),
            {
                "X-Contact-Id": "42",
                "X-Contact-Email": "anna@example.com",
                "X-Contact-Name": "Anna Müller",
                "X-Contact-Language": "de",
            },
        )
        self.assertEqual(data["items"][0]["created_at"], datetime(2026, 11, 10, 9, tzinfo=UTC))
        self.assertIsNone(data["items"][0]["closed_at"])

    def test_ticket_parses_message_dates(self):
        self.api.get(url("tickets/12"), json=ticket_json(12))
        ticket = self.client_.ticket(ANNA, 12)
        self.assertEqual(ticket["messages"][1]["created_at"], datetime(2026, 11, 11, 10, 30, tzinfo=UTC))

    def test_create_ticket_as_json_without_files(self):
        self.api.post(url("tickets"), json=ticket_json(13), status=201)
        ticket = self.client_.create_ticket(
            ANNA, subject="Hilfe", body="Text", category_id=3, language="de", metadata={"url": "/x"}
        )
        self.assertEqual(ticket["number"], 13)
        request = self.api.calls[0].request
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(
            json.loads(request.body),
            {"subject": "Hilfe", "body": "Text", "category_id": 3, "language": "de", "metadata": {"url": "/x"}},
        )
        self.assertEqual(request.req_kwargs["timeout"], (5.0, 15.0))

    def test_create_ticket_with_files_as_multipart(self):
        self.api.post(url("tickets"), json=ticket_json(13), status=201)
        upload = SimpleUploadedFile("screen.png", b"\x89PNG\r\n\x1a\nxyz", content_type="image/png")
        upload.read()  # a form may have read it already
        self.client_.create_ticket(
            ANNA, subject="Hilfe", body="Text", files=[upload, ("a.txt", BytesIO(b"hi"), "text/plain")]
        )
        request = self.api.calls[0].request
        self.assertTrue(request.headers["Content-Type"].startswith("multipart/form-data"))
        body = request.body
        self.assertIn(b'name="payload"', body)
        self.assertIn(b'{"subject": "Hilfe", "body": "Text"}', body)
        self.assertIn(b'name="attachments"; filename="screen.png"', body)
        self.assertIn(b"\x89PNG\r\n\x1a\nxyz", body)
        self.assertIn(b'name="attachments"; filename="a.txt"', body)
        self.assertEqual(request.req_kwargs["timeout"], (5.0, 120.0))

    def test_reply_mark_read_close_unread_count(self):
        self.api.post(url("tickets/12/messages"), json=ticket_json(12), status=201)
        self.api.post(url("tickets/12/read"), json={"unread_count": 1})
        self.api.post(url("tickets/12/close"), json=ticket_json(12, status="closed"))
        self.api.get(url("tickets/unread-count"), json={"unread_count": 3})
        self.assertEqual(self.client_.reply(ANNA, 12, body="Danke")["number"], 12)
        self.assertEqual(json.loads(self.api.calls[0].request.body), {"body": "Danke"})
        self.assertEqual(self.client_.mark_read(ANNA, 12), 1)
        self.assertEqual(self.client_.close_ticket(ANNA, 12)["status"], "closed")
        self.assertEqual(self.client_.unread_count(ANNA), 3)

    def test_download_streams_the_file(self):
        self.api.get(
            url("tickets/12/attachments/7"),
            body=b"PNGDATA",
            content_type="image/png",
            headers={"Content-Disposition": 'attachment; filename="screen.png"'},
        )
        download = self.client_.download_attachment(ANNA, 12, 7)
        self.assertTrue(self.api.calls[0].request.req_kwargs["stream"])
        self.assertEqual(download.content_type, "image/png")
        self.assertEqual(download.content_disposition, 'attachment; filename="screen.png"')
        self.assertEqual(b"".join(download), b"PNGDATA")
        download.close()

    def test_numbers_are_integers_in_the_path(self):
        with self.assertRaises(ValueError):
            self.client_.ticket(ANNA, "12/../../categories")


class ErrorTests(MockedApiTestCase):
    def setUp(self):
        super().setUp()
        self.client_ = HelpdeskClient()

    def call(self, **kwargs):
        self.api.get(url("tickets/12"), **kwargs)
        return self.client_.ticket(ANNA, 12)

    def test_api_error_carries_code_and_details(self):
        details = [{"loc": ["header", "X-Contact-Id"], "message": "Field required", "type": "missing"}]
        with self.assertRaises(HelpdeskApiError) as cm:
            self.call(status=422, json=error_json("validation_error", "Invalid request", details))
        self.assertEqual((cm.exception.status, cm.exception.code), (422, "validation_error"))
        self.assertEqual(cm.exception.details, details)

    def test_not_found_and_blocked(self):
        with self.assertRaises(HelpdeskApiError) as cm:
            self.call(status=404, json=error_json("not_found"))
        self.assertEqual(cm.exception.code, "not_found")
        self.api.replace(responses.GET, url("tickets/12"), status=403, json=error_json("contact_blocked"))
        with self.assertRaises(HelpdeskApiError) as cm:
            self.client_.ticket(ANNA, 12)
        self.assertEqual(cm.exception.code, "contact_blocked")

    def test_rate_limit_with_retry_after(self):
        with self.assertRaises(HelpdeskApiError) as cm:
            self.call(status=429, json=error_json("rate_limited"), headers={"Retry-After": "30"})
        self.assertEqual((cm.exception.code, cm.exception.retry_after), ("rate_limited", 30))

    def test_nginx_413_and_429_without_json(self):
        with self.assertRaises(HelpdeskApiError) as cm:
            self.call(status=413, body="<html>413</html>", content_type="text/html")
        self.assertEqual(cm.exception.code, "request_too_large")
        self.api.replace(responses.GET, url("tickets/12"), status=429, body="<html>", content_type="text/html")
        with self.assertRaises(HelpdeskApiError) as cm:
            self.client_.ticket(ANNA, 12)
        self.assertEqual(cm.exception.code, "rate_limited")

    def test_unauthorized_is_logged_as_error(self):
        with self.assertLogs("helpdesk_client.client", "ERROR"), self.assertRaises(HelpdeskApiError) as cm:
            self.call(status=401, json=error_json("unauthorized"))
        self.assertEqual(cm.exception.code, "unauthorized")

    def test_server_errors_and_foreign_pages_are_unavailable(self):
        cases = [
            {"status": 500, "json": error_json("server_error")},
            {"status": 502, "body": "Bad gateway", "content_type": "text/html"},
            {"status": 404, "body": "<html>Not found</html>", "content_type": "text/html"},
            {"status": 302, "headers": {"Location": "https://elsewhere.test/"}},
            {"status": 200, "body": "<html>maintenance</html>", "content_type": "text/html"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.api.reset()
                with self.assertLogs("helpdesk_client.client"), self.assertRaises(HelpdeskUnavailable):
                    self.call(**kwargs)

    def test_timeout_and_connection_error_are_unavailable(self):
        for exc in (requests.ConnectTimeout(), requests.ReadTimeout(), requests.ConnectionError()):
            with self.subTest(exc=exc):
                self.api.reset()
                with self.assertLogs("helpdesk_client.client", "WARNING"), self.assertRaises(HelpdeskUnavailable):
                    self.call(body=exc)
