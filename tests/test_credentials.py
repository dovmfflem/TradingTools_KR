from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.credentials import (
    KEYRING_SERVICE,
    CredentialNotFoundError,
    SPECS,
    load_credentials,
    save_credentials_to_keyring,
)
from src.exchanges.upbit.upbit_rest import UpbitRest


class CredentialsTest(unittest.TestCase):
    def test_load_from_env(self) -> None:
        env = {
            "TRADINGTOOLS_UPBIT_API_KEY": "env-api",
            "TRADINGTOOLS_UPBIT_SECRET_KEY": "env-secret",
        }
        with patch.dict("os.environ", env, clear=True):
            credentials = load_credentials("upbit", source="env")

        self.assertEqual(credentials.api_key, "env-api")
        self.assertEqual(credentials.secret_key, "env-secret")
        self.assertEqual(credentials.source, "env")

    def test_load_from_custom_env_prefix(self) -> None:
        env = {
            "MYAPP_COINONE_ACCESS_TOKEN": "custom-token",
            "MYAPP_COINONE_SECRET_KEY": "custom-secret",
        }
        with patch.dict("os.environ", env, clear=True):
            credentials = load_credentials("coinone", source="env", env_prefix="MYAPP")

        self.assertEqual(credentials.access_token, "custom-token")
        self.assertEqual(credentials.secret_key, "custom-secret")
        self.assertEqual(credentials.source, "env")

    def test_load_from_custom_env_names(self) -> None:
        env = {
            "ANY_PRIMARY": "custom-api",
            "ANY_SECRET": "custom-secret",
        }
        with patch.dict("os.environ", env, clear=True):
            credentials = load_credentials(
                "upbit",
                source="env",
                env_primary="ANY_PRIMARY",
                env_secret="ANY_SECRET",
            )

        self.assertEqual(credentials.api_key, "custom-api")
        self.assertEqual(credentials.secret_key, "custom-secret")

    def test_load_from_info_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "info.yaml"
            path.write_text(
                "\n".join(
                    [
                        'coinone_access_token: "yaml-token"',
                        'coinone_secret_key: "yaml-secret"',
                    ]
                ),
                encoding="utf-8",
            )

            credentials = load_credentials(
                "coinone",
                source="info_yaml",
                file_path=str(path),
            )

        self.assertEqual(credentials.access_token, "yaml-token")
        self.assertEqual(credentials.secret_key, "yaml-secret")
        self.assertEqual(credentials.source, "info_yaml")

    def test_load_from_custom_info_yaml_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "info.yaml"
            path.write_text(
                "\n".join(
                    [
                        'my_api: "yaml-api"',
                        'my_secret: "yaml-secret"',
                    ]
                ),
                encoding="utf-8",
            )

            credentials = load_credentials(
                "bithumb",
                source="info_yaml",
                file_path=str(path),
                yaml_primary="my_api",
                yaml_secret="my_secret",
            )

        self.assertEqual(credentials.api_key, "yaml-api")
        self.assertEqual(credentials.secret_key, "yaml-secret")

    def test_auto_prefers_env_over_info_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "info.yaml"
            path.write_text(
                "\n".join(
                    [
                        'bithumb_api_key: "yaml-api"',
                        'bithumb_secret_key: "yaml-secret"',
                    ]
                ),
                encoding="utf-8",
            )
            env = {
                "TRADINGTOOLS_BITHUMB_API_KEY": "env-api",
                "TRADINGTOOLS_BITHUMB_SECRET_KEY": "env-secret",
            }
            with patch.dict("os.environ", env, clear=True):
                credentials = load_credentials(
                    "bithumb",
                    source="auto",
                    file_path=str(path),
                )

        self.assertEqual(credentials.api_key, "env-api")
        self.assertEqual(credentials.secret_key, "env-secret")
        self.assertEqual(credentials.source, "env")

    def test_load_from_keyring_with_mock_backend(self) -> None:
        store = {
            (KEYRING_SERVICE, "upbit.api_key"): "keyring-api",
            (KEYRING_SERVICE, "upbit.secret_key"): "keyring-secret",
        }
        fake_keyring = types.SimpleNamespace(
            get_password=lambda service, key: store.get((service, key)),
            set_password=lambda service, key, value: store.__setitem__((service, key), value),
            delete_password=lambda service, key: store.pop((service, key)),
        )

        with patch.dict("sys.modules", {"keyring": fake_keyring}):
            credentials = load_credentials("upbit", source="keyring")

        self.assertEqual(credentials.api_key, "keyring-api")
        self.assertEqual(credentials.secret_key, "keyring-secret")
        self.assertEqual(credentials.source, "keyring")

    def test_upbit_pocket_specs_support_five_slots(self) -> None:
        for index in range(1, 6):
            self.assertIn(f"upbit_pocket_{index}", SPECS)
        self.assertEqual(
            SPECS["upbit_pocket_1"].env_primary,
            "TRADINGTOOLS_UPBIT_POCKET_1_API_KEY",
        )
        self.assertEqual(
            SPECS["upbit_pocket_1"].yaml_primary,
            "upbit_pocket_1_api_key",
        )

    def test_binance_specs_include_spot_and_futures(self) -> None:
        self.assertEqual(SPECS["binance"].env_primary, "TRADINGTOOLS_BINANCE_API_KEY")
        self.assertEqual(
            SPECS["binance_futures"].env_primary,
            "TRADINGTOOLS_BINANCE_FUTURES_API_KEY",
        )

    def test_load_binance_futures_from_info_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "info.yaml"
            path.write_text(
                "\n".join(
                    [
                        'binance_futures_api_key: "futures-api"',
                        'binance_futures_secret_key: "futures-secret"',
                    ]
                ),
                encoding="utf-8",
            )

            credentials = load_credentials(
                "binance_futures",
                source="info_yaml",
                file_path=str(path),
            )

        self.assertEqual(credentials.api_key, "futures-api")
        self.assertEqual(credentials.secret_key, "futures-secret")

    def test_load_upbit_pocket_fifth_slot_from_info_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "info.yaml"
            path.write_text(
                "\n".join(
                    [
                        'upbit_pocket_5_api_key: "pocket5-api"',
                        'upbit_pocket_5_secret_key: "pocket5-secret"',
                    ]
                ),
                encoding="utf-8",
            )

            credentials = load_credentials(
                "upbit_pocket_5",
                source="info_yaml",
                file_path=str(path),
            )

        self.assertEqual(credentials.api_key, "pocket5-api")
        self.assertEqual(credentials.secret_key, "pocket5-secret")

    def test_upbit_pocket_keyring_slots_are_distinct(self) -> None:
        store: dict[tuple[str, str], str] = {}
        fake_keyring = types.SimpleNamespace(
            get_password=lambda service, key: store.get((service, key)),
            set_password=lambda service, key, value: store.__setitem__((service, key), value),
            delete_password=lambda service, key: store.pop((service, key)),
        )

        with patch.dict("sys.modules", {"keyring": fake_keyring}):
            save_credentials_to_keyring(
                "upbit_pocket_3",
                primary_key="pocket3-api",
                secret_key="pocket3-secret",
            )
            credentials = load_credentials("upbit_pocket_3", source="keyring")

        self.assertEqual(credentials.api_key, "pocket3-api")
        self.assertEqual(credentials.secret_key, "pocket3-secret")
        self.assertIn((KEYRING_SERVICE, "upbit_pocket_3.api_key"), store)

    def test_upbit_pocket_config_rejects_out_of_range_index(self) -> None:
        with self.assertRaises(ValueError):
            UpbitRest.from_pocket_config(source="env", pocket_index=6)

    def test_save_to_keyring_with_mock_backend(self) -> None:
        store: dict[tuple[str, str], str] = {}
        fake_keyring = types.SimpleNamespace(
            get_password=lambda service, key: store.get((service, key)),
            set_password=lambda service, key, value: store.__setitem__((service, key), value),
            delete_password=lambda service, key: store.pop((service, key)),
        )

        with patch.dict("sys.modules", {"keyring": fake_keyring}):
            save_credentials_to_keyring(
                "coinone",
                primary_key="token",
                secret_key="secret",
            )

        self.assertEqual(store[(KEYRING_SERVICE, "coinone.access_token")], "token")
        self.assertEqual(store[(KEYRING_SERVICE, "coinone.secret_key")], "secret")

    def test_custom_keyring_service_with_mock_backend(self) -> None:
        store: dict[tuple[str, str], str] = {}
        fake_keyring = types.SimpleNamespace(
            get_password=lambda service, key: store.get((service, key)),
            set_password=lambda service, key, value: store.__setitem__((service, key), value),
            delete_password=lambda service, key: store.pop((service, key)),
        )

        with patch.dict("sys.modules", {"keyring": fake_keyring}):
            save_credentials_to_keyring(
                "binance",
                primary_key="binance-api",
                secret_key="binance-secret",
                keyring_service="OtherProject",
            )
            credentials = load_credentials(
                "binance",
                source="keyring",
                keyring_service="OtherProject",
            )

        self.assertEqual(credentials.api_key, "binance-api")
        self.assertEqual(credentials.secret_key, "binance-secret")
        self.assertIn(("OtherProject", "binance.api_key"), store)

    def test_custom_keyring_item_names_with_mock_backend(self) -> None:
        store: dict[tuple[str, str], str] = {}
        fake_keyring = types.SimpleNamespace(
            get_password=lambda service, key: store.get((service, key)),
            set_password=lambda service, key, value: store.__setitem__((service, key), value),
            delete_password=lambda service, key: store.pop((service, key)),
        )

        with patch.dict("sys.modules", {"keyring": fake_keyring}):
            save_credentials_to_keyring(
                "coinone",
                primary_key="custom-token",
                secret_key="custom-secret",
                keyring_service="OtherProject",
                keyring_primary="coinone.token.custom",
                keyring_secret="coinone.secret.custom",
            )
            credentials = load_credentials(
                "coinone",
                source="keyring",
                keyring_service="OtherProject",
                keyring_primary="coinone.token.custom",
                keyring_secret="coinone.secret.custom",
            )

        self.assertEqual(credentials.access_token, "custom-token")
        self.assertEqual(credentials.secret_key, "custom-secret")
        self.assertIn(("OtherProject", "coinone.token.custom"), store)

    def test_missing_source_raises_not_found(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(CredentialNotFoundError):
                load_credentials("upbit", source="info_yaml", file_path="missing.yaml")


if __name__ == "__main__":
    unittest.main()
