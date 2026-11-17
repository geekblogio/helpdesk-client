# helpdesk-client

Django-App, mit der eingeloggte Nutzer eines Webauftritts Anfragen an das selbst gehostete Helpdesk
([geekblogio/helpdesk](https://github.com/geekblogio/helpdesk)) stellen, verfolgen und beantworten.

Das Backend des Webauftritts spricht die Helpdesk-API v1 mit einem API-Key pro Site an
(Server-zu-Server); der Key verlässt nie den Server. Der Vertrag der API steht in
[`docs/api.md`](https://github.com/geekblogio/helpdesk/blob/main/docs/api.md) im Helpdesk-Repo.

Enthalten:

- API-Client mit Timeouts und einheitlicher Fehlerbehandlung (`helpdesk_client.client`)
- Seiten mit Login-Pflicht: „Meine Anfragen“, neue Anfrage (Kategorie, Anhänge), Detail mit
  Verlauf, Antworten, Ticket schließen, Download-Proxy für Anhänge. Ein ungelesenes Ticket wird beim
  Öffnen als gelesen markiert.
- Überschreibbare Templates mit schlichtem, semantischem Markup, Übersetzungen de/en
- System-Checks für die Settings

Python 3.14, Django 6.1, `requests`.

## Einbindung

### 1. Paket installieren

Das Paket wird per Git-Tag installiert:

```bash
poetry add git+ssh://git@github.com/geekblogio/helpdesk-client.git#v0.1.0
```

Bis zum ersten Tag (HD-17) geht `#main` oder ein Commit-Hash. Das Repo ist privat: Der Docker-Build
des Webauftritts braucht dafür Lesezugriff (Deploy-Key per SSH-Mount oder ein Token in der URL als
Build-Secret, nie im Image).

### 2. Settings

```python
INSTALLED_APPS = [
    # ...
    "django.contrib.auth",
    "django.contrib.messages",  # optional, für die Erfolgsmeldungen
    "helpdesk_client",
]

MIDDLEWARE = [
    # ...
    "django.middleware.locale.LocaleMiddleware",  # optional: Sprache der Seiten und des Kontakts
    # ...
]

HELPDESK_API_URL = get_env("HELPDESK_API_URL")  # z. B. https://help.webservercenter.de/api/v1/
HELPDESK_API_KEY = get_env("HELPDESK_API_KEY")  # hdk_..., im Helpdesk unter Sites → API-Keys
HELPDESK_BASE_TEMPLATE = "base.html"  # Layout des Webauftritts
```

`LOGIN_URL` muss auf die Anmeldung des Webauftritts zeigen; nicht angemeldete Besucher werden dorthin
umgeleitet.

| Setting | Default | Bedeutung |
|---|---|---|
| `HELPDESK_API_URL` | – (Pflicht) | Basis-URL der API v1. Prod `https://help.webservercenter.de/api/v1/`, Staging `https://dev.help.webservercenter.de/api/v1/` |
| `HELPDESK_API_KEY` | – (Pflicht) | API-Key der Site |
| `HELPDESK_CONNECT_TIMEOUT` | `5.0` | Sekunden für den Verbindungsaufbau |
| `HELPDESK_TIMEOUT` | `15.0` | Sekunden bis zur Antwort |
| `HELPDESK_UPLOAD_TIMEOUT` | `120.0` | Sekunden bis zur Antwort bei Requests mit Anhängen |
| `HELPDESK_BASE_TEMPLATE` | `"base.html"` | Template, das die Seiten erweitern; braucht die Blöcke `title` und `content` |
| `HELPDESK_CONTACT_RESOLVER` | `"helpdesk_client.contact.contact_from_request"` | `callable(request) -> Contact`, siehe unten |
| `HELPDESK_METADATA_RESOLVER` | `""` | `callable(request) -> dict` mit Kontextdaten für neue Tickets, z. B. Kundennummer |
| `HELPDESK_PAGE_SIZE` | `20` | Tickets pro Seite in „Meine Anfragen“ (1 bis 100) |

Fehlen URL oder Key oder sind Werte ungültig, meldet `manage.py check` Fehler
(`helpdesk_client.E001` bis `E006`; `W001` bei `http://` außerhalb von `DEBUG`). Der Client selbst
wirft `ImproperlyConfigured`, wenn URL oder Key fehlen.

### 3. URLs

```python
urlpatterns = [
    # ...
    path("support/", include("helpdesk_client.urls")),
]
```

| Name (`helpdesk_client:…`) | Pfad | Methode |
|---|---|---|
| `ticket_list` | `support/` | GET |
| `ticket_create` | `support/new/` | GET, POST |
| `ticket_detail` | `support/<number>/` | GET |
| `ticket_reply` | `support/<number>/reply/` | POST |
| `ticket_close` | `support/<number>/close/` | POST |
| `attachment_download` | `support/<number>/attachments/<id>/` | GET |

`<number>` ist die Ticketnummer innerhalb der Site (`12` für `WSC-12`).

### 4. Uploads

Anhänge: höchstens 5 Dateien je Nachricht, je 10 MB, PNG, JPEG, GIF, WebP, PDF, TXT, LOG. Das
Formular prüft Anzahl, Größe und Endung vorab; den Inhalt prüft die API. Der Webauftritt muss
Requests bis rund 55 MB annehmen, im Host-nginx also z. B. `client_max_body_size 60m;` für den Pfad
`/support/`. Djangos `DATA_UPLOAD_MAX_NUMBER_FILES` (Default 100) darf nicht unter 5 liegen.

## Anpassen

### Wer ist der Kontakt?

Standard (`helpdesk_client.contact.contact_from_request`): Primärschlüssel des Users als
`external_user_id`, `email`, `get_full_name()` und die aktive Sprache (`get_language()`). Die
`external_user_id` muss für die Lebensdauer des Kontos stabil sein, an ihr hängen die Tickets.
Name, E-Mail und Sprache aktualisiert das Helpdesk bei jedem Aufruf. Eine E-Mail, die die API
ablehnen würde, wird weggelassen; Name und Sprache werden auf die Längen der API gekürzt.

Eigene Variante:

```python
# myproject/helpdesk.py
from django.utils import translation
from helpdesk_client.client import Contact


def contact(request):
    user = request.user
    return Contact(
        external_id=str(user.pk),
        email=user.email,
        name=user.customer.display_name,
        language=translation.get_language(),
    )


def metadata(request):
    return {"customer_number": request.user.customer.number}
```

```python
HELPDESK_CONTACT_RESOLVER = "myproject.helpdesk.contact"
HELPDESK_METADATA_RESOLVER = "myproject.helpdesk.metadata"
```

### Templates

Alle Seiten erweitern `helpdesk_client/base.html`, das wiederum `HELPDESK_BASE_TEMPLATE` erweitert und
die Blöcke `title` und `content` füllt. Heißen die Blöcke im Layout anders oder zeigt das Layout
Django-Messages schon selbst an, überschreibt der Webauftritt `helpdesk_client/base.html`
(Block `helpdesk_messages` leer lassen).

Überschreiben geht wie bei jeder Django-App: eine Datei mit gleichem Pfad in einem Template-Verzeichnis
des Webauftritts, das vor den App-Templates gesucht wird (`DIRS` oder eine App vor
`helpdesk_client` in `INSTALLED_APPS`).

| Template | Inhalt | Kontext |
|---|---|---|
| `helpdesk_client/base.html` | Rahmen, Messages | `helpdesk_base_template` |
| `helpdesk_client/ticket_list.html` | „Meine Anfragen“ | `tickets`, `count`, `page`, `pages`, `previous_page`, `next_page` |
| `helpdesk_client/ticket_form.html` | neue Anfrage | `form` |
| `helpdesk_client/ticket_detail.html` | Verlauf, Antworten, Schließen | `ticket`, `reply_form`, `reopen_window_expired` |
| `helpdesk_client/error.html` | API nicht erreichbar, gesperrt, Rate-Limit | `error_title`, `error_message` |
| `helpdesk_client/includes/field.html` | ein Formularfeld | `field` |
| `helpdesk_client/includes/form_errors.html` | Fehler ohne Feld | `form` |

`ticket` und die Listeneinträge sind die Objekte der API (`number`, `display_number`, `subject`,
`status`, `category`, `unread`, `messages`, `can_reply`, `can_close`, `reopen_until`, …); Datumsfelder
sind bereits `datetime`-Objekte. `message.body_html` ist von der API gerendert und mit nh3 bereinigt
und wird mit `|safe` ausgegeben.

Das Markup bringt kein CSS mit. Die Klassen beginnen mit `helpdesk-`, z. B. `helpdesk-ticket--unread`,
`helpdesk-status--waiting`, `helpdesk-entry--agent`, `helpdesk-field--invalid`. Den Status als Text
liefert der Filter `{{ ticket.status|helpdesk_status }}` aus `{% load helpdesk_client %}`.

### Texte

Die Texte sind englisch im Code, deutsch in `helpdesk_client/locale/de` (Anrede „Sie“). Eigene
Formulierungen kommen über `LOCALE_PATHS` des Webauftritts, die vor den App-Übersetzungen gelten.

## Verhalten bei Fehlern

| Antwort der API | Seite |
|---|---|
| `404 not_found` (fremdes oder unbekanntes Ticket) | 404 |
| `403 contact_blocked` | 403 mit Hinweis |
| `429 rate_limited` | 429 mit Hinweis |
| `422` beim Anlegen/Antworten | Formular mit Fehlern an den Feldern bzw. am Anhang |
| `409 reopen_window_expired` beim Antworten | Detail mit Hinweis und Link auf eine neue Anfrage |
| `401`, `5xx`, Timeout, keine Verbindung, fremde Seite | 503 „vorübergehend nicht erreichbar“, Log-Eintrag |

Logs enthalten Methode, Pfad (mit Ticketnummer), Status und Fehlercode, keine personenbezogenen Daten.
`401 unauthorized` (falscher oder gesperrter Key) wird als Error geloggt.

## Client direkt nutzen

```python
from helpdesk_client.client import Contact, HelpdeskApiError, HelpdeskClient, HelpdeskUnavailable

client = HelpdeskClient()  # liest die Settings
contact = Contact(external_id="42", email="anna@example.com", name="Anna Muster", language="de")
ticket = client.create_ticket(contact, subject="Login klappt nicht", body="Seit heute …", category_id=3)
client.unread_count(contact)
```

Methoden: `categories`, `tickets`, `ticket`, `create_ticket`, `reply`, `mark_read`, `close_ticket`,
`unread_count`, `download_attachment`. API-Fehler kommen als `HelpdeskApiError` mit `status`, `code`,
`details` und `retry_after`; alles, was nicht die API selbst beantwortet hat, als `HelpdeskUnavailable`.

## Entwicklung

```bash
poetry install
poetry run python manage.py test
poetry run ruff check . && poetry run ruff format --check .
poetry run coverage run manage.py test && poetry run coverage report
```

Die Tests laufen gegen eine mit [`responses`](https://github.com/getsentry/responses) gemockte API im
Testprojekt unter `tests/` (`tests/settings.py`, SQLite). Dasselbe Projekt eignet sich zum Durchklicken
gegen Staging:

```bash
export HELPDESK_API_URL=https://dev.help.webservercenter.de/api/v1/ HELPDESK_API_KEY=hdk_...
poetry run python manage.py migrate
poetry run python manage.py createsuperuser
poetry run python manage.py runserver   # Anmeldung unter /admin/, Seiten unter /support/
```

Übersetzungen aktualisieren und kompilieren (in einem Wegwerf-Container mit gettext; die `.mo`-Dateien
werden eingecheckt, weil Installationen per Git-Tag aus den Quellen bauen):

```bash
scripts/translations.sh           # makemessages de/en + compilemessages
scripts/translations.sh compile   # nur compilemessages
```

## Ausblick (HD-17)

Noch nicht enthalten: Ungelesen-Badge als Template-Tag (gehört nach
`helpdesk_client/templatetags/helpdesk_client.py`, Grundlage `HelpdeskClient.unread_count`),
Lösch-Hook beim Löschen des Users, Webhook-Empfänger, CI und Release.
