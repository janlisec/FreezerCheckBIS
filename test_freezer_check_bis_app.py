import json
import os
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from freezer_check_bis_service import (
    Freezer,
    OpenBISSession,
    OpenBISStartup,
    PersonOption,
)
from freezer_check_bis_app import create_app


TEST_CONFIG = {
    "instances": {
        "playground": {
            "url": "https://playground.example.invalid/openbis",
            "freezer_collection": "/SPACE/PROJECT/FREEZER_COLLECTION",
        },
        "main": {
            "url": "https://main.example.invalid/openbis",
            "freezer_collection": "/SPACE/PROJECT/MAIN_FREEZERS",
        },
    },
    "schema": {
        "freezer_type": "FREEZER_TYPE",
        "freezer_name_property": "$name",
        "freezer_location_property": "location_property",
        "freezer_temp_min_property": "minimum_property",
        "freezer_temp_max_property": "maximum_property",
        "person_type": "PERSON_TYPE",
        "person_username_property": "username_property",
        "person_name_property": "$name",
        "person_given_name_property": "given_name",
        "person_family_name_property": "family_name",
        "person_organizational_entity_property": "organization_property",
        "person_organizational_entity_prefix": "ORG_",
        "control_collection": "/SPACE/PROJECT/CONTROL_COLLECTION",
        "control_type": "CONTROL_TYPE",
        "object_name_property": "$name",
        "monitoring_date_property": "date_property",
        "temp_min_property": "minimum_property",
        "temp_max_property": "maximum_property",
        "acting_person_property": "person_property",
    },
}


class FreezerCheckBISAppTests(unittest.TestCase):
    def setUp(self):
        self.config_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_directory.cleanup)
        config_path = os.path.join(
            self.config_directory.name,
            "freezercheckbis.config.json",
        )
        with open(config_path, "w", encoding="utf-8") as config_file:
            json.dump(TEST_CONFIG, config_file)
        environment = patch.dict(
            os.environ,
            {"FREEZERCHECKBIS_CONFIG": config_path},
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.app = create_app("x" * 32)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def csrf_token(self, response):
        match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def form_token(self, response, name):
        match = re.search(
            rf'name="{re.escape(name)}" value="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def make_session(self, *, instance_name="playground"):
        person_id = "/SPACE/PROJECT/PEOPLE/TEST_USER"
        startup = OpenBISStartup(
            user_id="testuser",
            person_identifier=person_id,
            people=(
                PersonOption(person_id, "Test User"),
                PersonOption("/SPACE/PROJECT/PEOPLE/OTHER_USER", "Alex Sample"),
            ),
            freezers=(
                Freezer(
                    "freezer-perm-id",
                    "/SPACE/PROJECT/FREEZER_COLLECTION/ONE",
                    "Test freezer",
                    "AH_8_15_2_329B",
                    "-80.0",
                    "-60.0",
                ),
            ),
        )
        return OpenBISSession(
            client=Mock(),
            startup=startup,
            instance_name=instance_name,
        )

    def login(self, openbis_session, *, instance_name="playground"):
        login_page = self.client.get("/login")
        with patch(
            "freezer_check_bis_app.authenticate_openbis",
            return_value=openbis_session,
        ) as authenticate:
            response = self.client.post(
                "/login",
                data={
                    "csrf_token": self.csrf_token(login_page),
                    "instance_name": instance_name,
                    "login_name": "test-user@example.invalid",
                    "password": "dummy-test-password",
                },
            )
        authenticate.assert_called_once_with(
            "test-user@example.invalid",
            "dummy-test-password",
            instance_name=instance_name,
        )
        return response

    def test_login_stores_session_server_side_and_defaults_to_playground(self):
        login_page = self.client.get("/login")
        self.assertIn(b'value="playground" checked', login_page.data)
        self.assertIn(b'value="main"', login_page.data)
        openbis_session = self.make_session()

        with patch(
            "freezer_check_bis_app.authenticate_openbis",
            return_value=openbis_session,
        ) as authenticate:
            response = self.client.post(
                "/login",
                data={
                    "csrf_token": self.csrf_token(login_page),
                    "login_name": "test-user@example.invalid",
                    "password": "dummy-test-password",
                },
            )

        authenticate.assert_called_once_with(
            "test-user@example.invalid",
            "dummy-test-password",
            instance_name="playground",
        )
        self.assertEqual(response.status_code, 302)
        cookie = self.client.get_cookie("session").value
        self.assertNotIn("dummy-test-password", cookie)
        self.assertNotIn("freezer-perm-id", cookie)
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"Signed in to playground as testuser", home.data)
        self.assertIn(b"freezer-perm-id", home.data)
        self.assertIn(b'"name": "Test freezer"', home.data)
        self.assertIn(b'"bam_location_complete": "AH_8_15_2_329B"', home.data)
        self.assertIn(b'"temp_min_celsius": "-80.0"', home.data)
        self.assertIn(b'"temp_max_celsius": "-60.0"', home.data)
        self.assertIn(b"Complete BAM Location", home.data)
        self.assertIn(b"Temperature Min", home.data)
        self.assertIn(b"Temperature Max", home.data)

    def test_freezer_limits_are_sent_to_the_browser_for_live_range_warnings(self):
        self.login(self.make_session())
        home = self.client.get("/")

        self.assertIn(b'"temp_min_celsius": "-80.0"', home.data)
        self.assertIn(b'"temp_max_celsius": "-60.0"', home.data)
        self.assertIn(b".temperature-out-of-range { background-color: #f4cccc; }", home.data)
        self.assertIn(b"minimum < allowedMinimum", home.data)
        self.assertIn(b"maximum > allowedMaximum", home.data)
        self.assertIn(
            b'tempMin.addEventListener("input", updateTemperatureWarnings)',
            home.data,
        )
        self.assertIn(
            b'tempMax.addEventListener("input", updateTemperatureWarnings)',
            home.data,
        )

    def test_login_rejects_missing_csrf_token(self):
        response = self.client.post(
            "/login",
            data={
                "login_name": "test-user@example.invalid",
                "password": "dummy-test-password",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_instance_is_rejected(self):
        login_page = self.client.get("/login")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(login_page),
                "instance_name": "production",
                "login_name": "test-user@example.invalid",
                "password": "dummy-test-password",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_authentication_failure_returns_login_error(self):
        login_page = self.client.get("/login")
        with patch(
            "freezer_check_bis_app.authenticate_openbis",
            side_effect=RuntimeError("Could not sign in."),
        ):
            response = self.client.post(
                "/login",
                data={
                    "csrf_token": self.csrf_token(login_page),
                    "instance_name": "main",
                    "login_name": "test-user@example.invalid",
                    "password": "dummy-test-password",
                },
            )

        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Could not sign in.", response.data)
        self.assertIn(b'value="main" checked', response.data)

    def test_main_session_can_save_temperature_checks(self):
        self.login(self.make_session(instance_name="main"), instance_name="main")
        home = self.client.get("/")
        self.assertIn(b"Signed in to main as testuser", home.data)
        self.assertNotIn(b"read-only", home.data)
        self.assertNotIn(b"test record", home.data)
        record = Mock(identifier="/SPACE/PROJECT/CONTROL_COLLECTION/RECORD1")
        with patch(
            "freezer_check_bis_app.create_temperature_check",
            return_value=record,
        ) as create_check:
            for _ in range(2):
                home = self.client.get("/")
                response = self.client.post(
                    "/checks",
                    data={
                        "csrf_token": self.form_token(home, "csrf_token"),
                        "submission_token": self.form_token(
                            home, "submission_token"
                        ),
                        "freezer_perm_id": "freezer-perm-id",
                        "monitoring_date": "2026-11-13",
                        "temp_min_celsius": "-20",
                        "temp_max_celsius": "-18",
                        "person_identifier": "/SPACE/PROJECT/PEOPLE/TEST_USER",
                    },
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], "/")

        self.assertEqual(create_check.call_count, 2)

    def test_latest_check_endpoint_returns_json_for_authenticated_session(self):
        self.login(self.make_session())
        with patch(
            "freezer_check_bis_app.get_latest_temperature_check",
            return_value=None,
        ) as latest_check:
            response = self.client.get("/freezers/freezer-perm-id/latest-check")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"latest_check": None})
        latest_check.assert_called_once()

    def test_latest_check_endpoint_requires_authentication(self):
        response = self.client.get("/freezers/freezer-perm-id/latest-check")
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())

    def test_latest_check_endpoint_returns_safe_error_when_lookup_fails(self):
        self.login(self.make_session())
        with patch(
            "freezer_check_bis_app.get_latest_temperature_check",
            side_effect=RuntimeError("internal detail"),
        ):
            response = self.client.get("/freezers/freezer-perm-id/latest-check")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json(),
            {"error": "Could not load previous temperature readings."},
        )

    def test_logout_clears_the_openbis_session(self):
        self.login(self.make_session())
        home = self.client.get("/")
        response = self.client.post(
            "/logout",
            data={"csrf_token": self.form_token(home, "csrf_token")},
        )

        self.assertEqual(response.status_code, 302)
        redirected = self.client.get("/")
        self.assertEqual(redirected.status_code, 302)
        self.assertEqual(redirected.headers["Location"], "/login")

    def test_home_redirects_when_server_session_is_missing(self):
        self.login(self.make_session())
        with self.client.session_transaction() as browser_session:
            browser_session["openbis_session_id"] = "missing-session"

        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")

    def test_save_rejects_missing_csrf_token(self):
        self.login(self.make_session())
        response = self.client.post(
            "/checks",
            data={"freezer_perm_id": "freezer-perm-id"},
        )
        self.assertEqual(response.status_code, 400)

    def test_save_rejects_non_numeric_temperature(self):
        self.login(self.make_session())
        home = self.client.get("/")
        response = self.client.post(
            "/checks",
            data={
                "csrf_token": self.form_token(home, "csrf_token"),
                "submission_token": self.form_token(home, "submission_token"),
                "freezer_perm_id": "freezer-perm-id",
                "monitoring_date": "2026-11-13",
                "temp_min_celsius": "cold",
                "temp_max_celsius": "-18",
                "person_identifier": "/SPACE/PROJECT/PEOPLE/TEST_USER",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Enter valid numeric values", response.data)

    def test_save_failure_is_reported_without_success_redirect(self):
        self.login(self.make_session())
        home = self.client.get("/")
        with patch(
            "freezer_check_bis_app.create_temperature_check",
            side_effect=RuntimeError("write rejected"),
        ) as create_check:
            response = self.client.post(
                "/checks",
                data={
                    "csrf_token": self.form_token(home, "csrf_token"),
                    "submission_token": self.form_token(home, "submission_token"),
                    "freezer_perm_id": "freezer-perm-id",
                    "monitoring_date": "2026-11-13",
                    "temp_min_celsius": "-20",
                    "temp_max_celsius": "-18",
                    "person_identifier": "/SPACE/PROJECT/PEOPLE/TEST_USER",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Temperature check was not saved: write rejected", response.data)
        create_check.assert_called_once()


if __name__ == "__main__":
    unittest.main()
