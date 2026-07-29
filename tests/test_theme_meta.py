"""Token metadata: friendly labels, grouping, and the two validation flags.

Headless. The metadata is presentation only — these tests mostly pin down that it
degrades rather than gating, and that its ``washed`` flags cannot silently rot.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mm_companion.ui.theme import loader, token_meta

META = token_meta.token_meta()
SRC = Path(__file__).resolve().parents[1] / "src" / "mm_companion"


# -- labels ---------------------------------------------------------------------


def test_a_listed_token_gets_its_friendly_name() -> None:
    assert META.label("tint.worse") == "Worse"
    assert "flaw" in META.hint("tint.worse")


def test_an_unlisted_token_still_gets_a_readable_label() -> None:
    """A mod's token, or one added after this file was last touched."""
    assert META.label("radius.somebodys-new-thing") == "Somebodys new thing"
    assert META.label("standalone") == "Standalone"
    assert META.hint("radius.somebodys-new-thing") == ""


def test_a_group_and_bucket_heading_fall_back_when_unnamed() -> None:
    assert META.group_label("colors") == "Colours"
    assert META.group_label("mystery") == "Mystery"
    assert META.bucket_label("tint") == "Semantic tints"
    assert META.bucket_label("mod-stuff") == "Mod Stuff"


# -- grouping -------------------------------------------------------------------


def test_buckets_come_in_the_preferred_order_then_alphabetically() -> None:
    grouped = token_meta.grouped(
        "colors", ["tint.a", "tint.b", "surface.a", "surface.b", "zz.a", "zz.b"]
    )

    assert [bucket for bucket, _ in grouped] == ["surface", "tint", "zz"]


def test_a_lone_unnamed_token_is_swept_into_other() -> None:
    grouped = dict(token_meta.grouped("colors", ["tint.a", "tint.b", "mystery.only"]))

    assert grouped[token_meta.OTHER_BUCKET] == ["mystery.only"]
    assert META.bucket_label(token_meta.OTHER_BUCKET) == "Other"


def test_a_named_bucket_keeps_its_heading_even_holding_one_token() -> None:
    """``focus`` and ``opacity`` are each a single token but deserve their own box."""
    grouped = dict(token_meta.grouped("metrics", ["opacity.inactive", "radius.a", "radius.b"]))

    assert grouped["opacity"] == ["opacity.inactive"]
    assert token_meta.OTHER_BUCKET not in grouped


def test_every_bundled_token_lands_in_some_bucket() -> None:
    for preset in loader.available_themes().values():
        for group in ("colors", "metrics", "typography"):
            names = sorted(getattr(preset, group))
            placed = [n for _bucket, members in token_meta.grouped(group, names) for n in members]
            assert sorted(placed) == names


# -- the flags ------------------------------------------------------------------

#: ``theme.wash`` is also called with a variable in ui/drop_feedback.py; those two
#: tokens are named at its call sites and cannot be found by the grep below.
EXTRA_WASHED = {"accent", "drop.reject"}


def _washed_call_sites() -> set[str]:
    pattern = re.compile(r"wash\(\s*[\"']([^\"']+)[\"']")
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        found |= set(pattern.findall(path.read_text(encoding="utf-8")))
    return found


def test_every_washed_token_is_flagged_as_one() -> None:
    """A wash raises on a ``palette()`` value, deep inside a paint path.

    The editor refuses such a value up front — but only for tokens it knows are
    washed, so a new wash call site has to be declared here or the guard silently
    stops covering it.
    """
    for name in _washed_call_sites() | EXTRA_WASHED:
        assert META.is_washed(name), f"{name} has a wash derived from it but is not flagged"


def test_the_contrast_checked_tints_are_flagged() -> None:
    for name in ("tint.better", "tint.worse", "tint.warning", "accent", "accent.dice"):
        assert META.styles_text(name), f"{name} styles text but is not contrast-checked"


@pytest.mark.parametrize("name", sorted(META.tokens))
def test_no_metadata_entry_names_a_token_nothing_defines(name: str) -> None:
    """Not fatal if it did — but it would be dead weight nobody would notice."""
    known = set()
    for preset in loader.available_themes().values():
        known |= set(preset.colors) | set(preset.metrics) | set(preset.typography)

    assert name in known
