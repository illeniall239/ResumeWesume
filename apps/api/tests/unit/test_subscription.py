"""Detecting a Claude login without ever reading one.

The line this module walks is a real one. Anthropic's Agent SDK documentation
says third-party developers may not "offer claude.ai login or rate limits for
their products" without approval -- so this app must never implement a sign-in
of its own, and must never handle somebody's credential. Noticing that the
person at this keyboard has already signed in to Claude Code, and letting the
SDK use that, is the supported case.

Every test here is about staying on the right side of that: what is checked,
what is not opened, and what the user is told.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.llm import subscription


@pytest.fixture
def no_env(monkeypatch):
    for name in (subscription.TOKEN_ENV, *subscription.API_KEY_ENVS):
        monkeypatch.delenv(name, raising=False)


def _cli(monkeypatch, present: bool = True) -> None:
    monkeypatch.setattr(
        subscription.shutil, "which", lambda _: "/usr/bin/claude" if present else None
    )


def _credentials(monkeypatch, present: bool, path: Path | None = None) -> None:
    monkeypatch.setattr(
        subscription, "CREDENTIALS_PATH", path or Path("/nowhere/.credentials.json")
    )
    monkeypatch.setattr(Path, "is_file", lambda self: present)


class TestDetection:
    def test_a_signed_in_machine_offers_the_subscription(
        self, monkeypatch, no_env
    ) -> None:
        _cli(monkeypatch)
        _credentials(monkeypatch, present=True)

        state = subscription.detect()
        assert state.is_subscription
        assert "your Claude plan" in state.detail

    def test_an_environment_token_counts(self, monkeypatch, no_env) -> None:
        """Containers and CI keep the credential in the environment."""
        _cli(monkeypatch)
        monkeypatch.setenv(subscription.TOKEN_ENV, "sk-ant-oat-whatever")

        assert subscription.detect().is_subscription

    def test_no_cli_means_no_offer(self, monkeypatch, no_env) -> None:
        """The SDK runs Claude Code; without it there is nothing to run."""
        _cli(monkeypatch, present=False)

        state = subscription.detect()
        assert not state.available
        assert "not installed" in state.detail

    def test_installed_but_not_signed_in_says_what_to_do(
        self, monkeypatch, no_env
    ) -> None:
        _cli(monkeypatch)
        _credentials(monkeypatch, present=False)

        state = subscription.detect()
        assert not state.available
        assert "log in" in state.detail

    def test_an_api_key_is_not_called_a_subscription(
        self, monkeypatch, no_env
    ) -> None:
        """These bill differently and the difference is the user's money.

        An API key is charged per token to a Console org; a subscription draws
        from a plan already paid for. Reporting one as the other would be a lie
        about what a turn costs.
        """
        _cli(monkeypatch)
        _credentials(monkeypatch, present=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")

        state = subscription.detect()
        assert state.available
        assert not state.is_subscription
        assert state.via == "api_key"
        assert "billed per token" in state.detail


class TestItNeverReadsTheCredential:
    def test_the_file_is_stat_ed_and_never_opened(self, monkeypatch, no_env) -> None:
        """The strongest guarantee this module can offer.

        Existence answers the question. Opening the file would put somebody's
        credential in this process for no reason, and the SDK is the only thing
        that needs its contents.
        """
        _cli(monkeypatch)
        _credentials(monkeypatch, present=True)

        def explode(*args: object, **kwargs: object):
            raise AssertionError("the credential file must never be opened")

        monkeypatch.setattr(Path, "open", explode)
        monkeypatch.setattr(Path, "read_text", explode)
        monkeypatch.setattr(Path, "read_bytes", explode)

        assert subscription.detect().is_subscription

    def test_an_unreadable_home_directory_is_not_a_crash(
        self, monkeypatch, no_env
    ) -> None:
        _cli(monkeypatch)
        monkeypatch.setattr(
            Path, "is_file", lambda self: (_ for _ in ()).throw(OSError("denied"))
        )

        assert not subscription.detect().available
