"""MM-Companion: a dice roller and character creator for Mutants & Masterminds."""

# Single source of truth for the app/package version. ``pyproject.toml`` derives
# its ``version`` from this attribute, and the installer build reads it too, so a
# release is just a bump here.
#
# Versions are stored as proper SemVer so they compare/order correctly. The
# shorthand used in release notes maps as:
#     0.1  -> 0.1.0
#     0.11 -> 0.1.1
#     0.12 -> 0.1.2
#     0.2  -> 0.2.0
__version__ = "0.2.2"
