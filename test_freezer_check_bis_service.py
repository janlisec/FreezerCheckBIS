import json
import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

import pandas as pd

import freezer_check_bis_service as service

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


class FreezerCheckBISServiceTests(unittest.TestCase):
    def setUp(self):
        self.config_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_directory.cleanup)
        self.config_path = os.path.join(
            self.config_directory.name,
            "freezercheckbis.config.json",
        )
        self.config = deepcopy(TEST_CONFIG)
        self._write_config()
        self.environment = patch.dict(
            os.environ,
            {
                "FREEZERCHECKBIS_CONFIG": self.config_path,
                "OPENBIS_ALLOW_MAIN_WRITES": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _write_config(self):
        with open(self.config_path, "w", encoding="utf-8") as config_file:
            json.dump(self.config, config_file)

    def test_authenticates_and_preloads_account_person_and_freezers(self):
        client = Mock()
        client.get_persons.return_value = Mock(
            df=pd.DataFrame([{"userId": "testuser", "active": True}])
        )
        client.get_samples.side_effect = [
            Mock(
                df=pd.DataFrame(
                    [{"identifier": "/SPACE/PROJECT/PEOPLE/TEST_USER"}]
                )
            ),
            Mock(
                totalCount=3,
                df=pd.DataFrame(
                    [
                        {
                            "identifier": "/SPACE/PROJECT/PEOPLE/TEST_USER",
                            "$NAME": "Test User",
                            "FAMILY_NAME": "User",
                            "ORGANIZATION_PROPERTY": "ORG_ALPHA",
                        },
                        {
                            "identifier": "/SPACE/PROJECT/PEOPLE/OTHER_USER",
                            "$NAME": "Alex",
                            "FAMILY_NAME": "Sample",
                            "ORGANIZATION_PROPERTY": "ORG_ALPHA",
                        },
                        {
                            "identifier": "/SPACE/PROJECT/PEOPLE/OTHER",
                            "$NAME": "Other Person",
                            "FAMILY_NAME": "Person",
                            "ORGANIZATION_PROPERTY": "OTHER_ORG",
                        },
                    ]
                ),
            ),
        ]
        client.get_objects.return_value = Mock(
            totalCount=2,
            df=pd.DataFrame(
                [
                    {
                        "permId": "freezer-1",
                        "identifier": "/SPACE/PROJECT/FREEZER_COLLECTION/ONE",
                    },
                    {
                        "permId": "freezer-2",
                        "identifier": "/SPACE/PROJECT/FREEZER_COLLECTION/TWO",
                    },
                ]
            ),
        )

        with patch.object(service, "Openbis", return_value=client) as factory:
            session = service.authenticate_openbis("test-user@example.invalid", "dummy-test-password")

        factory.assert_called_once_with("https://playground.example.invalid/openbis")
        self.assertEqual(session.instance_name, "playground")
        client.login.assert_called_once_with(
            "test-user@example.invalid", "dummy-test-password", save_token=False
        )
        client.get_persons.assert_called_once_with(
            email="test-user@example.invalid", count=10
        )
        self.assertEqual(
            client.get_samples.call_args_list[0].kwargs,
            {"type": "PERSON_TYPE", "username_property": "testuser", "count": 10},
        )
        self.assertEqual(
            client.get_samples.call_args_list[1].kwargs,
            {
                "type": "PERSON_TYPE",
                "count": service.PERSON_QUERY_LIMIT,
                "props": [
                    "$name",
                    "given_name",
                    "family_name",
                    "organization_property",
                ],
            },
        )
        client.get_objects.assert_called_once_with(
            collection="/SPACE/PROJECT/FREEZER_COLLECTION",
            type="FREEZER_TYPE",
            count=service.FREEZER_QUERY_LIMIT,
        )
        self.assertEqual(session.startup.user_id, "testuser")
        self.assertEqual(
            session.startup.person_identifier,
            "/SPACE/PROJECT/PEOPLE/TEST_USER",
        )
        self.assertEqual(
            [freezer.perm_id for freezer in session.startup.freezers],
            ["freezer-1", "freezer-2"],
        )
        self.assertEqual(
            [person.identifier for person in session.startup.people],
            [
                "/SPACE/PROJECT/PEOPLE/OTHER_USER",
                "/SPACE/PROJECT/PEOPLE/TEST_USER",
            ],
        )
        self.assertEqual(
            [person.label for person in session.startup.people],
            ["Alex Sample", "Test User"],
        )

    def test_rejects_unsupported_host_before_connecting(self):
        self.config["instances"]["playground"]["url"] = (
            "http://other.example.invalid/openbis"
        )
        self._write_config()
        with patch.object(service, "Openbis") as factory:
            with self.assertRaisesRegex(RuntimeError, "must be HTTPS"):
                service.authenticate_openbis("test-user@example.invalid", "dummy-test-password")
        factory.assert_not_called()

    def test_playground_is_the_default_instance(self):
        self.assertEqual(
            service._configured_openbis_target(),
            (
                "https://playground.example.invalid/openbis",
                "playground",
                service._load_config(),
            ),
        )

    def test_selected_instance_uses_its_configured_url(self):
        self.assertEqual(
            service._configured_openbis_target("main"),
            (
                "https://main.example.invalid/openbis",
                "main",
                service._load_config(),
            ),
        )

    def test_rejects_invalid_selected_instance(self):
        with self.assertRaisesRegex(ValueError, "either playground or main"):
            service._configured_openbis_target("production")

    def test_main_writes_require_explicit_environment_flag(self):
        os.environ.pop("OPENBIS_ALLOW_MAIN_WRITES", None)
        self.assertFalse(service._main_writes_enabled("main"))
        os.environ["OPENBIS_ALLOW_MAIN_WRITES"] = "1"
        self.assertTrue(service._main_writes_enabled("main"))
        self.assertFalse(service._main_writes_enabled("playground"))

    def test_authentication_marks_main_session_read_only(self):
        client = Mock()
        client.get_persons.return_value = Mock(
            df=pd.DataFrame([{"userId": "testuser", "active": True}])
        )
        client.get_samples.side_effect = [
            Mock(
                df=pd.DataFrame(
                    [{"identifier": "/SPACE/PROJECT/PEOPLE/TEST_USER"}]
                )
            ),
            Mock(
                totalCount=1,
                df=pd.DataFrame(
                    [
                        {
                            "identifier": "/SPACE/PROJECT/PEOPLE/TEST_USER",
                            "$NAME": "Test User",
                            "FAMILY_NAME": "User",
                            "ORGANIZATION_PROPERTY": "ORG_ALPHA",
                        }
                    ]
                ),
            ),
        ]
        client.get_objects.return_value = Mock(
            totalCount=1,
            df=pd.DataFrame(
                [
                    {
                        "permId": "main-freezer-id",
                        "identifier": "/SPACE/PROJECT/MAIN_FREEZERS/ONE",
                    }
                ]
            ),
        )

        with patch.object(service, "Openbis", return_value=client) as factory:
            session = service.authenticate_openbis(
                "test-user@example.invalid",
                "dummy-test-password",
                instance_name="main",
            )

        factory.assert_called_once_with("https://main.example.invalid/openbis")
        self.assertEqual(session.instance_name, "main")
        self.assertTrue(session.read_only)
        client.get_objects.assert_called_once_with(
            collection="/SPACE/PROJECT/MAIN_FREEZERS",
            type="FREEZER_TYPE",
            count=service.FREEZER_QUERY_LIMIT,
        )

    def test_rejects_partial_freezer_inventory(self):
        client = Mock()
        client.get_persons.return_value = Mock(
            df=pd.DataFrame([{"userId": "testuser", "active": True}])
        )
        client.get_samples.side_effect = [
            Mock(df=pd.DataFrame([{"identifier": "/SPACE/PROJECT/PEOPLE/TEST_USER"}])),
            Mock(
                totalCount=1,
                df=pd.DataFrame(
                    [{"identifier": "/SPACE/PROJECT/PEOPLE/TEST_USER"}]
                ),
            ),
        ]
        client.get_objects.return_value = Mock(totalCount=2, df=pd.DataFrame())

        with patch.object(service, "Openbis", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                service.authenticate_openbis("test-user@example.invalid", "dummy-test-password")

    def test_empty_configured_collection_reports_other_freezer_collections(self):
        client = Mock()
        client.get_objects.side_effect = [
            Mock(totalCount=0, df=pd.DataFrame()),
            Mock(
                totalCount=2,
                df=pd.DataFrame(
                    [
                        {"identifier": "/SPACE/PROJECT/OTHER_COLLECTION/ONE"},
                        {"identifier": "/SPACE/PROJECT/ANOTHER_COLLECTION/TWO"},
                    ]
                ),
            ),
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            r"2 visible freezer objects in other collections: "
            r"/SPACE/PROJECT/ANOTHER_COLLECTION, "
            r"/SPACE/PROJECT/OTHER_COLLECTION",
        ):
            service._freezer_inventory(
                client,
                "/SPACE/PROJECT/FREEZER_COLLECTION",
                "FREEZER_TYPE",
            )

        self.assertEqual(
            client.get_objects.call_args_list[1].kwargs,
            {"type": "FREEZER_TYPE", "count": service.FREEZER_QUERY_LIMIT},
        )

    def test_empty_configured_collection_reports_no_visible_type_matches(self):
        client = Mock()
        client.get_objects.side_effect = [
            Mock(totalCount=0, df=pd.DataFrame()),
            Mock(totalCount=0, df=pd.DataFrame()),
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "No visible FREEZER_TYPE objects",
        ):
            service._freezer_inventory(
                client,
                "/SPACE/PROJECT/FREEZER_COLLECTION",
                "FREEZER_TYPE",
            )

    def test_fails_when_person_or_account_is_ambiguous(self):
        client = Mock()
        client.get_persons.return_value = Mock(
            df=pd.DataFrame(
                [
                    {"userId": "testuser", "active": True},
                    {"userId": "other", "active": True},
                ]
            )
        )

        with patch.object(service, "Openbis", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                service.authenticate_openbis("test-user@example.invalid", "dummy-test-password")
        client.get_samples.assert_not_called()

    def test_create_temperature_check_sets_properties_and_parent(self):
        freezer = service.Freezer(
            "freezer-1", "/SPACE/PROJECT/FREEZER_COLLECTION/ONE"
        )
        people = (
            service.PersonOption("/SPACE/PROJECT/PEOPLE/TEST_USER", "Test User"),
            service.PersonOption("/SPACE/PROJECT/PEOPLE/OTHER_USER", "Alex Sample"),
        )
        openbis = Mock()
        openbis.get_collections.return_value = Mock(
            df=pd.DataFrame(
                [{"identifier": "/SPACE/PROJECT/CONTROL_COLLECTION"}]
            )
        )
        parent = Mock(identifier=freezer.identifier, type="FREEZER_TYPE")
        openbis.get_object.return_value = parent
        record = Mock(identifier="/SPACE/PROJECT/CONTROL_COLLECTION/RECORD1")
        openbis.new_object.return_value = record
        session = service.OpenBISSession(
            client=openbis,
            startup=service.OpenBISStartup(
                user_id="testuser",
                person_identifier=people[0].identifier,
                freezers=(freezer,),
                people=people,
            ),
        )

        result = service.create_temperature_check(
            session,
            freezer_perm_id="freezer-1",
            monitoring_date="2026-11-13",
            temp_min_celsius=-20.0,
            temp_max_celsius=-18.0,
            person_identifier=people[1].identifier,
        )

        self.assertIs(result, record)
        openbis.new_object.assert_called_once_with(
            type="CONTROL_TYPE",
            collection="/SPACE/PROJECT/CONTROL_COLLECTION",
            props={
                "$name": "Freezer check ONE 2026-11-13",
                "date_property": "2026-11-13",
                "minimum_property": -20.0,
                "maximum_property": -18.0,
                "person_property": people[1].identifier,
            },
        )
        record.set_parents.assert_called_once_with([parent])
        record.save.assert_called_once_with()

    def test_create_temperature_check_rejects_bad_readings_without_writing(self):
        openbis = Mock()
        person_id = "/SPACE/PROJECT/PEOPLE/TEST_USER"
        session = service.OpenBISSession(
            client=openbis,
            startup=service.OpenBISStartup(
                user_id="testuser",
                person_identifier=person_id,
                freezers=(service.Freezer("freezer-1", "/FREEZER/ONE"),),
                people=(service.PersonOption(person_id, "Test User"),),
            ),
        )

        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            service.create_temperature_check(
                session,
                freezer_perm_id="freezer-1",
                monitoring_date="2026-11-13",
                temp_min_celsius=6.0,
                temp_max_celsius=4.0,
                person_identifier=person_id,
            )
        openbis.get_collections.assert_not_called()
        openbis.new_object.assert_not_called()

    def test_main_session_cannot_create_temperature_check(self):
        client = Mock()
        session = service.OpenBISSession(
            client=client,
            startup=service.OpenBISStartup(
                user_id="testuser",
                person_identifier="/SPACE/PROJECT/PEOPLE/TEST_USER",
                freezers=(),
                people=(),
            ),
            instance_name="main",
        )
        with self.assertRaisesRegex(RuntimeError, "disabled for the main"):
            service.create_temperature_check(
                session,
                freezer_perm_id="main-freezer-id",
                monitoring_date="2026-11-13",
                temp_min_celsius=-20.0,
                temp_max_celsius=-18.0,
                person_identifier="/SPACE/PROJECT/PEOPLE/TEST_USER",
            )
        client.new_object.assert_not_called()

    def test_latest_temperature_check_returns_most_recent_parented_record(self):
        freezer = service.Freezer("freezer-1", "/FREEZER/ONE")
        openbis = Mock()
        openbis.get_objects.return_value = Mock(
            totalCount=2,
            df=pd.DataFrame(
                [
                    {
                        "DATE_PROPERTY": "2026-10-01",
                        "MINIMUM_PROPERTY": 4.0,
                        "MAXIMUM_PROPERTY": 6.0,
                    },
                    {
                        "DATE_PROPERTY": "2026-10-15",
                        "MINIMUM_PROPERTY": 3.5,
                        "MAXIMUM_PROPERTY": 5.5,
                    },
                ]
            ),
        )
        session = service.OpenBISSession(
            client=openbis,
            startup=service.OpenBISStartup(
                user_id="testuser",
                person_identifier="/SPACE/PROJECT/PEOPLE/TEST_USER",
                freezers=(freezer,),
                people=(),
            ),
        )

        latest = service.get_latest_temperature_check(session, "freezer-1")

        self.assertEqual(
            latest,
            service.TemperatureReading("2026-10-15", 3.5, 5.5),
        )
        openbis.get_objects.assert_called_once_with(
            collection="/SPACE/PROJECT/CONTROL_COLLECTION",
            type="CONTROL_TYPE",
            withParents=freezer.identifier,
            count=service.PERSON_QUERY_LIMIT,
            props=["date_property", "minimum_property", "maximum_property"],
        )

    def test_latest_temperature_check_returns_none_when_no_records_exist(self):
        openbis = Mock()
        openbis.get_objects.return_value = Mock(
            totalCount=0,
            df=pd.DataFrame(
                columns=[
                    "DATE_PROPERTY",
                    "MINIMUM_PROPERTY",
                    "MAXIMUM_PROPERTY",
                ]
            ),
        )
        session = service.OpenBISSession(
            client=openbis,
            startup=service.OpenBISStartup(
                user_id="testuser",
                person_identifier="/SPACE/PROJECT/PEOPLE/TEST_USER",
                freezers=(service.Freezer("freezer-1", "/FREEZER/ONE"),),
                people=(),
            ),
        )

        self.assertIsNone(
            service.get_latest_temperature_check(session, "freezer-1")
        )

    def test_latest_temperature_check_rejects_incomplete_results(self):
        openbis = Mock()
        openbis.get_objects.return_value = Mock(
            totalCount=2,
            df=pd.DataFrame(
                [
                    {
                        "MONITORING_DATE": "2026-10-15",
                        "TEMP_MIN_CELSIUS": 3.5,
                        "TEMP_MAX_CELSIUS": 5.5,
                    }
                ]
            ),
        )
        session = service.OpenBISSession(
            client=openbis,
            startup=service.OpenBISStartup(
                user_id="testuser",
                person_identifier="/SPACE/PROJECT/PEOPLE/TEST_USER",
                freezers=(service.Freezer("freezer-1", "/FREEZER/ONE"),),
                people=(),
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            service.get_latest_temperature_check(session, "freezer-1")


if __name__ == "__main__":
    unittest.main()
