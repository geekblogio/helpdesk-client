# CLAUDE.md

Hinweise für Claude Code in diesem Repository.

## Interaktion

- Christian schreibt Deutsch; antworte auf Deutsch. Code und Kommentare auf Englisch.
- **Review vor Commit/Push:** Nicht ungefragt committen oder pushen (Ausnahme: im Auftrag einer
  Session ausdrücklich verlangt). Neuer Code in Feature-Branches, Merge per PR.

## Projekt

Wiederverwendbare Django-App `helpdesk_client` für die Webauftritte, Gegenstück zur API v1 im Repo
`geekblogio/helpdesk` (verbindlich: `docs/planung.md` und `docs/api.md` dort). Aufgaben in Vikunja,
Projekt **Helpdesk** (id 7), Pakete HD-16/HD-17; Arbeitsweise wie in der `CLAUDE.md` des Helpdesk-Repos.

```
helpdesk_client/
  client.py      API-Client (requests): Contact, HelpdeskClient, HelpdeskApiError, HelpdeskUnavailable
  conf.py        Settings mit Defaults (immer zur Laufzeit lesen: conf.get)
  checks.py      System-Checks helpdesk_client.E001…
  contact.py     request -> Contact, Metadaten (beides per Setting austauschbar)
  forms.py       Formulare, Anhang-Regeln wie in der API
  views.py       Views mit Login-Pflicht; API-Fehler -> Fehlerseiten
  templates/     überschreibbar, schlichtes Markup, Klassen mit Präfix helpdesk-
  locale/        de, en; .mo-Dateien sind eingecheckt
tests/           Testprojekt (settings, urls, base.html) und Tests gegen eine mit responses gemockte API
```

## Befehle

```bash
poetry install
poetry run python manage.py test
poetry run ruff check . && poetry run ruff format --check .
scripts/translations.sh            # makemessages + compilemessages im Container
```

## Konventionen

- Die API ist die Autorität: Der Client spiegelt ihre Regeln nur für bessere Fehlermeldungen.
  Ändert sich der Vertrag in `docs/api.md`, hier Client, Tests und README anpassen.
- UI-Texte englisch mit `gettext`/`{% translate %}`, deutsch mit „Sie“ (Ton der Webauftritte).
  Nach Textänderungen `scripts/translations.sh` und die `.mo`-Dateien mit committen.
- Keine personenbezogenen Daten in Logs (nur Methode, Pfad, Status, Fehlercode).
- ruff ist die Norm (`pyproject.toml`).
