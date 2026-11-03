"""Run only the tests a change can affect.

Two halves:

* **Python** — pytest-testmon records which lines of ``src/`` and ``tests/`` every
  test executes (``.testmondata``) and, on ``--testmon``, runs only the tests whose
  executed code has changed since the run that recorded them, plus new tests.
* **Everything testmon cannot see** — game data, block sizes, theme presets, icons,
  the sample mods, the test platform pins. This script fingerprints those files
  after every green run (``.affected-tests/``), and on the next run turns what
  changed into extra tests: for a JSON file, the tests that name an ``id`` or key
  that changed; for anything else, the tests that name the file.

Usage (from anywhere in the repo)::

    python .claude/skills/affected-tests/affected.py            # select and run
    python .claude/skills/affected-tests/affected.py --dry      # only show the selection
    python .claude/skills/affected-tests/affected.py --full     # run all, rebuild the records
    python .claude/skills/affected-tests/affected.py -- -x -k x  # anything after -- goes to pytest

Exit status is pytest's (0 when every selected test passed, or nothing needed running).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
    ).stdout.strip()
)
STATE_DIR = ROOT / ".affected-tests"
STATE_FILE = STATE_DIR / "fingerprints.json"
SNAPSHOT_DIR = STATE_DIR / "snapshot"
TESTMON_DB = ROOT / ".testmondata"
TESTS = ROOT / "tests"

# Any change here can move any test, so it means a full run.
FULL_RUN_PATTERNS = (
    re.compile(r"^pyproject\.toml$"),
    re.compile(r"^tests/(.+/)?conftest\.py$"),
    re.compile(r"^tests/qt/"),
)
# Game content: the rules layer reads all of it, and tests/core is cheap (~10s).
GAME_DATA = re.compile(r"^src/mm_companion/data/")
# A name that turns up in more test files than this says nothing about which
# tests a change touches ("name", "cost", "rank"), so it selects nothing.
TOO_COMMON = 12


# -- what is being watched ---------------------------------------------------


def _watched() -> list[str]:
    """Every tracked or new (not ignored) file testmon is blind to, repo-relative."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    watched = []
    for path in filter(None, listed):
        if not (ROOT / path).is_file():
            continue  # deleted in the working tree but still in the index
        in_scope = path.startswith(("src/", "tests/", "docs/sample-mods/"))
        if path == "pyproject.toml" or path.endswith("conftest.py"):
            watched.append(path)
        elif in_scope and not path.endswith((".py", ".pyc")):
            watched.append(path)
    return sorted(watched)


def _digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _load_state() -> dict[str, str] | None:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_state(paths: list[str]) -> None:
    """Remember these files as the last green run saw them, JSON content included."""
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)
    SNAPSHOT_DIR.mkdir(parents=True)
    for path in paths:
        if path.endswith(".json"):
            target = SNAPSHOT_DIR / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
    STATE_FILE.write_text(
        json.dumps({p: _digest(p) for p in paths}, indent=1, sort_keys=True), encoding="utf-8"
    )


# -- turning a change into tests -----------------------------------------------


def _changed_names(old, new, trail: tuple[str, ...] = (), in_item: bool = False) -> set[str]:
    """The ids and keys that lead to every difference between two JSON values.

    A list of objects with an ``id`` is matched up by id, so inserting one item
    reports that item rather than every item after it — and a change inside an item
    reports only its id, since its field names ("description", "cost") are shared
    by every other item and would point at tests that have nothing to do with it.
    """
    if old == new:
        return set()
    if isinstance(old, dict) and isinstance(new, dict):
        names = set()
        for key in old.keys() | new.keys():
            if old.get(key) != new.get(key):
                names |= _changed_names(old.get(key), new.get(key), (*trail, key), in_item)
        return names
    if _is_id_list(old) or _is_id_list(new):
        before = {item["id"]: item for item in old or [] if isinstance(item, dict)}
        after = {item["id"]: item for item in new or [] if isinstance(item, dict)}
        names = set()
        for item_id in before.keys() | after.keys():
            if before.get(item_id) != after.get(item_id):
                names.add(str(item_id))
                # Tests name some content by id ("affliction") and some by its
                # display name ("Improved Initiative"), so both count.
                for item in (before.get(item_id), after.get(item_id)):
                    if isinstance(item, dict) and isinstance(item.get("name"), str):
                        names.add(item["name"])
                names |= _changed_names(
                    before.get(item_id), after.get(item_id), (*trail, str(item_id)), True
                )
        return names
    if in_item:
        return set()  # the item's id already names it
    # A leaf, or a list without ids: name the place it hangs from.
    names = set(trail[-1:])
    if len(trail) >= 2:
        names.add(f"{trail[-2]}.{trail[-1]}")  # theme tokens are read as "group.name"
    return names


def _is_id_list(value) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, dict) and "id" in item for item in value)
    )


def _test_texts() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8", errors="replace")
        for path in TESTS.rglob("test_*.py")
    }


def _tests_naming(name: str, texts: dict[str, str], quoted: bool) -> list[str]:
    if quoted:
        pattern = re.compile(r"""(["'])""" + re.escape(name) + r"\1")
        return [path for path, text in texts.items() if pattern.search(text)]
    return [path for path, text in texts.items() if name in text]


def _select(changed: list[str], texts: dict[str, str]):
    """Extra test paths for the non-Python changes, with a reason for each."""
    extra: dict[str, set[str]] = {}  # test path -> reasons
    ignored: list[str] = []

    def add(paths, reason):
        for path in paths:
            extra.setdefault(path, set()).add(reason)

    for path in changed:
        if GAME_DATA.match(path):
            add(["tests/core"], f"{path} is game data")
        names: set[str] = set()
        old = SNAPSHOT_DIR / path
        if path.endswith(".json") and old.is_file() and (ROOT / path).is_file():
            try:
                before = json.loads(old.read_text(encoding="utf-8"))
                after = json.loads((ROOT / path).read_text(encoding="utf-8"))
                names = {n for n in _changed_names(before, after) if len(n) >= 3}
            except ValueError:
                names = set()
        for name in sorted(names):
            hits = _tests_naming(name, texts, quoted=True)
            if len(hits) > TOO_COMMON:
                ignored.append(f"{name!r} ({len(hits)} test files)")
            else:
                add(hits, f'{path}: "{name}" changed')
        # Whatever else changed about it, tests that name the file itself are in.
        stem = Path(path).stem
        if path.startswith("docs/sample-mods/"):
            add(_tests_naming("sample-mods", texts, quoted=False), f"{path} is a sample mod")
        elif len(stem) >= 4:
            hits = _tests_naming(Path(path).name, texts, quoted=False)
            hits += _tests_naming(stem, texts, quoted=True)
            if len(set(hits)) <= TOO_COMMON:
                add(sorted(set(hits)), f"{path} changed")
            else:
                ignored.append(f"{stem!r} ({len(set(hits))} test files)")
    # A folder already selected covers the files inside it.
    folders = [p for p in extra if not p.endswith(".py")]
    for path in [p for p in extra if p.endswith(".py")]:
        folder = next((f for f in folders if path.startswith(f + "/")), None)
        if folder is not None:
            extra[folder] |= extra.pop(path)
    return extra, ignored


# -- running ---------------------------------------------------------------------


def _pytest(args: list[str]) -> int:
    # affected_tests_order.py, beside this file, keeps the tests in file order.
    command = [sys.executable, "-m", "pytest", "-p", "affected_tests_order", *args]
    print("\n$ " + " ".join(command[2:]), flush=True)
    env = dict(os.environ)
    here = str(Path(__file__).resolve().parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [here, env.get("PYTHONPATH")]))
    return subprocess.run(command, cwd=ROOT, env=env).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry", action="store_true", help="show the selection, run nothing")
    parser.add_argument("--full", action="store_true", help="run everything, rebuild records")
    parser.add_argument("pytest_args", nargs="*", help="after --, passed to pytest")
    options = parser.parse_args()
    joined = " ".join(options.pytest_args).replace("-p ", "-p")
    if "-pno:cacheprovider" in joined:
        # testmon reads --lf, an option the cache plugin defines (KeyError: 'lf').
        parser.error("testmon needs pytest's cache plugin: drop -p no:cacheprovider")

    watched = _watched()
    state = _load_state()
    full_reason = None
    if options.full:
        full_reason = "asked for with --full"
    elif not TESTMON_DB.exists():
        full_reason = "no .testmondata yet — this run records what every test touches"
    elif state is None:
        full_reason = "no record of the last green run's data files"

    changed = []
    if state is not None:
        current = {p: _digest(p) for p in watched}
        changed = sorted(p for p in current.keys() | state.keys() if current.get(p) != state.get(p))
    if full_reason is None:
        for path in changed:
            if any(p.match(path) for p in FULL_RUN_PATTERNS):
                full_reason = f"{path} changed"
                break

    print("Non-Python files changed since the last green run:", len(changed))
    for path in changed[:20]:
        print("   ", path)
    if len(changed) > 20:
        print(f"    … and {len(changed) - 20} more")

    if full_reason:
        print(f"\nFULL RUN: {full_reason}")
        if options.dry:
            return 0
        code = _pytest(["--testmon-noselect", *options.pytest_args])
    else:
        extra, ignored = _select(changed, _test_texts())
        print("\nTests for changed Python code: testmon picks those in the last run below.")
        print(f"Extra tests for the non-Python changes: {len(extra)} path(s)")
        for path, reasons in sorted(extra.items()):
            print(f"    {path}  <- {'; '.join(sorted(reasons))}")
        if ignored:
            print("Names too common to select by:", ", ".join(ignored))
        if options.dry:
            return 0
        code = 0
        if extra:
            code = _pytest(["--testmon-noselect", *sorted(extra), *options.pytest_args])
        # After the extras so testmon sees them as already run against this code.
        second = _pytest(["--testmon", *options.pytest_args])
        code = code if code not in (0, 5) else second

    if code in (0, 5):  # 5: nothing was selected, which is a pass too
        _save_state(watched)
        print("\nAll selected tests passed; records updated.")
        return 0
    print("\nSome tests failed. The data-file records were NOT updated, so the same")
    print("extra tests are picked again next time; testmon reruns its failures itself.")
    return code


if __name__ == "__main__":
    sys.exit(main())
