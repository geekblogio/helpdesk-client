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
- Ungelesen-Badge als Template-Tag, gecacht ([Badge](#ungelesen-badge))
- Lösch-Hook: Wird ein User gelöscht, anonymisiert das Helpdesk seinen Kontakt ([Lösch-Hook](#lösch-hook))
- Optionaler Webhook-Empfänger mit Signaturprüfung und Django-Signal ([Webhooks](#webhook-empfänger))
- Kategorien und Anhang-Regeln aus `GET /config` der API, gecacht
- Überschreibbare Templates mit schlichtem, semantischem Markup, Übersetzungen de/en
- System-Checks für die Settings

Python 3.14+, Django 6.1+, `requests`.

## Einbindung

### 1. Paket installieren

Das Paket wird per Git-Tag installiert, z. B. in der `pyproject.toml` des Webauftritts:

```bash
poetry add git+ssh://git@github.com/geekblogio/helpdesk-client.git#v0.1.0
```

```toml
[tool.poetry.dependencies]
helpdesk-client = { git = "ssh://git@github.com/geekblogio/helpdesk-client.git", tag = "v0.1.0" }
```

Ein Update ist ein neuer Tag in der URL bzw. bei `tag` und `poetry update helpdesk-client`. Die
Releases mit Änderungen stehen unter
[Releases](https://github.com/geekblogio/helpdesk-client/releases). Das Repo ist privat: Der
Docker-Build des Webauftritts braucht dafür Lesezugriff, entweder einen Deploy-Key per SSH-Mount
(`RUN --mount=type=ssh poetry install …`) oder ein Token in der URL als Build-Secret, nie im Image.

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
HELPDESK_WEBHOOK_SECRET = get_env("HELPDESK_WEBHOOK_SECRET", "")  # optional, siehe Webhook-Empfänger
```

Danach `manage.py migrate`: Die App hat eine Tabelle für ausstehende Anonymisierungen (Lösch-Hook).

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
| `HELPDESK_USER_ID_RESOLVER` | `"helpdesk_client.contact.external_id_for_user"` | `callable(user) -> str`: die stabile `external_user_id` (Default: Primärschlüssel). Nutzen der Standard-Kontakt und der Lösch-Hook |
| `HELPDESK_METADATA_RESOLVER` | `""` | `callable(request) -> dict` mit Kontextdaten für neue Tickets, z. B. Kundennummer |
| `HELPDESK_PAGE_SIZE` | `20` | Tickets pro Seite in „Meine Anfragen“ (1 bis 100) |
| `HELPDESK_CACHE` | `"default"` | Alias in `CACHES` für Badge, Site-Konfiguration und Webhook-IDs |
| `HELPDESK_UNREAD_CACHE_TIMEOUT` | `60` | Sekunden, die der Badge seine Zahl cacht |
| `HELPDESK_UNREAD_TIMEOUT` | `2.0` | Sekunden, die der Badge höchstens auf die API wartet (Verbindung und Antwort) |
| `HELPDESK_CONFIG_CACHE_TIMEOUT` | `300` | Sekunden, die Kategorien und Anhang-Regeln aus `GET /config` gecacht werden |
| `HELPDESK_DELETE_HOOK` | `True` | Kontakt im Helpdesk anonymisieren, wenn der User gelöscht wird |
| `HELPDESK_WEBHOOK_SECRET` | `""` | Secret des Webhooks der Site; leer schaltet den Empfänger ab (404) |

Fehlen URL oder Key oder sind Werte ungültig, meldet `manage.py check` Fehler
(`helpdesk_client.E001` bis `E009`; `W001` bei `http://` außerhalb von `DEBUG`). Der Client selbst
wirft `ImproperlyConfigured`, wenn URL oder Key fehlen.

**Cache:** Badge-Zahlen, Site-Konfiguration und gesehene Webhook-IDs liegen im Django-Cache. Läuft
der Webauftritt mit mehreren Prozessen (gunicorn-Worker), gehört dafür ein gemeinsamer Cache her
(Redis, Memcached). Mit dem prozesslokalen `LocMemCache` löscht ein Webhook die Zahl nur im
Prozess, der ihn bekommen hat; die anderen zeigen sie bis zum Ablauf der TTL weiter an.

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
| `webhook` | `support/webhook/` | POST, ohne Login (siehe [Webhook-Empfänger](#webhook-empfänger)) |

`<number>` ist die Ticketnummer innerhalb der Site (`12` für `WSC-12`).

### 4. Uploads

Die Regeln für Anhänge (Anzahl, Größe, Endungen) und die Kategorien holt das Paket mit
`GET /api/v1/config` und cacht sie `HELPDESK_CONFIG_CACHE_TIMEOUT` Sekunden pro Sprache. Das Formular
prüft Anzahl, Größe und Endung vorab und nennt die Regeln im Hilfetext; den Inhalt prüft die API.
Ist die Konfiguration nicht abrufbar, gelten die Regeln der API v1 von heute (5 Dateien je Nachricht,
je 10 MB, PNG, JPEG, GIF, WebP, PDF, TXT, LOG) und die Kategorien kommen einzeln aus
`GET /categories`. Ein Fehlschlag wird eine Minute gemerkt.

Der Webauftritt muss Requests bis rund 55 MB annehmen, im Host-nginx also z. B.
`client_max_body_size 60m;` für den Pfad `/support/`. Djangos `DATA_UPLOAD_MAX_NUMBER_FILES`
(Default 100) darf nicht unter der Höchstzahl der Dateien liegen.

### 5. Ungelesen-Badge

```django
{% load helpdesk_client %}
<a href="{% url 'helpdesk_client:ticket_list' %}">Support {% helpdesk_unread_badge %}</a>
```

`{% helpdesk_unread_badge %}` rendert `<span class="helpdesk-badge" title="3 ungelesene Antworten">3</span>`
und bei 0, für Gäste oder ohne erreichbares Helpdesk nichts (Template
`helpdesk_client/includes/unread_badge.html`, überschreibbar). Für eigenes Markup gibt es die Zahl
allein: `{% helpdesk_unread_count as count %}` (`None`, wenn unbekannt). Beide brauchen `request` im
Kontext (Context-Processor `django.template.context_processors.request`).

Die Zahl wird pro Kontakt `HELPDESK_UNREAD_CACHE_TIMEOUT` Sekunden gecacht (Default 60). Öffnet die
Person ein ungelesenes Ticket, steht danach die neue Zahl im Cache. Ein Webhook des Helpdesks löscht
sie, damit eine neue Antwort sofort erscheint. Der Badge wartet höchstens `HELPDESK_UNREAD_TIMEOUT`
Sekunden auf die API. Fällt sie aus, zeigt er nichts an und merkt sich das ebenfalls für die TTL,
damit nicht jede Seite auf die API wartet.

### 6. Lösch-Hook

Wird ein User gelöscht (`post_delete` auf `AUTH_USER_MODEL`, auch bei `QuerySet.delete()`), ruft das
Paket nach dem Commit `DELETE /api/v1/contacts/{external_user_id}` auf. Das Helpdesk anonymisiert den
Kontakt dieser Site samt Tickets und Anhängen (siehe `docs/api.md`, „Kontakt anonymisieren“). Der
Aufruf ist wiederholbar und antwortet nie mit 404.

- Das Löschen des Users scheitert nie am Hook: Jeder Fehler wird abgefangen. Wird das Löschen
  zurückgerollt, ruft der Hook nichts auf.
- Im selben Commit wie das Löschen entsteht ein Eintrag `PendingAnonymization` (nur die
  `external_user_id`). Bestätigt die API, wird er gelöscht; scheitert der Aufruf (Helpdesk nicht
  erreichbar, falscher Key, …), bleibt er mit Zahl der Versuche und letztem Fehler stehen.
- Logs enthalten nur die Nummer des Eintrags und Status/Fehlercode, nie die ID.
- Dauert der Aufruf, wartet die Antwort des Löschens bis zu `HELPDESK_CONNECT_TIMEOUT` +
  `HELPDESK_TIMEOUT` Sekunden. Wer das nicht will, schaltet den Hook ab und ruft
  `helpdesk_client.hooks.anonymize(external_id)` aus einem eigenen Hintergrund-Task auf.

**Wiederholen:** Den Command per Cron laufen lassen, z. B. stündlich:

```bash
python manage.py helpdesk_anonymize                       # alle ausstehenden noch einmal
python manage.py helpdesk_anonymize --external-id 42 43   # bestimmte Kontakte, z. B. vor der Einbindung gelöschte User
```

Er gibt `2 anonymized, 0 failed.` aus und endet mit Status 1, wenn ein Aufruf scheitert; die
Einträge bleiben dann stehen. Im Django-Admin stehen sie unter „Support → Ausstehende
Anonymisierungen“ (nur lesen, löschen geht).

Abschalten: `HELPDESK_DELETE_HOOK = False`. User, die per SQL oder `raw` am ORM vorbei gelöscht
werden, sieht der Hook nicht; für sie bleibt der Command mit `--external-id`.

### 7. Webhook-Empfänger

Optional. Das Helpdesk meldet neue Tickets, Antworten des Agenten und Statuswechsel
(`ticket.created`, `ticket.replied`, `ticket.status_changed`) an eine URL des Webauftritts.

1. Im Helpdesk unter Webauftritte → Webhook die URL eintragen, z. B.
   `https://www.webservercenter.de/support/webhook/` (nur `https`), und ein Secret erzeugen. Das
   Secret wird nur einmal angezeigt.
2. Im Webauftritt `HELPDESK_WEBHOOK_SECRET` auf dieses Secret setzen (aus der Umgebung, nicht im
   Repo). Ohne Secret antwortet die URL mit 404.
3. Im Helpdesk unter Webauftritte → Webhook-Log prüfen, dass die Zustellungen ankommen
   (`zugestellt`). Ein neues Secret gilt im Helpdesk sofort; den Wert im Webauftritt gleich danach
   tauschen.

Die View prüft die Signatur genau wie in `docs/api.md` beschrieben: `X-Helpdesk-Signature: v1=<hex>`
ist HMAC-SHA256 mit dem Secret über `"<X-Helpdesk-Timestamp>.<roher Body>"`, der Zeitstempel darf
höchstens 300 Sekunden von der eigenen Uhr abweichen (die Server-Uhr muss also per NTP stimmen).
Mehrere Signaturen (`v1=…,v1=…`) sind erlaubt, eine muss passen. CSRF gilt für die URL nicht.

| Antwort | Wann |
|---|---|
| `204` | verarbeitet, oder dieselbe `id` kam in der letzten Stunde schon |
| `400` | Body ist kein JSON-Objekt mit `id` |
| `401` | Signatur fehlt, ist falsch oder abgelaufen |
| `404` | kein `HELPDESK_WEBHOOK_SECRET` gesetzt |
| `500` | ein Receiver des Signals ist gescheitert; das Helpdesk stellt später erneut zu |

Ein gültiges Ereignis löscht den Badge-Cache von `external_user_id` und sendet danach das Signal
`helpdesk_client.signals.webhook_received` mit `event`, `payload` (der Body als dict) und `request`:

```python
from django.dispatch import receiver
from helpdesk_client.signals import webhook_received


@receiver(webhook_received)
def on_helpdesk_event(sender, event, payload, **kwargs):
    if event == "ticket.replied":
        notify_user_later.delay(payload["external_user_id"], payload["number"])  # schnell bleiben
```

Das Helpdesk wartet höchstens 5 Sekunden auf die Antwort; Receiver sollten Arbeit in den Hintergrund
legen. Unbekannte Events und Felder ignorieren. Die Reihenfolge der Events ist nicht garantiert,
maßgeblich ist der Stand über die API.

## Anpassen

### Wer ist der Kontakt?

Standard (`helpdesk_client.contact.contact_from_request`): `HELPDESK_USER_ID_RESOLVER` (Default:
Primärschlüssel des Users) als `external_user_id`, `email`, `get_full_name()` und die aktive Sprache
(`get_language()`). Die `external_user_id` muss für die Lebensdauer des Kontos stabil sein, an ihr
hängen die Tickets. Wer eine andere ID will, setzt `HELPDESK_USER_ID_RESOLVER`
(`callable(user) -> str`), denn der Lösch-Hook braucht dieselbe ID ohne Request. Ein eigener
`HELPDESK_CONTACT_RESOLVER` muss dieselbe ID liefern, sonst anonymisiert der Hook den falschen
Kontakt (oder keinen).
Name, E-Mail und Sprache aktualisiert das Helpdesk bei jedem Aufruf. Eine E-Mail, die die API
ablehnen würde, wird weggelassen; Name und Sprache werden auf die Längen der API gekürzt.

Eigene Variante:

```python
# myproject/helpdesk.py
from django.utils import translation
from helpdesk_client.client import Contact
from helpdesk_client.contact import get_external_id


def contact(request):
    user = request.user
    return Contact(
        external_id=get_external_id(user),
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
| `helpdesk_client/includes/unread_badge.html` | Ungelesen-Badge | `unread_count` |

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
| `422` beim Anlegen/Antworten | Formular mit Fehlern an den Feldern (über `details[].loc`) bzw. am Anhang |
| `422 invalid_input` mit `loc` auf `category_id` (Kategorie inaktiv oder unbekannt) | Fehler an der Kategorie, Kategorien neu geladen |
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

Methoden: `config`, `categories`, `tickets`, `ticket`, `create_ticket`, `reply`, `mark_read`,
`close_ticket`, `unread_count`, `download_attachment`, `anonymize_contact`. Die gecachte
Konfiguration liefert `helpdesk_client.siteconfig.get_site_config(language)`. API-Fehler kommen als `HelpdeskApiError` mit `status`, `code`,
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

## CI und Release

GitHub Actions (`.github/workflows/test.yml`) prüft jeden PR und jeden Push auf `main`: ruff, fehlende
Migrationen und die Tests mit Abdeckung (`fail_under = 90`), als Matrix über Python 3.14/3.15 und
Django 6.1. Die Jobs laufen auf GitHub-gehosteten Runnern (`ubuntu-latest`): Das Paket braucht weder
Docker noch Dienste, und der Self-hosted-Runner des Helpdesk-Repos ist nur für dieses Repo registriert.

Release: Version in `pyproject.toml` und `helpdesk_client/__init__.py` anheben (per PR), nach dem
Merge auf `main` taggen und pushen:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Der Workflow `.github/workflows/release.yml` prüft, dass Tag und Version übereinstimmen, und legt ein
GitHub-Release mit Notizen aus den PRs an (Gruppen nach Labels, `.github/release.yml`).
