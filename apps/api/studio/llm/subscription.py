"""Whether this machine already has a Claude subscription signed in.

The question is narrow on purpose: *is there an existing login here that the
Claude Agent SDK will pick up on its own?* Not "log the user in", and not "read
their token".

That distinction is the whole design. Anthropic's Agent SDK documentation is
explicit:

    Unless previously approved, Anthropic does not allow third party
    developers to offer claude.ai login or rate limits for their products,
    including agents built on the Claude Agent SDK.

An app that implements its own claude.ai sign-in, or that routes other people's
subscription credentials through a server it runs, is the thing that rule
forbids. Noticing that the person at this keyboard has already logged into
Claude Code, on their own machine, and letting the SDK use that login exactly as
`claude -p` would, is the supported case -- Anthropic's help centre puts
"Claude Agent SDK, `claude -p`, and third-party app usage" together as drawing
from the subscription's usage limits.

So the check below reads no secrets. It asks whether the CLI is installed and
whether a credential *exists*, never what it contains, and never sends it
anywhere. The SDK does the authenticating; this module only decides whether to
offer the option.

If this application is ever hosted for other people rather than run locally,
this stops being the supported case and needs Anthropic's approval first.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Where Claude Code keeps a subscription login. Existence is all that is ever
#: checked -- the file is never opened.
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"

#: A long-lived subscription token, for machines where the credential lives in
#: the environment instead of on disk (containers, CI).
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

#: The keys that mean "billed per token against a console org" rather than
#: "drawn from a subscription". Checked only so the UI can say which one is
#: about to be used; an app key is not a subscription and is not described as
#: one.
API_KEY_ENVS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


@dataclass(frozen=True)
class Subscription:
    """What was found, and what to tell the user about it."""

    #: Whether the Agent SDK can run at all here.
    available: bool
    #: How it will authenticate: "subscription", "api_key", or "" when it cannot.
    via: str = ""
    #: One line for the settings page, in the user's terms.
    detail: str = ""

    @property
    def is_subscription(self) -> bool:
        return self.available and self.via == "subscription"


def detect() -> Subscription:
    """Look for an existing Claude login on this machine.

    Cheap enough to call whenever the settings page is opened: one PATH lookup
    and one stat.
    """
    cli = shutil.which("claude")
    if cli is None:
        return Subscription(
            available=False,
            detail=(
                "Claude Code is not installed on this machine. Install it and "
                "sign in to use your Claude subscription here."
            ),
        )

    if os.environ.get(TOKEN_ENV):
        return Subscription(
            available=True,
            via="subscription",
            detail=f"Signed in through {TOKEN_ENV}. Usage draws from your Claude plan.",
        )

    # Existence only. Opening this file would be reading somebody's credential
    # for no reason -- the SDK is the only thing that needs its contents.
    try:
        signed_in = CREDENTIALS_PATH.is_file()
    except OSError:  # pragma: no cover -- an unreadable home directory
        signed_in = False

    if signed_in:
        return Subscription(
            available=True,
            via="subscription",
            detail=(
                "Signed in to Claude Code on this machine. Usage draws from "
                "your Claude plan rather than an API key."
            ),
        )

    key = next((name for name in API_KEY_ENVS if os.environ.get(name)), None)
    if key:
        return Subscription(
            available=True,
            via="api_key",
            detail=(
                f"Using {key}. This is billed per token to your Claude Console "
                "org, not to a subscription."
            ),
        )

    return Subscription(
        available=False,
        detail=(
            "Claude Code is installed but not signed in. Run `claude` once and "
            "log in, then this option becomes available."
        ),
    )
