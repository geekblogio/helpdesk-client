from io import StringIO
from unittest import mock

import requests
import responses
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import CommandError, call_command
from django.db import DatabaseError, transaction
from django.test import override_settings

from helpdesk_client.cache import set_unread_count, unread_key
from helpdesk_client.models import PendingAnonymization

from .support import MockedApiTestCase, error_json, url

User = get_user_model()


def secret_id(user) -> str:
    return f"cp/{user.username}"


class DeleteHookTests(MockedApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("anna", email="anna@example.com", password="x")

    def delete_user(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()

    def test_deleting_a_user_anonymizes_the_contact(self):
        pk = self.user.pk
        set_unread_count(str(pk), 3)
        self.api.delete(url(f"contacts/{pk}"), json={"anonymized": True, "tickets": 2})
        with self.assertLogs("helpdesk_client.hooks", "INFO") as logs:
            self.delete_user()
        self.assertFalse(User.objects.filter(pk=pk).exists())
        self.assertEqual(len(self.api.calls), 1)
        request = self.api.calls[0].request
        self.assertEqual(request.headers["Authorization"], "Bearer hdk_test_key")
        self.assertNotIn("X-Contact-Id", request.headers)
        self.assertFalse(PendingAnonymization.objects.exists())
        self.assertIsNone(caches["default"].get(unread_key(str(pk))))
        self.assertIn("Helpdesk contact anonymized (2 tickets)", logs.output[-1])

    @override_settings(HELPDESK_USER_ID_RESOLVER="tests.test_hooks.secret_id")
    def test_id_comes_from_the_resolver_and_is_quoted_and_not_logged(self):
        self.api.delete(url("contacts/cp%2Fanna"), status=503, body="<html>")
        with self.assertLogs("helpdesk_client", "INFO") as logs:
            self.delete_user()
        self.assertEqual(self.api.calls[0].request.url, url("contacts/cp%2Fanna"))
        output = "\n".join(logs.output)
        self.assertNotIn("anna", output)
        self.assertIn("contacts/<id>", output)
        self.assertEqual(PendingAnonymization.objects.get().external_id, "cp/anna")

    @override_settings(HELPDESK_DELETE_HOOK=False)
    def test_hook_can_be_switched_off(self):
        self.delete_user()
        self.assertEqual(len(self.api.calls), 0)
        self.assertFalse(PendingAnonymization.objects.exists())

    def test_api_errors_do_not_block_the_deletion(self):
        answers = [
            {"status": 503, "body": "<html>"},
            {"status": 401, "json": error_json("unauthorized")},
            {"body": requests.ConnectionError()},
            {"body": requests.ReadTimeout()},
        ]
        for kwargs in answers:
            with self.subTest(answer=kwargs):
                self.user = User.objects.create_user(f"user{len(self.api.calls)}")
                pk = self.user.pk
                self.api.upsert(responses.DELETE, url(f"contacts/{pk}"), **kwargs)
                with self.assertLogs("helpdesk_client", "WARNING") as logs:
                    self.delete_user()
                self.assertFalse(User.objects.filter(pk=pk).exists())
                pending = PendingAnonymization.objects.get(external_id=str(pk))
                self.assertEqual(pending.attempts, 1)
                self.assertIsNotNone(pending.last_attempt_at)
                self.assertIn(f"pending anonymization #{pending.pk}", logs.output[-1])
        reasons = list(PendingAnonymization.objects.values_list("last_error", flat=True))
        self.assertEqual(
            reasons, ["HelpdeskUnavailable", "401 unauthorized", "HelpdeskUnavailable", "HelpdeskUnavailable"]
        )

    @override_settings(HELPDESK_API_URL="")
    def test_missing_configuration_does_not_block_the_deletion(self):
        with self.assertLogs("helpdesk_client.hooks", "WARNING") as logs:
            self.delete_user()
        self.assertFalse(User.objects.filter(username="anna").exists())
        self.assertIn("ImproperlyConfigured", logs.output[-1])
        self.assertEqual(PendingAnonymization.objects.count(), 1)

    def test_unusable_pending_table_does_not_block_the_deletion(self):
        pk = self.user.pk
        self.api.delete(url(f"contacts/{pk}"), status=503, body="")
        broken = mock.patch.object(PendingAnonymization.objects, "get_or_create", side_effect=DatabaseError("no table"))
        with broken, self.assertLogs("helpdesk_client", "WARNING") as logs:
            self.delete_user()
        self.assertFalse(User.objects.filter(pk=pk).exists())
        self.assertEqual(len(self.api.calls), 1)
        output = "\n".join(logs.output)
        self.assertIn("Could not record the pending anonymization (DatabaseError)", output)
        self.assertIn("Could not record the failed anonymization (DatabaseError)", output)
        self.assertIn("pending anonymization #?", output)

    @override_settings(HELPDESK_USER_ID_RESOLVER="tests.test_hooks.missing")
    def test_broken_resolver_does_not_block_the_deletion(self):
        with self.assertLogs("helpdesk_client.hooks", "ERROR"):
            self.delete_user()
        self.assertFalse(User.objects.filter(username="anna").exists())
        self.assertEqual(len(self.api.calls), 0)

    def test_rolled_back_deletion_calls_nothing(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    self.user.delete()
                    raise RuntimeError("rollback")
            except RuntimeError:
                pass
        self.assertEqual(callbacks, [])
        self.assertEqual(len(self.api.calls), 0)
        self.assertTrue(User.objects.filter(username="anna").exists())
        self.assertFalse(PendingAnonymization.objects.exists())

    def test_bulk_deletion_anonymizes_every_user(self):
        other = User.objects.create_user("ben")
        for user in (self.user, other):
            self.api.delete(url(f"contacts/{user.pk}"), json={"anonymized": False, "tickets": 0})
        with self.assertLogs("helpdesk_client.hooks", "INFO"), self.captureOnCommitCallbacks(execute=True):
            User.objects.filter(username__in=["anna", "ben"]).delete()
        self.assertEqual(len(self.api.calls), 2)
        self.assertFalse(PendingAnonymization.objects.exists())


class AnonymizeCommandTests(MockedApiTestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command("helpdesk_anonymize", *args, stdout=out)
        return out.getvalue()

    def test_retries_pending_anonymizations(self):
        PendingAnonymization.objects.create(external_id="7", attempts=2)
        PendingAnonymization.objects.create(external_id="8", attempts=1)
        self.api.delete(url("contacts/7"), json={"anonymized": True, "tickets": 1})
        self.api.delete(url("contacts/8"), json={"anonymized": False, "tickets": 0})
        with self.assertLogs("helpdesk_client.hooks", "INFO"):
            self.assertEqual(self.run_command(), "2 anonymized, 0 failed.\n")
        self.assertFalse(PendingAnonymization.objects.exists())

    def test_failures_stay_pending_and_fail_the_command(self):
        PendingAnonymization.objects.create(external_id="7", attempts=2)
        PendingAnonymization.objects.create(external_id="8")
        self.api.delete(url("contacts/7"), status=500, json=error_json("server_error"))
        self.api.delete(url("contacts/8"), json={"anonymized": True, "tickets": 0})
        with self.assertLogs("helpdesk_client", "INFO"), self.assertRaises(CommandError) as raised:
            self.run_command()
        self.assertEqual(raised.exception.returncode, 1)
        pending = PendingAnonymization.objects.get()
        self.assertEqual((pending.external_id, pending.attempts), ("7", 3))

    def test_anonymizes_given_ids(self):
        self.api.delete(url("contacts/a%2Fb"), json={"anonymized": True, "tickets": 0})
        self.api.delete(url("contacts/9"), json={"anonymized": True, "tickets": 0})
        with self.assertLogs("helpdesk_client.hooks", "INFO"):
            self.assertEqual(self.run_command("--external-id", "a/b", "9"), "2 anonymized, 0 failed.\n")
        self.assertEqual(len(self.api.calls), 2)

    def test_nothing_pending(self):
        self.assertEqual(self.run_command(), "0 anonymized, 0 failed.\n")
