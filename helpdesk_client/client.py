"""Client for API v1 of the helpdesk (server to server).

The website calls the API with its API key and passes the logged-in user as a
:class:`Contact` in the ``X-Contact-*`` headers. Errors of the API arrive as
:class:`HelpdeskApiError` with the API's ``code``; network problems, timeouts,
server errors and answers that are not the API's raise :class:`HelpdeskUnavailable`.

Dates in the answers are turned into aware ``datetime`` objects, everything
else stays as the API sends it (see ``docs/api.md`` in the helpdesk repo).
"""

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email

from . import __version__, conf

logger = logging.getLogger(__name__)

DOWNLOAD_CHUNK_SIZE = 64 * 1024

# Limits of the API's X-Contact-* headers (characters, before percent-encoding).
MAX_EXTERNAL_ID_LENGTH = 255
MAX_EMAIL_LENGTH = 254
MAX_NAME_LENGTH = 200
MAX_LANGUAGE_LENGTH = 35

_TICKET_DATES = ("created_at", "updated_at", "closed_at", "reopen_until")


class HelpdeskError(Exception):
    """Base class of all errors of the client."""


class HelpdeskUnavailable(HelpdeskError):
    """The helpdesk cannot be reached or did not answer as the API does (timeout, 5xx, proxy page)."""


class HelpdeskApiError(HelpdeskError):
    """An error answer of the API: ``{"error": {"code", "message", "details"?}}``.

    ``code`` is what callers branch on (``not_found``, ``contact_blocked``,
    ``validation_error``, ``reopen_window_expired``, ...); ``message`` is English
    and meant for logs, not for users.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str = "",
        *,
        details: list[dict] | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(f"{status} {code}: {message}" if message else f"{status} {code}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []
        self.retry_after = retry_after


@dataclass(frozen=True)
class Contact:
    """The logged-in user of the website as the helpdesk sees them.

    ``external_id`` must be stable for the lifetime of the account (tickets hang
    on it); name, email and language are refreshed by the API on every call.
    """

    external_id: str
    email: str = ""
    name: str = ""
    language: str = ""

    def headers(self) -> dict[str, str]:
        """The ``X-Contact-*`` headers, percent-encoded UTF-8 as the API expects.

        Values the API would reject are dropped or shortened, so an odd profile
        never locks a user out of the support pages: an email the API would not
        accept is left out, name and language are cut to the API's limits.
        """
        external_id = str(self.external_id).strip()
        if not external_id or len(external_id) > MAX_EXTERNAL_ID_LENGTH:
            raise ValueError(f"external_id must have 1 to {MAX_EXTERNAL_ID_LENGTH} characters")
        headers = {"X-Contact-Id": _encode(external_id)}
        email = (self.email or "").strip()
        if email and _is_valid_email(email):
            headers["X-Contact-Email"] = _encode(email)
        name = " ".join((self.name or "").split())[:MAX_NAME_LENGTH]
        if name:
            headers["X-Contact-Name"] = _encode(name)
        language = (self.language or "").strip()[:MAX_LANGUAGE_LENGTH]
        if language:
            headers["X-Contact-Language"] = _encode(language)
        return headers


def _encode(value: str) -> str:
    return quote(value, safe="")


def _is_valid_email(value: str) -> bool:
    if len(value) > MAX_EMAIL_LENGTH:
        return False
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


class AttachmentDownload:
    """A streamed attachment from the API. Iterate for the bytes; always :meth:`close`.

    Django's ``StreamingHttpResponse`` calls :meth:`close` when it is done with it.
    """

    def __init__(self, response: requests.Response):
        self._response = response

    @property
    def content_type(self) -> str:
        return self._response.headers.get("Content-Type", "application/octet-stream")

    @property
    def content_disposition(self) -> str:
        return self._response.headers.get("Content-Disposition", "attachment")

    @property
    def content_length(self) -> str | None:
        return self._response.headers.get("Content-Length")

    def __iter__(self) -> Iterator[bytes]:
        return self._response.iter_content(DOWNLOAD_CHUNK_SIZE)

    def close(self) -> None:
        self._response.close()


# A file to upload: a Django UploadedFile (or any file object with .name), or a
# (filename, file object, content type) tuple as requests takes it.
UploadFile = Any


class HelpdeskClient:
    """Calls API v1. One instance per request is fine; it keeps one HTTP session.

    Arguments default to the settings ``HELPDESK_API_URL``, ``HELPDESK_API_KEY``
    and the ``HELPDESK_*TIMEOUT`` values.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        connect_timeout: float | None = None,
        timeout: float | None = None,
        upload_timeout: float | None = None,
        session: requests.Session | None = None,
    ):
        base_url = base_url or conf.get("HELPDESK_API_URL")
        api_key = api_key or conf.get("HELPDESK_API_KEY")
        if not base_url or not api_key:
            raise ImproperlyConfigured("helpdesk_client needs HELPDESK_API_URL and HELPDESK_API_KEY.")
        self.base_url = base_url.rstrip("/") + "/"
        self.connect_timeout = connect_timeout or conf.get("HELPDESK_CONNECT_TIMEOUT")
        self.timeout = timeout or conf.get("HELPDESK_TIMEOUT")
        self.upload_timeout = upload_timeout or conf.get("HELPDESK_UPLOAD_TIMEOUT")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": f"helpdesk-client/{__version__} {requests.utils.default_user_agent()}",
            }
        )

    # --- Endpoints ------------------------------------------------------------

    def config(self, language: str | None = None) -> dict:
        """Site config: ``{name, prefix, languages, language, categories, attachments}``.

        ``attachments`` holds ``max_files``, ``max_file_size`` (bytes), ``allowed_extensions``
        and ``content_types``. Cached use goes through :mod:`helpdesk_client.siteconfig`.
        """
        params = {"language": language} if language else None
        return self._json("GET", "config", params=params)

    def anonymize_contact(self, external_id: str) -> dict:
        """Anonymize the site's contact with this id: ``{anonymized, tickets}``.

        Repeatable and never 404: ``anonymized: false`` means there was nothing (left) to anonymize.
        The id is left out of the client's logs.
        """
        external_id = str(external_id).strip()
        if not external_id or len(external_id) > MAX_EXTERNAL_ID_LENGTH:
            raise ValueError(f"external_id must have 1 to {MAX_EXTERNAL_ID_LENGTH} characters")
        return self._json("DELETE", f"contacts/{_encode(external_id)}", log_path="contacts/<id>")

    def categories(self, language: str | None = None) -> list[dict]:
        """Active categories of the site; ``name`` in ``language`` (the site's default if omitted)."""
        params = {"language": language} if language else None
        return self._json("GET", "categories", params=params)

    def tickets(self, contact: Contact, *, page: int = 1, page_size: int = 20) -> dict:
        """One page of the contact's tickets: ``{items, count, page, page_size, pages}``."""
        data = self._json("GET", "tickets", contact, params={"page": page, "page_size": page_size})
        data["items"] = [_parse_ticket(item) for item in data.get("items", [])]
        return data

    def ticket(self, contact: Contact, number: int) -> dict:
        """The ticket with its public messages. Does not mark it read."""
        return _parse_ticket(self._json("GET", f"tickets/{int(number)}", contact))

    def create_ticket(
        self,
        contact: Contact,
        *,
        subject: str,
        body: str,
        category_id: int | None = None,
        language: str | None = None,
        metadata: dict | None = None,
        files: Iterable[UploadFile] = (),
    ) -> dict:
        """Open a ticket; returns it with its first message."""
        payload: dict[str, Any] = {"subject": subject, "body": body}
        if category_id is not None:
            payload["category_id"] = category_id
        if language:
            payload["language"] = language[:MAX_LANGUAGE_LENGTH]
        if metadata:
            payload["metadata"] = metadata
        return _parse_ticket(self._send_payload("tickets", contact, payload, files))

    def reply(self, contact: Contact, number: int, *, body: str, files: Iterable[UploadFile] = ()) -> dict:
        """Add the person's reply; returns the ticket. A closed ticket reopens within its window."""
        return _parse_ticket(self._send_payload(f"tickets/{int(number)}/messages", contact, {"body": body}, files))

    def mark_read(self, contact: Contact, number: int) -> int:
        """Mark the ticket read; returns the contact's remaining unread count."""
        return self._json("POST", f"tickets/{int(number)}/read", contact)["unread_count"]

    def close_ticket(self, contact: Contact, number: int) -> dict:
        """Close the ticket on behalf of the person; closing twice changes nothing."""
        return _parse_ticket(self._json("POST", f"tickets/{int(number)}/close", contact))

    def unread_count(self, contact: Contact) -> int:
        """Number of the contact's tickets with an agent reply they have not read."""
        return self._json("GET", "tickets/unread-count", contact)["unread_count"]

    def download_attachment(self, contact: Contact, number: int, attachment_id: int) -> AttachmentDownload:
        """Open an attachment as a stream. The caller must close the returned object."""
        response = self._request("GET", f"tickets/{int(number)}/attachments/{int(attachment_id)}", contact, stream=True)
        return AttachmentDownload(response)

    # --- Plumbing -------------------------------------------------------------

    def _send_payload(self, path: str, contact: Contact, payload: dict, files: Iterable[UploadFile]) -> dict:
        parts = [_file_part(file) for file in files]
        if not parts:
            return self._json("POST", path, contact, json=payload)
        # multipart: the JSON object as the form field "payload", one part per file.
        return self._json(
            "POST",
            path,
            contact,
            data={"payload": json.dumps(payload)},
            files=[("attachments", part) for part in parts],
            timeout=self.upload_timeout,
        )

    def _json(self, method: str, path: str, contact: Contact | None = None, **kwargs) -> Any:
        response = self._request(method, path, contact, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            log_path = kwargs.get("log_path") or path
            logger.warning("Helpdesk API %s %s answered %s without JSON", method, log_path, response.status_code)
            raise HelpdeskUnavailable("The helpdesk answered without JSON") from exc

    def _request(
        self,
        method: str,
        path: str,
        contact: Contact | None = None,
        *,
        timeout: float | None = None,
        stream: bool = False,
        log_path: str | None = None,
        **kwargs,
    ) -> requests.Response:
        # log_path replaces a path that carries personal data (an external id) in the logs.
        headers = contact.headers() if contact is not None else {}
        log_path = log_path or path
        try:
            response = self.session.request(
                method,
                self.base_url + path,
                headers=headers,
                timeout=(self.connect_timeout, timeout or self.timeout),
                stream=stream,
                allow_redirects=False,
                **kwargs,
            )
        except requests.Timeout as exc:
            logger.warning("Helpdesk API %s %s timed out", method, log_path)
            raise HelpdeskUnavailable("The helpdesk did not answer in time") from exc
        except requests.RequestException as exc:
            logger.warning("Helpdesk API %s %s failed: %s", method, log_path, exc.__class__.__name__)
            raise HelpdeskUnavailable("The helpdesk cannot be reached") from exc
        if response.status_code >= 300:
            try:
                raise _error_for(response, method, log_path)
            finally:
                response.close()
        return response


def _error_for(response: requests.Response, method: str, path: str) -> HelpdeskError:
    status = response.status_code
    error = None
    if "json" in response.headers.get("Content-Type", ""):
        try:
            error = response.json().get("error")
        except ValueError, AttributeError:
            error = None
    if status >= 500 or (not isinstance(error, dict) and status not in (413, 429)):
        # Server errors, redirects and pages of a proxy (wrong URL, maintenance).
        logger.error("Helpdesk API %s %s answered %s", method, path, status)
        return HelpdeskUnavailable(f"The helpdesk answered {status}")
    if not isinstance(error, dict):
        # nginx answers 413 and 429 itself, without the API's JSON.
        error = {"code": "request_too_large" if status == 413 else "rate_limited", "message": ""}
    code = str(error.get("code") or "error")
    level = logging.ERROR if code == "unauthorized" else logging.INFO
    logger.log(level, "Helpdesk API %s %s answered %s %s", method, path, status, code)
    retry_after = response.headers.get("Retry-After")
    return HelpdeskApiError(
        status,
        code,
        str(error.get("message") or ""),
        details=error.get("details") if isinstance(error.get("details"), list) else None,
        retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
    )


def _file_part(file: UploadFile) -> tuple:
    if isinstance(file, tuple):
        return file
    if hasattr(file, "seek"):
        file.seek(0)
    content_type = getattr(file, "content_type", None) or "application/octet-stream"
    return (_basename(file.name), file, content_type)


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _parse_datetime(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _parse_ticket(data: dict) -> dict:
    for key in _TICKET_DATES:
        if key in data:
            data[key] = _parse_datetime(data[key])
    for message in data.get("messages", []):
        message["created_at"] = _parse_datetime(message.get("created_at"))
    return data
