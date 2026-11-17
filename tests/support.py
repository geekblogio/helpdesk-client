"""Test helpers: a mocked helpdesk API (``responses``) and answers shaped like API v1."""

import re
from urllib.parse import unquote

import responses
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase

API = "https://helpdesk.test/api/v1/"


def url(path: str) -> str:
    return API + path


def ticket_json(number=12, *, status="open", unread=False, can_reply=True, can_close=True, **extra) -> dict:
    """A ticket as ``GET /tickets/{number}`` answers it."""
    data = {
        "number": number,
        "display_number": f"WSC-{number}",
        "subject": "Login klappt nicht",
        "status": status,
        "category": {"id": 3, "name": "Technik"},
        "language": "de",
        "unread": unread,
        "created_at": "2026-11-10T09:00:00Z",
        "updated_at": "2026-11-11T10:30:00+00:00",
        "closed_at": None,
        "metadata": {},
        "can_reply": can_reply,
        "can_close": can_close,
        "reopen_until": None,
        "messages": [
            {
                "id": 1,
                "author_type": "contact",
                "author_name": "Anna Muster",
                "body": "Seit heute komme ich nicht rein.",
                "body_html": "<p>Seit heute komme ich nicht rein.</p>",
                "created_at": "2026-11-10T09:00:00Z",
                "attachments": [{"id": 7, "filename": "screen.png", "content_type": "image/png", "size": 2048}],
            },
            {
                "id": 2,
                "author_type": "agent",
                "author_name": "Christian",
                "body": "Bitte **Cache** leeren.",
                "body_html": "<p>Bitte <strong>Cache</strong> leeren.</p>",
                "created_at": "2026-11-11T10:30:00Z",
                "attachments": [],
            },
        ],
    }
    data.update(extra)
    return data


def list_item(number=12, **kwargs) -> dict:
    data = ticket_json(number, **kwargs)
    for key in ("metadata", "can_reply", "can_close", "reopen_until", "messages"):
        data.pop(key)
    return data


def page_json(items, *, page=1, pages=1, page_size=20) -> dict:
    return {"items": items, "count": len(items), "page": page, "page_size": page_size, "pages": pages}


def error_json(code: str, message: str = "Error", details=None) -> dict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


CATEGORIES = [
    {"id": 3, "name": "Technik", "name_de": "Technik", "name_en": "Technical"},
    {"id": 4, "name": "Rechnung", "name_de": "Rechnung", "name_en": "Billing"},
]


def config_json(*, categories=None, **attachments) -> dict:
    """``GET /config`` as the API answers it; ``attachments`` overrides single rules."""
    rules = {
        "max_files": 5,
        "max_file_size": 10 * 1024 * 1024,
        "allowed_extensions": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".log"],
        "content_types": ["application/pdf", "image/gif", "image/jpeg", "image/png", "image/webp", "text/plain"],
    }
    rules.update(attachments)
    return {
        "name": "WebServerCenter",
        "prefix": "WSC",
        "languages": ["de", "en"],
        "language": "de",
        "categories": CATEGORIES if categories is None else categories,
        "attachments": rules,
    }


class MockedApiTestCase(TestCase):
    """Every HTTP call goes to ``self.api`` (a ``responses`` mock); unmatched calls fail.

    The cache (unread counts, site config, webhook ids) starts empty in every test.
    """

    def setUp(self):
        super().setUp()
        caches["default"].clear()
        self.addCleanup(caches["default"].clear)
        self.api = responses.RequestsMock(assert_all_requests_are_fired=False)
        self.api.start()
        self.addCleanup(self.api.stop)
        self.addCleanup(self.api.reset)

    def contact_headers(self, call) -> dict:
        return {
            name: unquote(value)
            for name, value in call.request.headers.items()
            if re.match(r"^X-Contact-", name, re.IGNORECASE)
        }


class LoggedInTestCase(MockedApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            "anna", email="anna@example.com", password="x", first_name="Anna", last_name="Müller"
        )
        self.client.force_login(self.user)
