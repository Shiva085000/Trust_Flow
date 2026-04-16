"""Hackstrom Track 3 — 11-node LangGraph document-processing pipeline.

Topology:

    ingest ─► preprocess ─► ocr_extract ─┬─(conf < 0.7)─► vision_adjudication ─┐
                                          └─(conf ≥ 0.7)──────────────────────►─┤
                                                                                 ▼
                                                                          field_extract
                                                                                 │
                                                                           reconcile
                                                                                 │
                                                                          hs_retrieve
                                                                                 │
                                                                     compliance_reason
                                                                                 │
                                                                deterministic_validate
                                                                ┌────────────────┤
                                                           (BLOCK)          (no BLOCK)
                                                                ▼                ▼
                                                       interrupt_node    country_validate ◄─┘
                                                                └────────────────┘
                                                                                 │
                                                                   declaration_generate
                                                                                 │
                                                                        audit_trace ─► END

HITL note: interrupt_node raises NodeInterrupt.  Callers should catch it,
present state.compliance_result to the operator, then re-invoke with a
corrected state (or abort).  Wire a SqliteSaver / RedisSaver checkpointer in
production via compile_graph(checkpointer=...).
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml
from sqlmodel import Field as SQLField, Session, SQLModel, create_engine

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

try:
    from langgraph.errors import NodeInterrupt
except ImportError:  # very old langgraph builds
    class NodeInterrupt(Exception):  # type: ignore[misc]
        """Sentinel exception that pauses a LangGraph run for human review."""

from models import (
    AuditEvent,
    BillOfLading,
    ComplianceIssue,
    ComplianceResult,
    HSCandidate,
    InvoiceDocument,
    WorkflowState,
)
from nodes.ocr_extract import ocr_extract_node as _ocr_extract_impl
from nodes.field_extract import field_extract_node as _field_extract_impl
from nodes.hs_retrieve import hs_retrieve_node as _hs_retrieve_impl
from nodes.compliance_reason import compliance_reason_node as _compliance_reason_impl

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# SQLModel — audit persistence
# ---------------------------------------------------------------------------

class AuditEventRow(SQLModel, table=True):
    """Persistent audit record written by the audit_trace node."""

    __tablename__ = "audit_events"

    id: int | None = SQLField(default=None, primary_key=True)
    run_id: str = SQLField(index=True)
    timestamp: datetime = SQLField(default_factory=datetime.utcnow)
    node_name: str
    input_summary: str = ""
    output_summary: str = ""
    latency_ms: float = 0.0


_DB_URL = os.getenv("DATABASE_URL", "sqlite:///./hackstrom.db")
_engine = create_engine(_DB_URL, echo=False, connect_args={"check_same_thread": False})
SQLModel.metadata.create_all(_engine)


# ---------------------------------------------------------------------------
# GraphState — extends WorkflowState with runtime-only fields
# ---------------------------------------------------------------------------

class GraphState(WorkflowState):
    """Full pipeline state.

    WorkflowState carries the persisted output fields (OCR data, trade docs,
    compliance, audit trail).  The fields below are ephemeral runtime context
    that the caller provides and that nodes consume but which are not part of
    the final declaration.
    """

    # Provided by the caller before the graph starts
    document_id: str = ""
    country: str = "us"

    # Set by declaration_generate
    declaration: dict[str, Any] | None = None
    summary: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_ms() -> float:
    return time.monotonic() * 1000


def _elapsed(start_ms: float) -> float:
    return _now_ms() - start_ms


def _emit(
    state: GraphState,
    node_name: str,
    input_summary: str,
    output_summary: str,
    latency_ms: float,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Attach an AuditEvent to the state updates returned by a node."""
    event = AuditEvent(
        node_name=node_name,
        input_summary=input_summary,
        output_summary=output_summary,
        latency_ms=latency_ms,
    )
    updates["audit_trail"] = state.audit_trail + [event]
    return updates


def _recompute_status(
    issues: list[ComplianceIssue],
) -> Literal["PASS", "WARN", "BLOCK"]:
    if any(i.severity == "block" for i in issues):
        return "BLOCK"
    if any(i.severity == "warn" for i in issues):
        return "WARN"
    return "PASS"


def _merge_issues(
    current: ComplianceResult | None,
    new_issues: list[ComplianceIssue],
) -> ComplianceResult:
    """Return a new ComplianceResult with new_issues appended and status recomputed."""
    existing = current.issues if current else []
    all_issues = existing + new_issues
    return ComplianceResult(
        status=_recompute_status(all_issues),
        issues=all_issues,
    )


def _load_rules(country: str) -> dict[str, Any]:
    rules_path = (
        Path(__file__).parent.parent / "country_rules" / f"{country.lower()}.yaml"
    )
    if rules_path.exists():
        return yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    log.warning("country_rules.missing", country=country, path=str(rules_path))
    return {}


# ---------------------------------------------------------------------------
# Node 1 — ingest
# ---------------------------------------------------------------------------

async def ingest(state: GraphState) -> dict[str, Any]:
    """Assign a fresh run_id and log document receipt."""
    t0 = _now_ms()
    run_id = uuid.uuid4()
    log.info(
        "ingest.start",
        run_id=str(run_id),
        document_id=state.document_id,
        invoice_path=state.invoice_pdf_path,
        bl_path=state.bl_pdf_path,
        country=state.country,
    )
    return _emit(
        state,
        "ingest",
        input_summary=f"doc={state.document_id} country={state.country}",
        output_summary=f"run_id={run_id} assigned",
        latency_ms=_elapsed(t0),
        updates={"run_id": run_id},
    )


# ---------------------------------------------------------------------------
# Node 2 — preprocess
# ---------------------------------------------------------------------------

async def preprocess(state: GraphState) -> dict[str, Any]:
    """Pre-flight validation: confirm both file paths exist and are readable.

    Does not set needs_vision_fallback — that is determined by actual OCR
    confidence in ocr_extract_node.  A quick filename-extension check is
    logged as a hint only.
    """
    t0 = _now_ms()
    image_extensions = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}

    inv_path = state.invoice_pdf_path or ""
    bl_path  = state.bl_pdf_path  or ""

    inv_image = Path(inv_path).suffix.lower() in image_extensions if inv_path else False
    bl_image  = Path(bl_path).suffix.lower()  in image_extensions if bl_path  else False

    missing = [
        label for label, p in [("invoice", inv_path), ("bl", bl_path)]
        if not p or not Path(p).exists()
    ]
    if missing:
        log.warning("preprocess.files_missing", missing=missing)
    else:
        log.info(
            "preprocess.done",
            inv_path=inv_path,
            bl_path=bl_path,
            inv_image_hint=inv_image,
            bl_image_hint=bl_image,
        )

    return _emit(
        state,
        "preprocess",
        input_summary=f"invoice={inv_path or 'N/A'} bl={bl_path or 'N/A'}",
        output_summary=f"inv_image_hint={inv_image} bl_image_hint={bl_image} missing={missing}",
        latency_ms=_elapsed(t0),
        updates={},
    )


# ---------------------------------------------------------------------------
# Node 3 — ocr_extract
# ---------------------------------------------------------------------------

async def ocr_extract(state: GraphState) -> dict[str, Any]:
    """Run Docling OCR on both uploaded documents.

    Delegates to nodes.ocr_extract.ocr_extract_node (sync, CPU-bound) via
    run_in_executor so the event loop is not blocked.
    """
    t0 = _now_ms()

    loop = asyncio.get_running_loop()
    updated: WorkflowState = await loop.run_in_executor(
        None, _ocr_extract_impl, state
    )

    inv_chars = len(updated.invoice_ocr_text or "")
    bl_chars  = len(updated.bl_ocr_text  or "")
    inv_bbox  = len(updated.invoice_bboxes or [])
    bl_bbox   = len(updated.bl_bboxes   or [])

    return _emit(
        state,
        "ocr_extract",
        input_summary=(
            f"invoice={state.invoice_pdf_path or 'N/A'} "
            f"bl={state.bl_pdf_path or 'N/A'}"
        ),
        output_summary=(
            f"inv_chars={inv_chars} bl_chars={bl_chars} "
            f"inv_bboxes={inv_bbox} bl_bboxes={bl_bbox} "
            f"confidence={updated.ocr_confidence:.3f} "
            f"fallback={updated.needs_vision_fallback}"
        ),
        latency_ms=_elapsed(t0),
        updates={
            "invoice_ocr_text":    updated.invoice_ocr_text,
            "bl_ocr_text":         updated.bl_ocr_text,
            "invoice_tables":      updated.invoice_tables,
            "bl_tables":           updated.bl_tables,
            "invoice_bboxes":      updated.invoice_bboxes,
            "bl_bboxes":           updated.bl_bboxes,
            "ocr_confidence":      updated.ocr_confidence,
            "needs_vision_fallback": updated.needs_vision_fallback,
        },
    )


# ---------------------------------------------------------------------------
# Node 4 (conditional) — vision_adjudication
# ---------------------------------------------------------------------------

async def vision_adjudication(state: GraphState) -> dict[str, Any]:
    """Re-process a low-confidence scan with a multimodal vision model.

    Stub: improves confidence and appends a correction notice.
    TODO: call GPT-4o / Claude vision with the page image bytes.
    """
    t0 = _now_ms()
    log.warning(
        "vision_adjudication.triggered",
        original_confidence=state.ocr_confidence,
    )

    # TODO: encode page images as base64, send to GPT-4o / Claude vision,
    # parse structured markdown response, re-extract bboxes from the response.
    correction_tag = "\n[vision-adjudicated: low-confidence scan corrected]"
    improved_invoice_text = (state.invoice_ocr_text or "") + correction_tag
    improved_bl_text      = (state.bl_ocr_text      or "") + correction_tag
    improved_confidence   = 0.82

    log.info("vision_adjudication.done", new_confidence=improved_confidence)
    return _emit(
        state,
        "vision_adjudication",
        input_summary=f"confidence={state.ocr_confidence:.3f}",
        output_summary=f"new_confidence={improved_confidence}",
        latency_ms=_elapsed(t0),
        updates={
            "invoice_ocr_text":      improved_invoice_text,
            "bl_ocr_text":           improved_bl_text,
            "ocr_confidence":        improved_confidence,
            "needs_vision_fallback": False,   # resolved by this node
        },
    )


# ---------------------------------------------------------------------------
# Node 5 — field_extract
# ---------------------------------------------------------------------------

async def field_extract(state: GraphState) -> dict[str, Any]:
    """Extract structured InvoiceDocument and BillOfLading from raw OCR text.

    Delegates to nodes/field_extract.py which uses Instructor + Groq or a
    local vLLM server.  Run in a thread executor so the blocking I/O call
    does not stall the event loop.
    """
    t0 = _now_ms()
    inv_chars = len(state.invoice_ocr_text or "")
    bl_chars  = len(state.bl_ocr_text  or "")

    loop    = asyncio.get_event_loop()
    updated: GraphState = await loop.run_in_executor(None, _field_extract_impl, state)

    invoice = updated.invoice
    bl      = updated.bill_of_lading

    return _emit(
        state,
        "field_extract",
        input_summary=f"inv_chars={inv_chars} bl_chars={bl_chars}",
        output_summary=(
            f"invoice={invoice.invoice_number if invoice else 'none'} "
            f"bl={bl.bl_number if bl else 'none'}"
        ),
        latency_ms=_elapsed(t0),
        updates={"invoice": invoice, "bill_of_lading": bl},
    )


# ---------------------------------------------------------------------------
# Node 6 — reconcile
# ---------------------------------------------------------------------------

async def reconcile(state: GraphState) -> dict[str, Any]:
    """Cross-check invoice vs B/L.  Adds WARN when gross weights diverge > 5 %."""
    t0 = _now_ms()
    new_issues: list[ComplianceIssue] = []

    inv = state.invoice
    bl = state.bill_of_lading

    if inv and bl and inv.gross_weight_kg > 0 and bl.gross_weight_kg > 0:
        diff_pct = (
            abs(inv.gross_weight_kg - bl.gross_weight_kg) / bl.gross_weight_kg * 100
        )
        if diff_pct > 5.0:
            new_issues.append(
                ComplianceIssue(
                    field="gross_weight_kg",
                    message=(
                        f"Invoice weight ({inv.gross_weight_kg} kg) differs from "
                        f"B/L weight ({bl.gross_weight_kg} kg) by {diff_pct:.1f}% "
                        f"(threshold 5%)"
                    ),
                    severity="block",
                )
            )
            log.warning("reconcile.weight_mismatch", diff_pct=diff_pct)

    compliance = _merge_issues(state.compliance_result, new_issues)
    summary = (
        f"weight_diff_ok" if not new_issues else f"weight_warn diff={diff_pct:.1f}%"
    )
    return _emit(
        state,
        "reconcile",
        input_summary=(
            f"inv_weight={inv.gross_weight_kg if inv else 'N/A'} "
            f"bl_weight={bl.gross_weight_kg if bl else 'N/A'}"
        ),
        output_summary=summary,
        latency_ms=_elapsed(t0),
        updates={"compliance_result": compliance},
    )


# ---------------------------------------------------------------------------
# Node 7 — hs_retrieve
# ---------------------------------------------------------------------------

async def hs_retrieve(state: GraphState) -> dict[str, Any]:
    """Query USITC HTS API for HS code candidates for each invoice line item.

    Delegates to nodes/hs_retrieve.py which tries the live USITC REST API and
    silently falls back to the bundled sample data when offline.
    """
    t0 = _now_ms()

    if not state.invoice:
        return _emit(
            state,
            "hs_retrieve",
            input_summary="invoice=None",
            output_summary="skipped",
            latency_ms=_elapsed(t0),
            updates={},
        )

    n_items = len(state.invoice.line_items)
    await _hs_retrieve_impl(state)   # mutates state.invoice.line_items in-place

    total_candidates = sum(len(item.hs_candidates) for item in state.invoice.line_items)
    log.info("hs_retrieve.done", items=n_items, total_candidates=total_candidates)
    return _emit(
        state,
        "hs_retrieve",
        input_summary=f"line_items={n_items}",
        output_summary=f"items={n_items} total_candidates={total_candidates}",
        latency_ms=_elapsed(t0),
        updates={"invoice": state.invoice},
    )


# ---------------------------------------------------------------------------
# Node 8 — compliance_reason
# ---------------------------------------------------------------------------

async def compliance_reason(state: GraphState) -> dict[str, Any]:
    """LLM picks the best HS code for each line item and writes a rationale.

    Delegates to nodes/compliance_reason.py (sync Instructor call) via
    run_in_executor.  After the call, any line items flagged for review
    (confidence < 0.5 or code not in candidates) are added as WARN compliance
    issues so the downstream validate node can surface them.
    """
    t0 = _now_ms()

    if not state.invoice:
        return _emit(
            state,
            "compliance_reason",
            input_summary="invoice=None",
            output_summary="skipped",
            latency_ms=_elapsed(t0),
            updates={},
        )

    n_items = len(state.invoice.line_items)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _compliance_reason_impl, state)

    # ── Surface flag_for_review items as WARN compliance issues ──────────────
    selections = state.__dict__.pop("_hs_selections", [])
    new_issues: list[ComplianceIssue] = []
    for sel in selections:
        if sel.flag_for_review:
            idx = sel.line_item_index
            desc = (
                state.invoice.line_items[idx].description[:60]
                if idx < n_items
                else f"index {idx}"
            )
            new_issues.append(
                ComplianceIssue(
                    field=f"line_items.{idx}.hs_code",
                    message=(
                        f"HS code '{sel.selected_code}' flagged for review: "
                        f"{sel.rationale[:120]}"
                    ),
                    severity="warn",
                )
            )
            log.warning(
                "compliance_reason.flagged",
                index=idx,
                description=desc,
                code=sel.selected_code,
                confidence=sel.confidence,
            )

    chosen = [item.hs_code for item in state.invoice.line_items if item.hs_code]
    compliance = _merge_issues(state.compliance_result, new_issues)
    log.info("compliance_reason.done", codes_assigned=chosen, flags=len(new_issues))
    return _emit(
        state,
        "compliance_reason",
        input_summary=f"items={n_items}",
        output_summary=f"codes_assigned={chosen} flags={len(new_issues)}",
        latency_ms=_elapsed(t0),
        updates={"invoice": state.invoice, "compliance_result": compliance},
    )


# ---------------------------------------------------------------------------
# Node 9 — deterministic_validate
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


async def deterministic_validate(state: GraphState) -> dict[str, Any]:
    """Pure-Python checks: required fields, date format, positive weight.

    Issues with severity='block' trigger the interrupt_node branch.
    """
    t0 = _now_ms()
    new_issues: list[ComplianceIssue] = []

    inv = state.invoice
    bl = state.bill_of_lading

    # ── Invoice checks ────────────────────────────────────────────────────────
    if inv is None:
        new_issues.append(
            ComplianceIssue(
                field="invoice",
                message="No invoice document was extracted",
                severity="block",
            )
        )
    else:
        if not inv.invoice_number.strip():
            new_issues.append(
                ComplianceIssue(
                    field="invoice.invoice_number",
                    message="Invoice number is empty",
                    severity="block",
                )
            )
        if not _ISO_DATE_RE.match(inv.date):
            new_issues.append(
                ComplianceIssue(
                    field="invoice.date",
                    message=f"Date '{inv.date}' is not ISO-8601 (YYYY-MM-DD)",
                    severity="block",
                )
            )
        if not inv.seller.strip():
            new_issues.append(
                ComplianceIssue(
                    field="invoice.seller",
                    message="Seller name is empty",
                    severity="block",
                )
            )
        if not inv.buyer.strip():
            new_issues.append(
                ComplianceIssue(
                    field="invoice.buyer",
                    message="Buyer name is empty",
                    severity="block",
                )
            )
        if inv.gross_weight_kg <= 0:
            new_issues.append(
                ComplianceIssue(
                    field="invoice.gross_weight_kg",
                    message="Gross weight must be greater than zero",
                    severity="block",
                )
            )
        if inv.total_amount <= 0:
            new_issues.append(
                ComplianceIssue(
                    field="invoice.total_amount",
                    message="Total amount must be greater than zero",
                    severity="warn",
                )
            )

    # ── B/L checks ───────────────────────────────────────────────────────────
    if bl is None:
        new_issues.append(
            ComplianceIssue(
                field="bill_of_lading",
                message="No bill of lading was extracted",
                severity="block",
            )
        )
    else:
        if not bl.bl_number.strip():
            new_issues.append(
                ComplianceIssue(
                    field="bill_of_lading.bl_number",
                    message="B/L number is empty",
                    severity="block",
                )
            )
        if not bl.vessel.strip():
            new_issues.append(
                ComplianceIssue(
                    field="bill_of_lading.vessel",
                    message="Vessel name is empty",
                    severity="warn",
                )
            )
        if bl.gross_weight_kg <= 0:
            new_issues.append(
                ComplianceIssue(
                    field="bill_of_lading.gross_weight_kg",
                    message="B/L gross weight must be greater than zero",
                    severity="block",
                )
            )

    compliance = _merge_issues(state.compliance_result, new_issues)
    block_count = sum(1 for i in new_issues if i.severity == "block")
    warn_count = sum(1 for i in new_issues if i.severity == "warn")
    log.info(
        "deterministic_validate.done",
        status=compliance.status,
        new_blocks=block_count,
        new_warns=warn_count,
    )
    return _emit(
        state,
        "deterministic_validate",
        input_summary=f"inv={'ok' if inv else 'None'} bl={'ok' if bl else 'None'}",
        output_summary=f"status={compliance.status} blocks={block_count} warns={warn_count}",
        latency_ms=_elapsed(t0),
        updates={"compliance_result": compliance},
    )


# ---------------------------------------------------------------------------
# Node 10 — interrupt_node (HITL pause)
# ---------------------------------------------------------------------------

async def interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause execution for human-in-the-loop review of BLOCK-level findings.

    Raises NodeInterrupt so the caller can inspect state.compliance_result,
    resolve issues, and re-invoke the graph.

    Production pattern:
        try:
            result = await graph.ainvoke(state, config)
        except NodeInterrupt as e:
            # present e.args[0] (message) + state to operator dashboard
            # operator corrects, then:
            result = await graph.ainvoke(corrected_state, config)
    """
    blocks = [
        i
        for i in (state.compliance_result.issues if state.compliance_result else [])
        if i.severity == "block"
    ]
    if not blocks:
        # All block issues resolved — continue to country_validate.
        log.info("interrupt_node.no_blocks", run_id=str(state.run_id))
        return {}
    msg = (
        f"HITL interrupt: {len(blocks)} BLOCK-level issue(s) require human review. "
        f"run_id={state.run_id}. "
        f"Issues: {[i.field + ': ' + i.message for i in blocks]}"
    )
    log.warning("interrupt_node.raised", run_id=str(state.run_id), block_count=len(blocks))
    raise NodeInterrupt(msg)


# ---------------------------------------------------------------------------
# Node 11 — country_validate
# ---------------------------------------------------------------------------

async def country_validate(state: GraphState) -> dict[str, Any]:
    """Load country_rules/<country>.yaml and apply jurisdiction-specific checks."""
    t0 = _now_ms()
    rules = _load_rules(state.country)
    new_issues: list[ComplianceIssue] = []
    inv = state.invoice

    if not rules:
        compliance = _merge_issues(state.compliance_result, [])
        return _emit(
            state,
            "country_validate",
            input_summary=f"country={state.country}",
            output_summary="no rules file found, skipped",
            latency_ms=_elapsed(t0),
            updates={"compliance_result": compliance},
        )

    # ── Seller / buyer name required ─────────────────────────────────────────
    legal_name_required = "legal_name" in (rules.get("required_fields") or [])
    if legal_name_required and inv:
        if not inv.seller.strip():
            new_issues.append(
                ComplianceIssue(
                    field="invoice.seller",
                    message=f"[{state.country.upper()}] Legal name (seller) is required",
                    severity="block",
                )
            )
        if not inv.buyer.strip():
            new_issues.append(
                ComplianceIssue(
                    field="invoice.buyer",
                    message=f"[{state.country.upper()}] Legal name (buyer) is required",
                    severity="block",
                )
            )

    # ── Date format ───────────────────────────────────────────────────────────
    date_fmt_rule = (rules.get("field_formats") or {}).get("date_of_birth", {})
    date_pattern = date_fmt_rule.get("pattern", "")
    if date_pattern and inv and not re.match(date_pattern, inv.date):
        new_issues.append(
            ComplianceIssue(
                field="invoice.date",
                message=(
                    f"[{state.country.upper()}] Date '{inv.date}' does not match "
                    f"required pattern '{date_pattern}'"
                ),
                severity="warn",
            )
        )

    # ── Compliance screening flags ────────────────────────────────────────────
    compliance_cfg = rules.get("compliance") or {}
    screening_labels = {
        "kyc_required": "KYC",
        "aml_check": "AML",
        "ofac_screening": "OFAC",
        "cbuae_screening": "CBUAE",
    }
    for key, label in screening_labels.items():
        if compliance_cfg.get(key):
            new_issues.append(
                ComplianceIssue(
                    field="compliance",
                    message=f"[{state.country.upper()}] {label} screening required per jurisdiction rules",
                    severity="warn",
                )
            )

    compliance = _merge_issues(state.compliance_result, new_issues)
    log.info(
        "country_validate.done",
        country=state.country,
        status=compliance.status,
        new_issues=len(new_issues),
    )
    return _emit(
        state,
        "country_validate",
        input_summary=f"country={state.country} inv={'ok' if inv else 'None'}",
        output_summary=f"status={compliance.status} new_issues={len(new_issues)}",
        latency_ms=_elapsed(t0),
        updates={"compliance_result": compliance},
    )


# ---------------------------------------------------------------------------
# Node 12 — declaration_generate
# ---------------------------------------------------------------------------

async def declaration_generate(state: GraphState) -> dict[str, Any]:
    """Build the final customs declaration JSON and a human-readable summary."""
    t0 = _now_ms()
    inv = state.invoice
    bl = state.bill_of_lading
    cr = state.compliance_result

    declaration: dict[str, Any] = {
        "run_id": str(state.run_id),
        "generated_at": datetime.utcnow().isoformat(),
        "jurisdiction": state.country.upper(),
        "invoice": inv.model_dump() if inv else None,
        "bill_of_lading": bl.model_dump() if bl else None,
        "compliance": cr.model_dump() if cr else None,
        "hs_codes": (
            [
                {"description": li.description, "hs_code": li.hs_code}
                for li in (inv.line_items if inv else [])
            ]
        ),
    }

    status = cr.status if cr else "UNKNOWN"
    issue_count = len(cr.issues) if cr else 0
    hs_assigned = sum(1 for li in (inv.line_items if inv else []) if li.hs_code)

    summary = (
        f"Declaration {state.run_id} | {state.country.upper()} | "
        f"Status: {status} | Issues: {issue_count} | "
        f"HS codes assigned: {hs_assigned}/{len(inv.line_items) if inv else 0} | "
        f"Vessel: {bl.vessel if bl else 'N/A'} | "
        f"Total: {inv.currency if inv else ''} {inv.total_amount if inv else 0:,.2f}"
    )

    log.info(
        "declaration_generate.done",
        status=status,
        hs_assigned=hs_assigned,
    )
    return _emit(
        state,
        "declaration_generate",
        input_summary=f"status={status} issues={issue_count}",
        output_summary=summary,
        latency_ms=_elapsed(t0),
        updates={"declaration": declaration, "summary": summary},
    )


# ---------------------------------------------------------------------------
# Node 13 — audit_trace
# ---------------------------------------------------------------------------

async def audit_trace(state: GraphState) -> dict[str, Any]:
    """Persist every AuditEvent collected during the run to SQLite."""
    t0 = _now_ms()
    run_id_str = str(state.run_id)

    rows = [
        AuditEventRow(
            run_id=run_id_str,
            timestamp=event.timestamp,
            node_name=event.node_name,
            input_summary=event.input_summary,
            output_summary=event.output_summary,
            latency_ms=event.latency_ms,
        )
        for event in state.audit_trail
    ]

    with Session(_engine) as session:
        session.add_all(rows)
        session.commit()

    log.info("audit_trace.done", run_id=run_id_str, events_persisted=len(rows))
    return _emit(
        state,
        "audit_trace",
        input_summary=f"events={len(state.audit_trail)}",
        output_summary=f"persisted={len(rows)} rows to {_DB_URL}",
        latency_ms=_elapsed(t0),
        updates={},
    )


# ---------------------------------------------------------------------------
# Routing functions (conditional edges)
# ---------------------------------------------------------------------------

def _route_ocr(state: GraphState) -> str:
    """After ocr_extract: needs_vision_fallback → vision_adjudication, else → field_extract."""
    if state.needs_vision_fallback:
        log.info("routing.vision_fallback", confidence=state.ocr_confidence)
        return "vision_adjudication"
    return "field_extract"


def _route_deterministic(state: GraphState) -> str:
    """After deterministic_validate: any BLOCK → interrupt_node, else → country_validate."""
    cr = state.compliance_result
    if cr and any(i.severity == "block" for i in cr.issues):
        log.warning("routing.block_found", issues=[i.field for i in cr.issues if i.severity == "block"])
        return "interrupt_node"
    return "country_validate"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def compile_graph(**compile_kwargs: Any) -> Any:
    """Build and compile the 11-node document-processing StateGraph.

    Args:
        **compile_kwargs: forwarded directly to builder.compile().
            Pass checkpointer=SqliteSaver(...) in production to enable
            durable HITL resume after interrupt_node.

    Returns:
        A compiled LangGraph runnable (supports .invoke / .ainvoke).

    Example:
        graph = compile_graph()
        try:
            result = await graph.ainvoke(
                GraphState(document_id="abc", invoice_pdf_path="/tmp/inv.pdf", bl_pdf_path="/tmp/bl.pdf", country="us")
            )
        except NodeInterrupt as e:
            # present to operator, then re-invoke with corrected state
    """
    builder: StateGraph = StateGraph(GraphState)

    # ── Register nodes ───────────────────────────────────────────────────────
    builder.add_node("ingest",                  ingest)
    builder.add_node("preprocess",              preprocess)
    builder.add_node("ocr_extract",             ocr_extract)
    builder.add_node("vision_adjudication",     vision_adjudication)
    builder.add_node("field_extract",           field_extract)
    builder.add_node("reconcile",               reconcile)
    builder.add_node("hs_retrieve",             hs_retrieve)
    builder.add_node("compliance_reason",       compliance_reason)
    builder.add_node("deterministic_validate",  deterministic_validate)
    builder.add_node("interrupt_node",          interrupt_node)
    builder.add_node("country_validate",        country_validate)
    builder.add_node("declaration_generate",    declaration_generate)
    builder.add_node("audit_trace",             audit_trace)

    # ── Entry point ──────────────────────────────────────────────────────────
    builder.set_entry_point("ingest")

    # ── Linear backbone ──────────────────────────────────────────────────────
    builder.add_edge("ingest",            "preprocess")
    builder.add_edge("preprocess",        "ocr_extract")
    # ocr_extract → conditional (see below)
    builder.add_edge("vision_adjudication", "field_extract")
    builder.add_edge("field_extract",     "reconcile")
    builder.add_edge("reconcile",         "hs_retrieve")
    builder.add_edge("hs_retrieve",       "compliance_reason")
    builder.add_edge("compliance_reason", "deterministic_validate")
    # deterministic_validate → conditional (see below)
    builder.add_edge("interrupt_node",    "country_validate")
    builder.add_edge("country_validate",  "declaration_generate")
    builder.add_edge("declaration_generate", "audit_trace")
    builder.add_edge("audit_trace",       END)

    # ── Conditional edges ─────────────────────────────────────────────────────
    builder.add_conditional_edges(
        "ocr_extract",
        _route_ocr,
        {"vision_adjudication": "vision_adjudication", "field_extract": "field_extract"},
    )
    builder.add_conditional_edges(
        "deterministic_validate",
        _route_deterministic,
        {"interrupt_node": "interrupt_node", "country_validate": "country_validate"},
    )

    if "checkpointer" not in compile_kwargs:
        compile_kwargs["checkpointer"] = MemorySaver()
    return builder.compile(**compile_kwargs)


# ---------------------------------------------------------------------------
# Module-level instance — imported by routes/workflow.py
# ---------------------------------------------------------------------------
document_graph = compile_graph()
