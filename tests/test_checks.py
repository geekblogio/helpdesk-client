from django.test import SimpleTestCase, override_settings

from helpdesk_client.checks import check_settings


def ids(errors):
    return sorted(error.id for error in errors)


class CheckTests(SimpleTestCase):
    def test_test_settings_pass(self):
        self.assertEqual(check_settings(), [])

    @override_settings(HELPDESK_API_URL="", HELPDESK_API_KEY="")
    def test_missing_url_and_key(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E001", "helpdesk_client.E003"])

    def test_missing_settings_fall_back_to_empty_defaults(self):
        with self.settings():
            from django.conf import settings

            del settings.HELPDESK_API_URL
            self.assertEqual(ids(check_settings()), ["helpdesk_client.E001"])

    @override_settings(HELPDESK_API_URL="help.webservercenter.de/api/v1/")
    def test_url_needs_scheme(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E002"])

    @override_settings(HELPDESK_API_URL="http://help.example/api/v1/", DEBUG=False)
    def test_plain_http_warns_outside_debug(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.W001"])

    @override_settings(HELPDESK_API_URL="http://localhost:8000/api/v1/", DEBUG=True)
    def test_plain_http_is_fine_in_debug(self):
        self.assertEqual(check_settings(), [])

    @override_settings(HELPDESK_TIMEOUT=0, HELPDESK_CONNECT_TIMEOUT="5", HELPDESK_UPLOAD_TIMEOUT=True)
    def test_timeouts_must_be_positive_numbers(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E004"] * 3)

    @override_settings(HELPDESK_PAGE_SIZE=500)
    def test_page_size_within_api_limit(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E005"])

    @override_settings(HELPDESK_CONTACT_RESOLVER="nowhere.contact", HELPDESK_METADATA_RESOLVER="nowhere.meta")
    def test_resolvers_must_import(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E006"] * 2)

    @override_settings(HELPDESK_CONTACT_RESOLVER="")
    def test_contact_resolver_is_required(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E006"])

    @override_settings(HELPDESK_USER_ID_RESOLVER="nowhere.user_id")
    def test_user_id_resolver_must_import(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E006"])

    @override_settings(HELPDESK_USER_ID_RESOLVER="")
    def test_user_id_resolver_is_required(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E006"])

    @override_settings(HELPDESK_UNREAD_CACHE_TIMEOUT=-1, HELPDESK_CONFIG_CACHE_TIMEOUT=1.5, HELPDESK_UNREAD_TIMEOUT=0)
    def test_cache_timeouts_and_badge_timeout(self):
        self.assertEqual(
            ids(check_settings()), ["helpdesk_client.E004", "helpdesk_client.E007", "helpdesk_client.E007"]
        )

    @override_settings(HELPDESK_CACHE="redis")
    def test_cache_alias_must_exist(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E008"])

    @override_settings(HELPDESK_WEBHOOK_SECRET=None)
    def test_webhook_secret_must_be_a_string(self):
        self.assertEqual(ids(check_settings()), ["helpdesk_client.E009"])
