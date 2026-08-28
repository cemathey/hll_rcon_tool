"""Regression tests for rcon/discord_markdown.py.

Covers two things:
1. Output parity with discord.py's actual escape_markdown/escape_mentions,
   so the local port can't silently drift from upstream behavior.
2. That importing the hot-path modules that used to pull in the full
   ``discord`` package no longer does so — this is the actual RAM
   regression this module exists to prevent.
"""

import os
import subprocess
import sys

import pytest

os.environ["HLL_MAINTENANCE_CONTAINER"] = "1"
from rcon.discord_markdown import escape_markdown, escape_mentions


@pytest.mark.parametrize(
    "text",
    [
        "hello *world*",
        "__underline__ and ~~strike~~",
        "check https://example.com/path_with_underscore link",
        "steam://joinlobby/123",
        "# heading\n- bullet\n> quote",
        "mention @everyone and @here and <@123456789012345678>",
        "plain text no markdown",
        "",
        "multi\nline **bold** text with `code`",
        "nested [link](https://example.com/_no_escape_) stays intact",
        "backslash already \\* escaped",
    ],
)
def test_escape_markdown_matches_discord_py(text):
    from discord.utils import escape_markdown as reference_escape_markdown

    assert escape_markdown(text) == reference_escape_markdown(text)


@pytest.mark.parametrize(
    "text",
    [
        "@everyone raid tonight",
        "@here check this out",
        "ping <@123456789012345678>",
        "ping <@!123456789012345678>",
        "ping <@&123456789012345678>",
        "no mentions here",
        "",
        "short id not a mention @123",
    ],
)
def test_escape_mentions_matches_discord_py(text):
    from discord.utils import escape_mentions as reference_escape_mentions

    assert escape_mentions(text) == reference_escape_mentions(text)


@pytest.mark.parametrize(
    "module_name",
    [
        "rcon.discord",
        "rcon.discord_chat",
        "rcon.hooks",
        "rcon.logs.loop",
    ],
)
def test_hot_path_modules_do_not_import_discord_package(module_name):
    """These modules only need markdown-escaping, not the full discord.py
    package (gateway/voice/models etc, ~30MB RSS to import per process). CRCON
    runs several long-lived processes per game server (see
    config/supervisord.conf), so this cost is paid once per process per
    server. A module-level `import discord`/`from discord import ...`
    resurfacing in any of them is exactly the regression this test guards
    against.

    Runs in a fresh subprocess (not just a cleared sys.modules cache in this
    test process) since that's what actually matters: each of the real
    supervisord-managed processes starts cold, so the thing worth pinning is
    "importing this module in a clean interpreter never loads discord",
    not an artifact of this test process's already-populated import cache.
    """
    env = dict(os.environ, HLL_MAINTENANCE_CONTAINER="1")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import {module_name}; import sys; "
            f"assert 'discord' not in sys.modules, sorted(m for m in sys.modules if m.startswith('discord'))",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"importing {module_name} pulled in the discord package "
        f"(or failed for another reason):\n{result.stderr}"
    )
