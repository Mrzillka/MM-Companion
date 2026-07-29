"""Human-readable names, help text and grouping for the theme tokens.

The Settings window builds its form by iterating the active preset's token maps,
so it always shows *every* token — including one a mod or a future release added
that nobody here has heard of. That is the point, but a raw dotted name and a hex
string is a poor thing to hand a user, so this module supplies the friendly half:
a label, a sentence of help, which bucket a token belongs to, and the two facts a
value has to be checked against.

**Presentation only, in both directions.** Metadata never decides which tokens
exist: a token with no entry still gets a row, under a label derived from its own
name, and an entry naming a token no preset defines is simply never asked for. So
``token_meta.json`` never has to be exhaustive or kept in lockstep with a preset.

It lives beside the presets but deliberately *not* inside ``themes/`` — the loader
globs that directory and would try to parse this as a preset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from typing import Any

RESOURCE_PACKAGE = "mm_companion.ui.theme"
RESOURCE_NAME = "token_meta.json"

#: The bucket that collects tokens whose own bucket holds only them.
OTHER_BUCKET = "_other"
OTHER_LABEL = "Other"


def bucket_of(name: str) -> str:
    """The bucket a token sorts into: its first dotted segment.

    ``"tint.better"`` and ``"tint.worse"`` share ``tint``; ``"family"``, having no
    dot, is its own.
    """
    return name.split(".", 1)[0]


def derived_label(name: str) -> str:
    """A readable label for a token with no metadata entry.

    Drops the bucket prefix (the group box already says it), turns the separators
    into spaces and capitalises: ``"border.width.accent-edge"`` reads as
    ``"Width accent edge"``.
    """
    tail = name.split(".", 1)[1] if "." in name else name
    words = tail.replace(".", " ").replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else name


@dataclass(frozen=True)
class TokenMeta:
    """The parsed metadata, with a derived answer for anything it doesn't know."""

    groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    buckets: dict[str, str] = field(default_factory=dict)
    tokens: dict[str, dict[str, Any]] = field(default_factory=dict)

    def group_label(self, group: str) -> str:
        """The heading for a whole token group — "Colours" for ``colors``."""
        return str(self.groups.get(group, {}).get("label") or group.title())

    def bucket_order(self, group: str) -> tuple[str, ...]:
        """The buckets of *group* in their preferred order; the rest follow sorted."""
        order = self.groups.get(group, {}).get("order") or []
        return tuple(str(item) for item in order)

    def bucket_label(self, bucket: str) -> str:
        """The heading for one bucket, derived when unnamed."""
        if bucket == OTHER_BUCKET:
            return OTHER_LABEL
        named = self.buckets.get(bucket)
        return str(named) if named else bucket.replace("-", " ").title()

    def is_named_bucket(self, bucket: str) -> bool:
        """Whether *bucket* is worth its own group even holding a single token."""
        return bucket in self.buckets

    def label(self, name: str) -> str:
        """The friendly name of one token, derived when unlisted."""
        entry = self.tokens.get(name) or {}
        return str(entry.get("label") or derived_label(name))

    def hint(self, name: str) -> str:
        """One sentence about what the token does, or ``""``."""
        return str((self.tokens.get(name) or {}).get("hint") or "")

    def is_washed(self, name: str) -> bool:
        """Whether a translucent fill is derived from this colour.

        Those must stay a literal ``#rrggbb``: ``theme.wash`` raises on a
        ``palette(role)``, and it would raise deep inside a card's paint path,
        where the user could never connect it back to the colour they picked.
        """
        return bool((self.tokens.get(name) or {}).get("washed"))

    def styles_text(self, name: str) -> bool:
        """Whether this colour styles text and is held to the contrast floor."""
        return bool((self.tokens.get(name) or {}).get("text_on"))


@lru_cache(maxsize=1)
def token_meta() -> TokenMeta:
    """The parsed ``token_meta.json``, read once per process."""
    text = files(RESOURCE_PACKAGE).joinpath(RESOURCE_NAME).read_text(encoding="utf-8")
    raw = json.loads(text)
    return TokenMeta(
        groups=_section(raw, "groups"),
        buckets={key: str(value) for key, value in _section(raw, "buckets").items()},
        tokens=_section(raw, "tokens"),
    )


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """One top-level section, with the file's ``_``-prefixed inline notes dropped."""
    section = raw.get(key) or {}
    if not isinstance(section, dict):
        return {}
    return {name: value for name, value in section.items() if not name.startswith("_")}


def grouped(group: str, names: list[str]) -> list[tuple[str, list[str]]]:
    """*names* arranged into ``(bucket, tokens)`` pairs, ready to lay out.

    Buckets come in the metadata's preferred order, then the rest alphabetically.
    A bucket holding a single token is folded into a trailing "Other" — unless the
    metadata names it, which is how ``focus`` and ``opacity`` keep their own
    heading rather than being swept in with whatever a mod happened to add.
    """
    meta = token_meta()
    buckets: dict[str, list[str]] = {}
    for name in names:
        buckets.setdefault(bucket_of(name), []).append(name)

    preferred = meta.bucket_order(group)
    ordered = [b for b in preferred if b in buckets]
    ordered += sorted(b for b in buckets if b not in preferred)

    result: list[tuple[str, list[str]]] = []
    other: list[str] = []
    for bucket in ordered:
        members = sorted(buckets[bucket])
        if len(members) == 1 and not meta.is_named_bucket(bucket):
            other.extend(members)
        else:
            result.append((bucket, members))
    if other:
        result.append((OTHER_BUCKET, other))
    return result
