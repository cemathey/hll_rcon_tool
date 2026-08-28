"""Local reimplementation of discord.py's markdown-escaping helpers.

Ports `discord.utils.escape_markdown`/`escape_mentions` (default arguments
only: `as_needed=False`, `ignore_links=True`) without pulling in the full
`discord` package, which costs ~30MB physical RAM per process just to import
and is otherwise unused outside of `rcon/seed_vip` but was widely imported.
Behavior is identical to discord.py's implementation for these defaults.

discord.py is MIT licensed, ported from `discord/utils.py` in discord.py 2.7.1 (the version pinned in
this project's `pyproject.toml` as `discord-py==2.7.1`):
https://github.com/Rapptz/discord.py/blob/v2.7.1/discord/utils.py

Parity with the original is covered by `tests/test_discord_markdown.py`.

In the event Discord or the dependency changes; this must change to match.
"""

import re

_URL_REGEX = r"(?P<url><[^: >]+:\/[^ >]+>|(?:https?|steam):\/\/[^\s<]+[^<.,:;\"'\]\s])"
_MARKDOWN_REGEX = r"(?P<markdown>[_\\~|\*`]|^>(?:>>)?\s|\[.+\]\(.+\)|^#{1,3}|^\s*-)"
_COMBINED_REGEX = f"(?:{_URL_REGEX}|{_MARKDOWN_REGEX})"
_MENTION_REGEX = re.compile(r"@(everyone|here|[!&]?[0-9]{17,20})")


def escape_markdown(text: str) -> str:
    def replacement(match: re.Match) -> str:
        groupdict = match.groupdict()
        url = groupdict.get("url")
        if url:
            return url
        return "\\" + groupdict["markdown"]

    return re.sub(_COMBINED_REGEX, replacement, text, count=0, flags=re.MULTILINE)


def escape_mentions(text: str) -> str:
    return _MENTION_REGEX.sub("@​\\1", text)
