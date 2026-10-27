from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from codex_api_provider_switch import build_demo_controller
from provider_switch.balances import (
    BalanceError, deepseek_balance_url, format_balance_summary, parse_deepseek_balance,
)
from provider_switch.models import AppSettings, default_profiles
from provider_switch.monitoring import MonitoringError, check_provider


def balance_payload(available: bool = True, total: str = "110.00") -> dict:
    return {"is_available": available, "balance_infos": [{
        "currency": "CNY", "total_balance": total,
        "granted_balance": "10.00", "topped_up_balance": "100.00",
    }]}


class DeepSeekBalanceTests(unittest.TestCase):
    def test_official_addresses_infer_balance_endpoint(self) -> None:
        profile = default_profiles()["deepseek"]
        for address in ("https://api.deepseek.com", "https://api.deepseek.com/",
                        "https://api.deepseek.com/v1/", "https://api.deepseek.com:443"):
            with self.subTest(address=address):
                profile.base_url = address
                self.assertEqual("https://api.deepseek.com/user/balance", deepseek_balance_url(profile))

    def test_custom_api_never_implicitly_sends_its_key_to_official(self) -> None:
        profile = default_profiles()["deepseek"]
        for address in ("https://relay.example/v1", "https://api.deepseek.com.evil.example",
                        "http://api.deepseek.com", "https://api.deepseek.com:8443",
                        "https://user@api.deepseek.com", "https://api.deepseek.com/private"):
            profile.base_url = address
            self.assertEqual("", deepseek_balance_url(profile))
        profile.quota_url = "https://relay.example/account/balance"
        self.assertEqual(profile.quota_url, deepseek_balance_url(profile))

    def test_query_uses_only_official_balance_and_keeps_health_unknown(self) -> None:
        profile = default_profiles()["deepseek"]
        with patch("provider_switch.monitoring._http_json", return_value=(200, balance_payload(), 9)) as request:
            result = check_provider(profile, "test-deepseek-key", probe_health=False)
        request.assert_called_once_with("https://api.deepseek.com/user/balance", "test-deepseek-key")
        self.assertEqual("unknown", result.health.state)
        self.assertIsNone(result.health.latency_ms)
        self.assertEqual([], result.quota)
        self.assertEqual(Decimal("110.00"), result.balances[0].total_balance)
        self.assertEqual(Decimal("10.00"), result.balances[0].granted_balance)
        self.assertTrue(result.balance_available)
        self.assertTrue(result.balance_checked_at)

    def test_zero_balance_is_not_empty_or_a_percentage(self) -> None:
        payload = balance_payload(False, "0.00")
        payload["balance_infos"][0].update(granted_balance="0.00", topped_up_balance="0.00")
        with patch("provider_switch.monitoring._http_json", return_value=(200, payload, 2)):
            result = check_provider(default_profiles()["deepseek"], "test-key", probe_health=False)
        text = format_balance_summary(result, details=True)
        self.assertIn("CNY 0.00", text)
        self.assertIn("余额不足", text)
        self.assertNotIn("%", text)
        self.assertFalse(result.balance_available)

    def test_multiple_currencies_and_large_amounts_are_not_merged(self) -> None:
        payload = balance_payload(total="1234567.89")
        payload["balance_infos"].append({
            "currency": "USD", "total_balance": "0.10",
            "granted_balance": "0.03", "topped_up_balance": "0.07",
        })
        with patch("provider_switch.monitoring._http_json", return_value=(200, payload, 2)):
            result = check_provider(default_profiles()["deepseek"], "test-key", probe_health=False)
        text = format_balance_summary(result, details=True)
        self.assertIn("CNY 1,234,567.89", text)
        self.assertIn("USD 0.10", text)
        self.assertIn("赠送 0.03 / 充值 0.07", text)

    def test_malformed_or_empty_results_are_not_reported_as_updated(self) -> None:
        invalid = [{}, [], {"is_available": True, "balance_infos": []}]
        for field, value in (("total_balance", "NaN"), ("total_balance", "1e999"),
                             ("total_balance", True), ("granted_balance", None),
                             ("currency", "")):
            item = balance_payload()
            item["balance_infos"][0][field] = value
            invalid.append(item)
        missing = balance_payload()
        missing["is_available"] = "true"
        invalid.append(missing)
        duplicate = balance_payload()
        duplicate["balance_infos"].append(copy.deepcopy(duplicate["balance_infos"][0]))
        invalid.append(duplicate)
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(BalanceError):
                    parse_deepseek_balance(payload)
                with patch("provider_switch.monitoring._http_json", return_value=(200, payload, 2)):
                    result = check_provider(default_profiles()["deepseek"], "test-key", probe_health=False)
                self.assertFalse(result.balances)
                self.assertNotIn("已更新", result.quota_message)

    def test_http_and_network_errors_never_echo_sensitive_details(self) -> None:
        for status in (401, 403, 429, 500):
            with patch("provider_switch.monitoring._http_json", return_value=(status, {"error": "private-key"}, 1)):
                result = check_provider(default_profiles()["deepseek"], "private-key", probe_health=False)
            self.assertNotIn("已更新", result.quota_message)
            self.assertNotIn("private-key", result.quota_message)
        with patch("provider_switch.monitoring._http_json", side_effect=MonitoringError("private-key")):
            result = check_provider(default_profiles()["deepseek"], "private-key", probe_health=False)
        self.assertNotIn("private-key", result.quota_message)

    def test_old_profile_can_query_without_switching_or_selecting_a_model(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ds-balance-test-") as name:
            controller = build_demo_controller(Path(name))
            controller.credentials["deepseek"] = "test-ds-key"
            profile = controller.settings.profiles["deepseek"]
            profile.model = ""
            controller.settings = AppSettings.from_dict(controller.settings.to_dict())
            before = controller.config.config_path.read_bytes()
            auth_path = controller.codex_home / "auth.json"
            auth_path.write_text('{"test":"unchanged"}', encoding="utf-8")
            controller.process_controller.restart = Mock(side_effect=AssertionError("must not restart"))
            self.assertTrue(controller.quota_ready("deepseek"))
            self.assertFalse(controller.profile_ready("deepseek"))
            with patch("provider_switch.monitoring._http_json", return_value=(200, balance_payload(), 2)):
                result = controller.query_quota("deepseek")
            self.assertTrue(result.balances)
            self.assertEqual(before, controller.config.config_path.read_bytes())
            self.assertEqual('{"test":"unchanged"}', auth_path.read_text(encoding="utf-8"))
            self.assertEqual("relay1", controller.detect_active_profile())
            controller.process_controller.restart.assert_not_called()

    def test_real_http_uses_bearer_get_and_explicit_custom_endpoint(self) -> None:
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(balance_payload()).encode())

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                profile = default_profiles()["deepseek"]
                profile.quota_url = f"http://127.0.0.1:{server.server_port}/user/balance"
                result = check_provider(profile, "local-test-key", probe_health=False)
                self.assertTrue(result.balances)
                self.assertEqual([("/user/balance", "Bearer local-test-key")], received)
            finally:
                server.shutdown()
                worker.join(timeout=2)
