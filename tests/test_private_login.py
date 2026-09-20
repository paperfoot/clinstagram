"""Tests for the resilient private API login flow."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from clinstagram.auth.private_login import (
    LoginConfig,
    LoginResult,
    _configure_client,
    _extract_uuids,
    _validate_session,
    login_private,
)


class TestExtractUuids:
    def test_extracts_known_keys(self):
        settings = {
            "uuid": "abc-123",
            "phone_id": "def-456",
            "device_id": "ghi-789",
            "advertising_id": "jkl-012",
            "device_settings": {"model": "Pixel 7"},
            "sessionid": "ignored",
        }
        uuids = _extract_uuids(settings)
        assert uuids["uuid"] == "abc-123"
        assert uuids["phone_id"] == "def-456"
        assert uuids["device_id"] == "ghi-789"
        assert uuids["advertising_id"] == "jkl-012"
        assert uuids["device_settings"]["model"] == "Pixel 7"
        assert "sessionid" not in uuids

    def test_empty_settings(self):
        assert _extract_uuids({}) == {}

    def test_partial_settings(self):
        uuids = _extract_uuids({"uuid": "abc"})
        assert uuids == {"uuid": "abc"}


class TestConfigureClient:
    def test_sets_proxy(self):
        client = MagicMock()
        config = LoginConfig(username="u", password="p", proxy="http://proxy:8080")
        _configure_client(client, config)
        client.set_proxy.assert_called_once_with("http://proxy:8080")

    def test_sets_delay_range(self):
        client = MagicMock()
        config = LoginConfig(username="u", password="p", delay_range=[2, 5])
        _configure_client(client, config)
        assert client.delay_range == [2, 5]

    def test_sets_totp_seed(self):
        client = MagicMock()
        config = LoginConfig(username="u", password="p", totp_seed="JBSWY3DPEHPK3PXP")
        _configure_client(client, config)
        assert client.totp_seed == "JBSWY3DPEHPK3PXP"

    def test_no_proxy_no_set(self):
        client = MagicMock()
        config = LoginConfig(username="u", password="p")
        _configure_client(client, config)
        client.set_proxy.assert_not_called()

    def test_challenge_handler_assigned(self):
        client = MagicMock()
        handler = MagicMock()
        config = LoginConfig(username="u", password="p", challenge_handler=handler)
        _configure_client(client, config)
        assert client.challenge_code_handler is handler

    def test_locale_and_timezone(self):
        client = MagicMock()
        config = LoginConfig(username="u", password="p", locale="en_US", timezone="-18000")
        _configure_client(client, config)
        client.set_locale.assert_called_once_with("en_US")
        client.set_timezone_offset.assert_called_once_with(-18000)


class TestValidateSession:
    def test_valid(self):
        client = MagicMock()
        client.account_info.return_value = MagicMock()
        assert _validate_session(client) is True
        client.account_info.assert_called_once()

    def test_invalid(self):
        client = MagicMock()
        client.account_info.side_effect = RuntimeError("401")
        assert _validate_session(client) is False


class TestLoginPrivate:
    """Test login_private with fully mocked instagrapi."""

    @patch("instagrapi.Client")
    @patch("clinstagram.auth.private_login._validate_session", return_value=True)
    def test_fresh_login_success(self, mock_validate, MockClient):
        cl = MagicMock()
        cl.get_settings.return_value = {"uuid": "abc", "sessionid": "sess123"}
        MockClient.return_value = cl

        config = LoginConfig(username="testuser", password="pass123")
        result = login_private(config)

        assert result.success is True
        assert result.username == "testuser"
        assert result.session_json != ""
        cl.login.assert_called_once_with("testuser", "pass123")

    @patch("instagrapi.Client")
    @patch("clinstagram.auth.private_login._validate_session", return_value=True)
    def test_session_restore_success(self, mock_validate, MockClient):
        cl = MagicMock()
        cl.get_settings.return_value = {"uuid": "abc", "sessionid": "sess-new"}
        MockClient.return_value = cl

        config = LoginConfig(username="testuser", password="pass123")
        existing = json.dumps({"uuid": "abc", "sessionid": "sess-old"})
        result = login_private(config, existing_session=existing)

        assert result.success is True
        cl.set_settings.assert_called_once()
        cl.login.assert_not_called()

    @patch("instagrapi.Client")
    @patch("clinstagram.auth.private_login._validate_session")
    def test_session_expired_relogin(self, mock_validate, MockClient):
        """When session is invalid, should re-login with preserved UUIDs."""
        cl1 = MagicMock()
        cl1.get_settings.return_value = {
            "uuid": "device-uuid",
            "phone_id": "phone-123",
            "sessionid": "old-sess",
        }

        cl2 = MagicMock()
        cl2.get_settings.return_value = {"uuid": "device-uuid", "sessionid": "new-sess"}

        MockClient.side_effect = [cl1, cl2]
        # First validate fails (expired), second succeeds (after relogin)
        mock_validate.side_effect = [False, True]

        config = LoginConfig(username="testuser", password="pass123")
        existing = json.dumps({"uuid": "device-uuid", "sessionid": "old-sess"})
        result = login_private(config, existing_session=existing)

        assert result.success is True
        assert result.relogin is True
        # Second client should have had set_uuids called
        cl2.set_uuids.assert_called_once()
        uuids = cl2.set_uuids.call_args[0][0]
        assert uuids["uuid"] == "device-uuid"
        assert uuids["phone_id"] == "phone-123"

    @patch("instagrapi.Client")
    def test_fresh_login_challenge_required(self, MockClient):
        from instagrapi.exceptions import ChallengeRequired

        cl = MagicMock()
        cl.login.side_effect = ChallengeRequired()
        MockClient.return_value = cl

        config = LoginConfig(username="testuser", password="pass123")
        result = login_private(config)

        assert result.success is False
        assert result.challenge_required is True

    @patch("instagrapi.Client")
    def test_fresh_login_2fa_no_seed(self, MockClient):
        from instagrapi.exceptions import TwoFactorRequired

        cl = MagicMock()
        cl.login.side_effect = TwoFactorRequired()
        MockClient.return_value = cl

        config = LoginConfig(username="testuser", password="pass123")
        result = login_private(config)

        assert result.success is False
        assert "2FA required" in result.error

    @patch("instagrapi.Client")
    @patch("clinstagram.auth.private_login._validate_session", return_value=True)
    def test_fresh_login_2fa_with_seed(self, mock_validate, MockClient):
        from instagrapi.exceptions import TwoFactorRequired

        cl = MagicMock()
        cl.login.side_effect = [TwoFactorRequired(), True]
        cl.get_settings.return_value = {"uuid": "abc", "sessionid": "sess"}
        MockClient.return_value = cl

        with patch("instagrapi.mixins.totp.TOTPMixin") as MockTOTP:
            MockTOTP.totp_generate_code.return_value = "123456"
            config = LoginConfig(username="testuser", password="pass123", totp_seed="SEED")
            result = login_private(config)

        assert result.success is True
        cl.login.assert_any_call("testuser", "pass123", verification_code="123456")

    @patch("instagrapi.Client")
    def test_fresh_login_generic_error(self, MockClient):
        cl = MagicMock()
        cl.login.side_effect = ConnectionError("Network down")
        MockClient.return_value = cl

        config = LoginConfig(username="testuser", password="pass123")
        result = login_private(config)

        assert result.success is False
        assert "Network down" in result.error

    @patch("instagrapi.Client")
    @patch("clinstagram.auth.private_login._validate_session", return_value=False)
    def test_fresh_login_validation_fails(self, mock_validate, MockClient):
        cl = MagicMock()
        cl.get_settings.return_value = {"sessionid": "s"}
        MockClient.return_value = cl

        config = LoginConfig(username="testuser", password="pass123")
        result = login_private(config)

        assert result.success is False
        assert "validation failed" in result.error

    @patch("instagrapi.Client")
    @patch("clinstagram.auth.private_login._validate_session", return_value=True)
    def test_proxy_passed_through(self, mock_validate, MockClient):
        cl = MagicMock()
        cl.get_settings.return_value = {"uuid": "abc"}
        MockClient.return_value = cl

        config = LoginConfig(username="u", password="p", proxy="socks5://1.2.3.4:1080")
        login_private(config)

        cl.set_proxy.assert_called_with("socks5://1.2.3.4:1080")


class TestLoginResult:
    def test_default_values(self):
        r = LoginResult(success=False)
        assert r.username == ""
        assert r.session_json == ""
        assert r.error == ""
        assert r.remediation == ""
        assert r.challenge_required is False
        assert r.relogin is False


class TestBadPasswordHandling:
    """Verify BadPassword is caught explicitly (not string-matched)."""

    @patch("instagrapi.Client")
    def test_bad_password_no_email_hint(self, MockClient):
        from instagrapi.exceptions import BadPassword

        cl = MagicMock()
        cl.login.side_effect = BadPassword()
        MockClient.return_value = cl

        config = LoginConfig(username="testuser", password="wrong")
        result = login_private(config)

        assert result.success is False
        assert "rejected the login" in result.error
        assert "email" in result.remediation  # hint to try email
        assert "IP" not in result.error  # no misleading IP message

    @patch("instagrapi.Client")
    def test_bad_password_with_email_no_extra_hint(self, MockClient):
        from instagrapi.exceptions import BadPassword

        cl = MagicMock()
        cl.login.side_effect = BadPassword()
        MockClient.return_value = cl

        config = LoginConfig(username="user@example.com", password="wrong")
        result = login_private(config)

        assert result.success is False
        # Should NOT suggest trying email when already using email
        assert "retry with that exact email" not in result.remediation

    @patch("instagrapi.Client")
    def test_sentry_block(self, MockClient):
        from instagrapi.exceptions import SentryBlock

        cl = MagicMock()
        cl.login.side_effect = SentryBlock()
        MockClient.return_value = cl

        config = LoginConfig(username="testuser", password="pass")
        result = login_private(config)

        assert result.success is False
        assert "suspicious" in result.error
        assert result.remediation != ""

    @patch("instagrapi.Client")
    def test_please_wait(self, MockClient):
        from instagrapi.exceptions import PleaseWaitFewMinutes

        cl = MagicMock()
        cl.login.side_effect = PleaseWaitFewMinutes()
        MockClient.return_value = cl

        config = LoginConfig(username="testuser", password="pass")
        result = login_private(config)

        assert result.success is False
        assert "wait" in result.error.lower()


class TestDeviceFingerprint:
    """Verify per-account device fingerprinting via set_device()."""

    def test_configure_uses_set_device(self):
        client = MagicMock()
        config = LoginConfig(username="u", password="p")
        _configure_client(client, config)
        client.set_device.assert_called_once()
        device_arg = client.set_device.call_args[0][0]
        # Must have key device fields from the pool
        assert "manufacturer" in device_arg
        assert "model" in device_arg
        assert "android_version" in device_arg

    def test_deterministic_per_account_fingerprint(self):
        """Same account always gets the same device; different accounts may differ."""
        from clinstagram.auth.private_login import _generate_device_for_account

        d1 = _generate_device_for_account("account_alpha")
        d2 = _generate_device_for_account("account_alpha")
        assert d1 == d2  # deterministic
        assert isinstance(d1, dict)
        assert "model" in d1

    def test_configure_rebuilds_user_agent(self):
        client = MagicMock()
        config = LoginConfig(username="u", password="p")
        _configure_client(client, config)
        client.set_user_agent.assert_called_once()

    def test_custom_device_overrides_defaults(self):
        client = MagicMock()
        config = LoginConfig(username="u", password="p", device_settings={"model": "Galaxy S24"})
        _configure_client(client, config)
        device_arg = client.set_device.call_args[0][0]
        assert device_arg["model"] == "Galaxy S24"
        assert device_arg["manufacturer"] == "Google"  # default preserved


class TestLoginCommand:
    """Test the CLI login command integration."""

    @patch("clinstagram.auth.private_login.login_private")
    def test_login_command_success(self, mock_login):
        from typer.testing import CliRunner

        from clinstagram.cli import app

        runner = CliRunner()
        mock_login.return_value = LoginResult(
            success=True,
            username="testuser",
            session_json='{"sessionid": "abc"}',
        )

        result = runner.invoke(
            app,
            ["--json", "auth", "login", "--username", "testuser", "--password", "pass123"],
            env={"CLINSTAGRAM_CONFIG_DIR": "/tmp/clinstagram-test", "CLINSTAGRAM_TEST_MODE": "1"},
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["username"] == "testuser"
        assert data["data"]["relogin"] is False
        assert data["backend_used"] == "private"

    @patch("clinstagram.auth.private_login.login_private")
    def test_login_command_failure(self, mock_login):
        from typer.testing import CliRunner

        from clinstagram.cli import app

        runner = CliRunner()
        mock_login.return_value = LoginResult(
            success=False,
            username="testuser",
            error="Bad password",
        )

        result = runner.invoke(
            app,
            ["--json", "auth", "login", "--username", "testuser", "--password", "wrong"],
            env={"CLINSTAGRAM_CONFIG_DIR": "/tmp/clinstagram-test", "CLINSTAGRAM_TEST_MODE": "1"},
        )

        assert result.exit_code == 2

    @patch("clinstagram.auth.private_login.login_private")
    def test_login_stores_session(self, mock_login):
        from typer.testing import CliRunner

        from clinstagram.cli import app

        runner = CliRunner()
        mock_login.return_value = LoginResult(
            success=True,
            username="testuser",
            session_json='{"sessionid": "stored-session"}',
        )

        result = runner.invoke(
            app,
            ["--json", "auth", "login", "--username", "testuser", "--password", "pass123"],
            env={"CLINSTAGRAM_CONFIG_DIR": "/tmp/clinstagram-test", "CLINSTAGRAM_TEST_MODE": "1"},
        )

        assert result.exit_code == 0
        # Verify login_private was called
        mock_login.assert_called_once()
