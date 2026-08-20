"""UPC → product name/brand via Open Food Facts (free, no key).

No model involved (§3 step 2). Unknown UPC is a normal outcome, not an error.
"""

from __future__ import annotations


def lookup(upc: str) -> dict | None:
    """{'product_name': str, 'brand': str, 'upc': str} or None if unknown.
    Network failure raises requests exceptions for the caller to degrade on."""
    raise NotImplementedError("Phase 1 — see plan §3")
