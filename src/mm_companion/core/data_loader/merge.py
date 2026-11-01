"""The mod merge: gather the active mods' content in load order and deep-merge it over the
base ruleset.
"""

from __future__ import annotations

from .. import mods as mods_module

# ===========================================================================
# Mod merge loader — gather the active mods' content in load order, deep-merge
# by record id, parse once, and cache. The public entry point lives at the end.
# ===========================================================================


# Candidate id fields for record lists, tried in order. Whichever a list's dict
# elements all carry identifies records for the by-id merge; a list whose elements
# share none (e.g. an ``options`` list of strings) is replaced wholesale by a mod.
_MERGE_ID_KEYS = ("id", "key", "name", "effect_id", "rank", "sizeRank", "column")


def _list_id_key(items: list) -> str | None:
    """The id field shared by every dict element of *items*, or ``None``."""
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    for key in _MERGE_ID_KEYS:
        if all(key in x for x in items):
            return key
    return None


def _merge_lists(base: list, override: list) -> list:
    base_key = _list_id_key(base)
    if base_key is None or base_key != _list_id_key(override):
        # Not record lists (or keyed differently): the mod replaces it wholesale.
        return list(override)
    result: list = []
    index: dict = {}
    for item in base:
        index[item[base_key]] = len(result)
        result.append(item)
    for item in override:
        ident = item[base_key]
        if ident in index:
            result[index[ident]] = _deep_merge(result[index[ident]], item)
        else:
            index[ident] = len(result)
            result.append(item)
    return result


def _deep_merge(base, override):
    """Recursively merge *override* onto *base* (later source wins).

    Dicts merge key-by-key; record lists (whose elements share an id field —
    see :data:`_MERGE_ID_KEYS`) merge by id, later records overriding earlier ones
    of the same id and new ids appended; anything else is replaced by *override*.
    This is how a mod overrides one record (supply just its id + changed fields) or
    adds new ones without restating the base content.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else value
        return merged
    if isinstance(base, list) and isinstance(override, list):
        return _merge_lists(base, override)
    return override


def _merge_content(mods: list[mods_module.Mod]) -> dict[str, dict]:
    """Deep-merge every content file across *mods* (base first) into ``name -> raw``."""
    filenames: list[str] = []
    for mod in mods:
        for filename in mod.files:
            if filename not in filenames:
                filenames.append(filename)
    merged: dict[str, dict] = {}
    for filename in filenames:
        acc: dict | None = None
        for mod in mods:
            raw = mod.read(filename)
            if raw is None:
                continue
            acc = raw if acc is None else _deep_merge(acc, raw)
        merged[filename] = acc if acc is not None else {}
    return merged
