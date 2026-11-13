"""OpenBIS authentication and freezer preload for the temperature app."""

import os
import json
from dataclasses import dataclass, field
from datetime import date
from math import isfinite
from pathlib import Path
from urllib.parse import urlparse

from pybis import Openbis

FREEZER_QUERY_LIMIT = 500
PERSON_QUERY_LIMIT = 5000


@dataclass(frozen=True)
class InstanceConfig:
    url: str
    freezer_collection: str


@dataclass(frozen=True)
class OpenBISConfig:
    instances: dict[str, InstanceConfig]
    freezer_type: str
    person_type: str
    person_username_property: str
    person_name_property: str
    person_given_name_property: str
    person_family_name_property: str
    person_organizational_entity_property: str
    person_organizational_entity_prefix: str
    control_collection: str
    control_type: str
    object_name_property: str
    monitoring_date_property: str
    temp_min_property: str
    temp_max_property: str
    acting_person_property: str


def _required_string(mapping: dict, key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Configuration value '{key}' must be a non-empty string.")
    return value.strip()


def _load_config() -> OpenBISConfig:
    config_path = Path(
        os.environ.get(
            "FREEZERCHECKBIS_CONFIG",
            Path(__file__).with_name("freezercheckbis.config.json"),
        )
    )
    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Configuration file not found: {config_path}. "
            "Copy freezercheckbis.config.example.json and fill in your instance "
            "URLs, collections, and schema identifiers."
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Configuration file is not valid JSON: {error}") from error

    if not isinstance(raw_config, dict):
        raise RuntimeError("Configuration root must be a JSON object.")
    raw_instances = raw_config.get("instances")
    if not isinstance(raw_instances, dict):
        raise RuntimeError("Configuration 'instances' must be a JSON object.")

    instances: dict[str, InstanceConfig] = {}
    for instance_name in ("playground", "main"):
        raw_instance = raw_instances.get(instance_name)
        if not isinstance(raw_instance, dict):
            raise RuntimeError(f"Configuration for '{instance_name}' is required.")
        url = _required_string(raw_instance, "url")
        parsed_url = urlparse(url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise RuntimeError(
                f"Configured URL for '{instance_name}' must be HTTPS, without "
                "embedded credentials, query, or fragment."
            )
        instances[instance_name] = InstanceConfig(
            url=url,
            freezer_collection=_required_string(raw_instance, "freezer_collection"),
        )

    schema = raw_config.get("schema")
    if not isinstance(schema, dict):
        raise RuntimeError("Configuration 'schema' must be a JSON object.")
    schema_fields = (
        "freezer_type",
        "person_type",
        "person_username_property",
        "person_name_property",
        "person_given_name_property",
        "person_family_name_property",
        "person_organizational_entity_property",
        "person_organizational_entity_prefix",
        "control_collection",
        "control_type",
        "object_name_property",
        "monitoring_date_property",
        "temp_min_property",
        "temp_max_property",
        "acting_person_property",
    )
    schema_values = {
        key: _required_string(schema, key) for key in schema_fields
    }
    return OpenBISConfig(instances=instances, **schema_values)


@dataclass(frozen=True)
class Freezer:
    perm_id: str
    identifier: str


@dataclass(frozen=True)
class OpenBISStartup:
    user_id: str
    person_identifier: str
    freezers: tuple[Freezer, ...]
    people: tuple["PersonOption", ...]


@dataclass(frozen=True)
class PersonOption:
    identifier: str
    label: str


@dataclass(frozen=True)
class TemperatureReading:
    monitoring_date: str
    temp_min_celsius: float
    temp_max_celsius: float


@dataclass
class OpenBISSession:
    """Authenticated server-side session; never serialize the client to a browser."""

    client: Openbis = field(repr=False)
    startup: OpenBISStartup
    config: OpenBISConfig = field(default_factory=lambda: _load_config(), repr=False)
    instance_name: str = "playground"
    main_writes_enabled: bool = False

    @property
    def read_only(self) -> bool:
        return self.instance_name == "main" and not self.main_writes_enabled


def _configured_openbis_target(
    selected_instance: str | None = None,
) -> tuple[str, str, OpenBISConfig]:
    config = _load_config()
    if selected_instance is None:
        instance_name = "playground"
    else:
        instance_name = selected_instance
        if selected_instance not in config.instances:
            raise ValueError("Select either playground or main.")
    return config.instances[instance_name].url, instance_name, config


def _main_writes_enabled(instance_name: str) -> bool:
    return (
        instance_name == "main"
        and os.environ.get("OPENBIS_ALLOW_MAIN_WRITES", "").strip() == "1"
    )


def _account_rows(client: Openbis, login_name: str):
    """Resolve the login email to its unique active openBIS user account."""
    accounts = client.get_persons(email=login_name, count=10)
    if len(accounts.df) != 1:
        raise RuntimeError(
            "The login email did not resolve to exactly one openBIS account."
        )

    row = accounts.df.iloc[0]
    user_id = row.get("userId")
    if not isinstance(user_id, str) or not user_id.strip():
        raise RuntimeError("The matched openBIS account has no user ID.")
    if not bool(row.get("active")):
        raise RuntimeError("The matched openBIS account is inactive.")
    return user_id.strip()


def _person_identifier(
    client: Openbis,
    user_id: str,
    config: OpenBISConfig,
) -> str:
    matches = client.get_samples(
        type=config.person_type,
        count=10,
        **{config.person_username_property: user_id},
    )
    if len(matches.df) != 1:
        raise RuntimeError(
            "The openBIS user did not resolve to exactly one person record."
        )

    identifier = matches.df.iloc[0].get("identifier")
    if not isinstance(identifier, str) or not identifier.strip():
        raise RuntimeError("The matched person record has no identifier.")
    return identifier.strip()


def _freezer_inventory(
    client: Openbis,
    collection: str,
    freezer_type: str,
) -> tuple[Freezer, ...]:
    result = client.get_objects(
        collection=collection,
        type=freezer_type,
        count=FREEZER_QUERY_LIMIT,
    )
    if result.totalCount != len(result.df):
        raise RuntimeError(
            "The freezer inventory query was incomplete; refusing to start "
            "with a partial scan-validation list."
        )

    freezers: list[Freezer] = []
    seen_perm_ids: set[str] = set()
    for _, row in result.df.iterrows():
        perm_id = row.get("permId")
        identifier = row.get("identifier")
        if not isinstance(perm_id, str) or not perm_id.strip():
            raise RuntimeError("A freezer record has no permanent ID.")
        if not isinstance(identifier, str) or not identifier.strip():
            raise RuntimeError("A freezer record has no identifier.")
        if perm_id in seen_perm_ids:
            raise RuntimeError("The freezer query returned duplicate permanent IDs.")
        seen_perm_ids.add(perm_id)
        freezers.append(Freezer(perm_id=perm_id, identifier=identifier))

    if not freezers:
        all_freezers = client.get_objects(
            type=freezer_type,
            count=FREEZER_QUERY_LIMIT,
        )
        if all_freezers.totalCount != len(all_freezers.df):
            raise RuntimeError(
                f"No {freezer_type} objects were found in {collection}; "
                "the instance-wide freezer query was incomplete, so collection "
                "locations could not be diagnosed."
            )
        other_collections = sorted(
            {
                identifier.rsplit("/", 1)[0]
                for identifier in all_freezers.df.get("identifier", [])
                if isinstance(identifier, str) and "/" in identifier
            }
        )
        if other_collections:
            collection_list = ", ".join(other_collections[:10])
            if len(other_collections) > 10:
                collection_list += f", and {len(other_collections) - 10} more"
            raise RuntimeError(
                f"No {freezer_type} objects were found in {collection}. "
                f"The instance has {all_freezers.totalCount} visible freezer "
                f"objects in other collections: {collection_list}. "
                "Check which collection is the intended freezer inventory."
            )
        raise RuntimeError(
            f"No visible {freezer_type} objects were found in "
            f"{collection} or elsewhere in this instance. Verify the "
            "object type, collection path, and account permissions."
        )
    return tuple(freezers)


def _person_inventory(
    client: Openbis,
    default_person_identifier: str,
    config: OpenBISConfig,
) -> tuple[PersonOption, ...]:
    result = client.get_samples(
        type=config.person_type,
        count=PERSON_QUERY_LIMIT,
        props=[
            config.person_name_property,
            config.person_given_name_property,
            config.person_family_name_property,
            config.person_organizational_entity_property,
        ],
    )
    if result.totalCount != len(result.df):
        raise RuntimeError(
            "The person inventory query was incomplete; refusing to offer "
            "a partial person-selection list."
        )

    people: list[PersonOption] = []
    seen_identifiers: set[str] = set()
    for _, row in result.df.iterrows():
        identifier = row.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            raise RuntimeError("A person record has no identifier.")
        if identifier in seen_identifiers:
            raise RuntimeError("The person query returned duplicate identifiers.")
        organizational_entity = row.get(
            config.person_organizational_entity_property.upper()
        )
        if (
            not isinstance(organizational_entity, str)
            or not organizational_entity.startswith(
                config.person_organizational_entity_prefix
            )
        ):
            continue

        display_name = row.get(config.person_name_property.upper())
        if isinstance(display_name, str) and display_name.strip():
            name = display_name.strip()
        else:
            given_name = row.get(config.person_given_name_property.upper())
            name = given_name.strip() if isinstance(given_name, str) else ""
        family_name = row.get(config.person_family_name_property.upper())
        if isinstance(family_name, str) and family_name.strip():
            surname = family_name.strip()
            if not name.casefold().endswith(f" {surname}".casefold()):
                name = f"{name} {surname}".strip()
        label = name
        people.append(
            PersonOption(
                identifier=identifier,
                label=label or identifier.rsplit("/", 1)[-1],
            )
        )
        seen_identifiers.add(identifier)

    if default_person_identifier not in seen_identifiers:
        raise RuntimeError(
            "The default person record is missing from the selectable people."
        )
    return tuple(sorted(people, key=lambda person: person.label.casefold()))


def create_temperature_check(
    session: OpenBISSession,
    *,
    freezer_perm_id: str,
    monitoring_date: str,
    temp_min_celsius: float,
    temp_max_celsius: float,
    person_identifier: str,
):
    """Create one new control record, linked to a loaded freezer."""
    if session.read_only:
        raise RuntimeError("Record creation is disabled for the main instance.")

    freezer_by_id = {freezer.perm_id: freezer for freezer in session.startup.freezers}
    freezer = freezer_by_id.get(freezer_perm_id)
    if freezer is None:
        raise ValueError("The selected freezer is not in the loaded inventory.")

    try:
        date.fromisoformat(monitoring_date)
    except (TypeError, ValueError) as error:
        raise ValueError("Monitoring date must be a valid ISO date.") from error

    if not isfinite(temp_min_celsius) or not isfinite(temp_max_celsius):
        raise ValueError("Temperatures must be finite numbers.")
    if temp_min_celsius > temp_max_celsius:
        raise ValueError("Minimum temperature cannot exceed maximum temperature.")

    people = {person.identifier for person in session.startup.people}
    if person_identifier not in people:
        raise ValueError("The selected person is not in the loaded people list.")

    path_parts = session.config.control_collection.strip("/").split("/")
    if len(path_parts) != 3 or not all(path_parts):
        raise RuntimeError(
            "The configured control collection must use the "
            "/SPACE/PROJECT/COLLECTION format."
        )
    space_code, project_code, collection_code = path_parts
    collections = session.client.get_collections(
        space=space_code,
        project=project_code,
        code=collection_code,
        count=2,
    )
    if (
        len(collections.df) != 1
        or collections.df.iloc[0].get("identifier")
        != session.config.control_collection
    ):
        raise RuntimeError("The configured control collection was not uniquely found.")

    parent = session.client.get_object(freezer.perm_id)
    if (
        parent.identifier != freezer.identifier
        or parent.type != session.config.freezer_type
    ):
        raise RuntimeError("The selected freezer no longer matches the loaded inventory.")

    properties = {
        session.config.object_name_property: (
            f"Freezer check {freezer.identifier.rsplit('/', 1)[-1]} "
            f"{monitoring_date}"
        ),
        session.config.monitoring_date_property: monitoring_date,
        session.config.temp_min_property: temp_min_celsius,
        session.config.temp_max_property: temp_max_celsius,
        session.config.acting_person_property: person_identifier,
    }
    obj = session.client.new_object(
        type=session.config.control_type,
        collection=session.config.control_collection,
        props=properties,
    )
    obj.set_parents([parent])
    obj.save()
    return obj


def get_latest_temperature_check(
    session: OpenBISSession,
    freezer_perm_id: str,
) -> TemperatureReading | None:
    """Return the latest dated check linked to a loaded freezer."""
    freezer_by_id = {freezer.perm_id: freezer for freezer in session.startup.freezers}
    freezer = freezer_by_id.get(freezer_perm_id)
    if freezer is None:
        raise ValueError("The selected freezer is not in the loaded inventory.")

    result = session.client.get_objects(
        collection=session.config.control_collection,
        type=session.config.control_type,
        withParents=freezer.identifier,
        count=PERSON_QUERY_LIMIT,
        props=[
            session.config.monitoring_date_property,
            session.config.temp_min_property,
            session.config.temp_max_property,
        ],
    )
    if result.totalCount != len(result.df):
        raise RuntimeError(
            "The temperature-check query was incomplete; refusing to use "
            "possibly stale default readings."
        )

    latest: TemperatureReading | None = None
    for _, row in result.df.iterrows():
        date_value = row.get(session.config.monitoring_date_property.upper())
        try:
            parsed_date = date.fromisoformat(str(date_value))
            temp_min = float(row.get(session.config.temp_min_property.upper()))
            temp_max = float(row.get(session.config.temp_max_property.upper()))
        except (TypeError, ValueError):
            continue
        if not isfinite(temp_min) or not isfinite(temp_max):
            continue

        if latest is None or parsed_date > date.fromisoformat(latest.monitoring_date):
            latest = TemperatureReading(
                monitoring_date=parsed_date.isoformat(),
                temp_min_celsius=temp_min,
                temp_max_celsius=temp_max,
            )
    return latest


def authenticate_openbis(
    login_name: str,
    password: str,
    instance_name: str | None = None,
) -> OpenBISSession:
    """Authenticate and preload identity/freezer data from a supported instance."""
    if not login_name.strip():
        raise ValueError("Login name must not be empty.")
    if not password:
        raise ValueError("Password must not be empty.")

    url, instance_name, config = _configured_openbis_target(instance_name)
    client = Openbis(url)
    client.login(login_name, password, save_token=False)

    user_id = _account_rows(client, login_name)
    person_identifier = _person_identifier(client, user_id, config)
    freezer_collection = config.instances[instance_name].freezer_collection
    freezers = _freezer_inventory(client, freezer_collection, config.freezer_type)
    people = _person_inventory(client, person_identifier, config)

    return OpenBISSession(
        client=client,
        startup=OpenBISStartup(
            user_id=user_id,
            person_identifier=person_identifier,
            freezers=freezers,
            people=people,
        ),
        config=config,
        instance_name=instance_name,
        main_writes_enabled=_main_writes_enabled(instance_name),
    )
