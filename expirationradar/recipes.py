"""Expiring items → llm-ladder text cascade → 2-3 use-it-up suggestions.

Cascade `recipes` in chains.yaml (§5). Ollama off → returns [] and the UI shows
a clean "N/A", never an error.
"""

from __future__ import annotations

from expirationradar.models import PantryItem, RecipeSuggestion


def suggest(items: list[PantryItem], limit: int = 3) -> list[RecipeSuggestion]:
    raise NotImplementedError("Phase 3 — see plan §6")
