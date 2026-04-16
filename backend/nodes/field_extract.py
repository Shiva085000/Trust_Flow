"""nodes/field_extract.py — LLM-based structured extraction via Instructor.

Uses Groq (llama-3.3-70b) when GROQ_API_KEY is set, otherwise falls back to a
locally-hosted vLLM server (Qwen2.5-7B-Instruct at http://localhost:8000/v1).

The Instructor library wraps the OpenAI-compatible chat endpoint and retries
automatically until the response validates against the Pydantic response_model
(up to max_retries attempts).
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

import instructor
import structlog

from models import BillOfLading, InvoiceDocument, WorkflowState

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROQ_MODEL_DEFAULT  = "llama-3.3-70b-versatile"
VLLM_MODEL_DEFAULT  = "Qwen/Qwen2.5-7B-Instruct"
VLLM_BASE_URL_DEFAULT = "http://localhost:8001/v1"

# ---------------------------------------------------------------------------
# Client factory (cached — one client per process)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_client() -> Any:
    """Return a patched Instructor client.

    Priority:
      1. GROQ_API_KEY set → Groq cloud (llama-3.3-70b-versatile)
      2. Otherwise        → local vLLM (OpenAI-compatible endpoint)
    """
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        from groq import Groq  # noqa: PLC0415

        log.info("field_extract.client", backend="groq", model=os.getenv("GROQ_MODEL", GROQ_MODEL_DEFAULT))
        return instructor.from_groq(Groq(api_key=groq_key), mode=instructor.Mode.JSON)

    # Local vLLM fallback
    from openai import OpenAI  # noqa: PLC0415

    base_url = os.getenv("VLLM_BASE_URL", VLLM_BASE_URL_DEFAULT)
    log.info("field_extract.client", backend="vllm", base_url=base_url, model=os.getenv("VLLM_MODEL", VLLM_MODEL_DEFAULT))
    return instructor.from_openai(
        OpenAI(base_url=base_url, api_key="local"),
        mode=instructor.Mode.JSON,
    )


def _active_model() -> str:
    """Return the model string appropriate for the active backend."""
    if os.getenv("GROQ_API_KEY", "").strip():
        return os.getenv("GROQ_MODEL", GROQ_MODEL_DEFAULT)
    return os.getenv("VLLM_MODEL", VLLM_MODEL_DEFAULT)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

INVOICE_PROMPT = """\
You are a customs document extraction specialist.

Extract all fields from the commercial invoice text below into the exact JSON \
schema provided. Rules:
- Dates must be in ISO-8601 format (YYYY-MM-DD). If the day is missing, use 01.
- Monetary amounts are plain floats (no currency symbols).
- If a field is absent from the document, use the schema default.
- For line_items, extract every row; do not skip or merge rows.
- hs_code and hs_candidates may be left empty — they are filled by a later node.

Invoice text:
{text}

Tables detected (list-of-dicts, one entry per table row):
{tables}
"""

BL_PROMPT = """\
You are a customs document extraction specialist.

Extract all fields from the Bill of Lading text below into the exact JSON \
schema provided. Rules:
- Gross weight must be in kilograms. Convert if the document uses lbs (÷ 2.205).
- If a field is absent from the document, use the schema default.
- For line_items, extract every cargo description row.
- hs_code and hs_candidates may be left empty.

Bill of Lading text:
{text}

Tables detected:
{tables}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_tables(tables: list[Any] | None) -> str:
    """Render extracted tables as a compact JSON string for the prompt."""
    if not tables:
        return "[]"
    try:
        return json.dumps(tables, ensure_ascii=False, indent=None)
    except (TypeError, ValueError):
        return str(tables)


# ---------------------------------------------------------------------------
# Public node function  (synchronous — called via run_in_executor in graph.py)
# ---------------------------------------------------------------------------


def field_extract_node(state: WorkflowState) -> WorkflowState:
    """Call the LLM twice (invoice + B/L) and populate state.invoice / state.bill_of_lading.

    This is intentionally *synchronous* because Instructor's sync client is
    simpler to test and because graph.py already wraps every sync node with
    asyncio.get_event_loop().run_in_executor(), keeping the event loop free.

    Raises:
        instructor.exceptions.InstructorRetryException: when the LLM fails to
            produce a valid schema after max_retries attempts. graph.py catches
            this and marks the run as failed.
    """
    client = get_client()
    model  = _active_model()
    max_retries = 3

    # ── Invoice ──────────────────────────────────────────────────────────────
    inv_text   = state.invoice_ocr_text or ""
    inv_tables = _format_tables(state.invoice_tables)

    log.info(
        "field_extract.invoice_start",
        model=model,
        text_chars=len(inv_text),
    )

    invoice: InvoiceDocument = client.chat.completions.create(
        model=model,
        response_model=InvoiceDocument,
        messages=[
            {
                "role": "user",
                "content": INVOICE_PROMPT.format(text=inv_text, tables=inv_tables),
            }
        ],
        max_retries=max_retries,
    )

    log.info(
        "field_extract.invoice_done",
        invoice_number=invoice.invoice_number,
        line_items=len(invoice.line_items),
        total_amount=invoice.total_amount,
    )

    # ── Bill of Lading ────────────────────────────────────────────────────────
    bl_text   = state.bl_ocr_text or ""
    bl_tables = _format_tables(state.bl_tables)

    log.info(
        "field_extract.bl_start",
        model=model,
        text_chars=len(bl_text),
    )

    bill_of_lading: BillOfLading = client.chat.completions.create(
        model=model,
        response_model=BillOfLading,
        messages=[
            {
                "role": "user",
                "content": BL_PROMPT.format(text=bl_text, tables=bl_tables),
            }
        ],
        max_retries=max_retries,
    )

    log.info(
        "field_extract.bl_done",
        bl_number=bill_of_lading.bl_number,
        line_items=len(bill_of_lading.line_items),
    )

    state.invoice        = invoice
    state.bill_of_lading = bill_of_lading
    return state
