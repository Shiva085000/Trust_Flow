"""nodes/hs_retrieve.py — USITC HTS REST API lookup with local fallback.

Live path:
    GET https://hts.usitc.gov/reststop/exportProducts
    ?searchTerm=<description>&offset=0&limit=<top_k>
    → {"HTSProductInfo": [{"htsno": "8471.30.00.00", "description": "..."}]}

Fallback (demo / offline mode):
    When the API is unreachable (timeout, network error, 4xx/5xx) the node
    searches the bundled /data/hs_codes_sample.json via simple keyword overlap
    scoring so the pipeline can complete without a live internet connection.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import httpx
import structlog

from models import HSCandidate, WorkflowState

log = structlog.get_logger(__name__)

USITC_BASE = "https://hts.usitc.gov/reststop"

# Path is relative to this file: backend/nodes/hs_retrieve.py → backend/data/
_SAMPLE_PATH = Path(__file__).parent.parent / "data" / "hs_codes_sample.json"


# ---------------------------------------------------------------------------
# Fallback helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_sample_data() -> list[dict[str, str]]:
    """Load the bundled sample data once per process and cache it."""
    try:
        with _SAMPLE_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        log.info("hs_retrieve.sample_loaded", entries=len(data))
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("hs_retrieve.sample_load_failed", error=str(exc), path=str(_SAMPLE_PATH))
        return []


def _tokenize(text: str) -> set[str]:
    """Lowercase, strip punctuation, return word set."""
    return set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def _fallback_search(description: str, top_k: int) -> list[HSCandidate]:
    """Keyword-overlap search against the local sample data.

    Each sample entry is scored by how many tokens from *description* appear
    in its own description.  Entries with zero overlap are excluded.
    """
    query_tokens = _tokenize(description)
    if not query_tokens:
        return []

    scored: list[tuple[int, dict[str, str]]] = []
    for entry in _load_sample_data():
        overlap = len(query_tokens & _tokenize(entry.get("description", "")))
        if overlap:
            scored.append((overlap, entry))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        HSCandidate(
            code=entry.get("code", ""),
            description=entry.get("description", ""),
            confidence=0.0,   # scored by compliance_reason node
            rationale=None,
        )
        for _, entry in scored[:top_k]
    ]


# ---------------------------------------------------------------------------
# USITC API helper
# ---------------------------------------------------------------------------


async def search_hs_codes(description: str, top_k: int = 5) -> list[HSCandidate]:
    """Return up to *top_k* HS code candidates for *description*.

    Queries the live USITC HTS REST API first.  On any transport/HTTP error,
    or when the API returns an empty result set, silently falls back to the
    bundled sample data so the pipeline continues in demo mode.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{USITC_BASE}/exportProducts",
                params={
                    "searchTerm": description,
                    "offset": 0,
                    "limit": top_k,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        candidates: list[HSCandidate] = []
        for item in data.get("HTSProductInfo", [])[:top_k]:
            raw_code = item.get("htsno", "")
            candidates.append(
                HSCandidate(
                    # USITC returns codes like "8471.30.00.00" — strip dots for
                    # uniform 10-digit storage; other nodes can re-format.
                    code=raw_code.replace(".", ""),
                    description=item.get("description", ""),
                    confidence=0.0,
                    rationale=None,
                )
            )

        if candidates:
            log.info(
                "hs_retrieve.api_ok",
                description=description[:60],
                hits=len(candidates),
            )
            return candidates

        # API responded but result list was empty — fall through to local data.
        log.info("hs_retrieve.api_empty", description=description[:60])

    except (httpx.HTTPError, httpx.TimeoutException, Exception) as exc:
        log.warning(
            "hs_retrieve.api_failed",
            description=description[:60],
            error=str(exc),
        )

    # Offline / empty API → local fallback
    fallback = _fallback_search(description, top_k)
    log.info(
        "hs_retrieve.fallback_used",
        description=description[:60],
        hits=len(fallback),
    )
    return fallback


# ---------------------------------------------------------------------------
# Public node function  (async — called directly by the graph wrapper)
# ---------------------------------------------------------------------------


async def hs_retrieve_node(state: WorkflowState) -> WorkflowState:
    """Populate hs_candidates for every invoice line item.

    Fires one USITC API request (or fallback search) per line item.
    Requests are issued sequentially to avoid hammering the public API.
    Results are written directly onto each LineItem object; the node returns
    the same state instance for the graph wrapper to diff against.
    """
    if not state.invoice or not state.invoice.line_items:
        log.info("hs_retrieve.skipped", reason="no_invoice_or_line_items")
        return state

    for item in state.invoice.line_items:
        candidates = await search_hs_codes(item.description, top_k=5)
        item.hs_candidates = candidates
        log.debug(
            "hs_retrieve.item_done",
            description=item.description[:60],
            candidates=len(candidates),
        )

    return state
