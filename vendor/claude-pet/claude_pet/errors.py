"""Shared error base.

Kept in its own stdlib-only module so the CLI can catch every expected failure
without importing `sprites` (and therefore Pillow) on the hook fast path.
"""


class PetError(Exception):
    """Any expected, user-facing failure."""
