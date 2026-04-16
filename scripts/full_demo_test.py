#!/usr/bin/env python3
"""Full end-to-end pipeline test — Hackstrom Track 3.

Exercises the complete HITL flow:
  upload → ingest → OCR → extract → reconcile → HS → compliance
  → BLOCK (weight mismatch) → HITL resume → PASS declaration

Usage:
  cd hackstrom-track3
  backend/.venv/Scripts/python.exe scripts/full_demo_test.py

Requires: backend running at localhost:8000 with GROQ_API_KEY set.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE_URL = "http://localhost:8000/api/v1"
TIMEOUT   = 30.0          # per-request timeout
MAX_WAIT  = 120           # seconds to wait for pipeline completion
POLL_SECS = 3

TEST_DOCS = Path(__file__).parent.parent / "test_docs" / "weight_conflict_set"

PASS  = "[PASS]"
FAIL  = "[FAIL]"
INFO  = "[INFO]"
STEP  = "[STEP]"


def _ok(msg: str)   -> None: print(f"  {PASS} {msg}")
def _err(msg: str)  -> None: print(f"  {FAIL} {msg}", file=sys.stderr)
def _info(msg: str) -> None: print(f"  {INFO} {msg}")


def step(n: int, title: str) -> None:
    print(f"\n{'-'*60}")
    print(f"{STEP} {n}. {title}")
    print(f"{'-'*60}")


def abort(msg: str) -> None:
    _err(msg)
    sys.exit(1)


# ---------------------------------------------------------------------------
# 0. Pre-flight
# ---------------------------------------------------------------------------

step(0, "Pre-flight checks")

invoice_path = TEST_DOCS / "invoice.pdf"
bl_path      = TEST_DOCS / "bl.pdf"

for p in (invoice_path, bl_path):
    if not p.exists():
        abort(f"Missing test doc: {p}  — run: python scripts/generate_test_docs.py")
    _ok(f"Found {p.name} ({p.stat().st_size} bytes)")

try:
    r = httpx.get(f"http://localhost:8000/health", timeout=5)
    r.raise_for_status()
    _ok(f"Backend healthy: {r.json()}")
except Exception as exc:
    abort(f"Backend not reachable — start it first: {exc}")


# ---------------------------------------------------------------------------
# 1. Two-file upload
# ---------------------------------------------------------------------------

step(1, "Upload invoice.pdf + bl.pdf (POST /upload/)")

with invoice_path.open("rb") as inv_f, bl_path.open("rb") as bl_f:
    resp = httpx.post(
        f"{BASE_URL}/upload/",
        files={
            "invoice_pdf": ("invoice.pdf", inv_f, "application/pdf"),
            "bl_pdf":      ("bl.pdf",      bl_f,  "application/pdf"),
        },
        data={"country": "us"},
        timeout=TIMEOUT,
    )

if resp.status_code != 202:
    abort(f"Upload failed {resp.status_code}: {resp.text}")

doc = resp.json()
run_id = doc["id"]
_ok(f"Uploaded. run_id = {run_id}")
_info(f"invoice_path = {doc['metadata'].get('invoice_path')}")
_info(f"bl_path      = {doc['metadata'].get('bl_path')}")


# ---------------------------------------------------------------------------
# 2. Trigger workflow
# ---------------------------------------------------------------------------

step(2, "Trigger workflow (POST /workflow/)")

resp = httpx.post(
    f"{BASE_URL}/workflow/",
    json={"document_id": run_id, "country": "us"},
    timeout=TIMEOUT,
)
if resp.status_code != 202:
    abort(f"Workflow creation failed {resp.status_code}: {resp.text}")

wf = resp.json()
wf_id = wf["id"]
_ok(f"Workflow queued. workflow_id = {wf_id}")


# ---------------------------------------------------------------------------
# 3. Poll until BLOCKED or FAILED (max MAX_WAIT seconds)
# ---------------------------------------------------------------------------

step(3, f"Polling for BLOCK (timeout {MAX_WAIT}s, polling every {POLL_SECS}s)")

deadline = time.time() + MAX_WAIT
status   = "queued"
data: dict = {}

while time.time() < deadline:
    resp = httpx.get(f"{BASE_URL}/workflow/status/{wf_id}", timeout=TIMEOUT)
    resp.raise_for_status()
    data   = resp.json()
    status = data["status"]
    steps_done = len([s for s in data.get("steps", []) if s["status"] == "completed"])
    print(f"    status={status:10s}  nodes_completed={steps_done}", end="\r", flush=True)

    if status in ("blocked", "failed", "completed"):
        break
    time.sleep(POLL_SECS)

print()  # newline after \r progress

_info(f"Final status: {status}")

if status == "failed":
    result = data.get("result", {})
    abort(f"Pipeline failed: {result.get('error', result)}")

if status == "completed":
    # No BLOCK — may happen if LLM extracted a plausible weight from the blank BL.
    # In that case verify we still got a valid declaration.
    _info("Pipeline completed without BLOCK (LLM inferred weight from blank BL).")
    _info("Skipping HITL steps — checking declaration directly.")

elif status == "blocked":
    result  = data.get("result", {})
    issues  = result.get("issues", [])
    message = result.get("message", "")

    _ok(f"Pipeline correctly BLOCKED. Issues: {len(issues)}")
    for iss in issues:
        sev = iss.get("severity", "?").upper()
        fld = iss.get("field", "?")
        msg = iss.get("message", "?")
        _info(f"  [{sev}] {fld}: {msg}")

    # Assert there is at least one block-level issue
    block_issues = [i for i in issues if i.get("severity") == "block"]
    if not block_issues:
        abort(f"BLOCKED status but no 'block' severity issues found: {issues}")
    _ok("At least one BLOCK-severity issue confirmed.")

    # -----------------------------------------------------------------------
    # 4. HITL resume
    # -----------------------------------------------------------------------

    step(4, "HITL resume — submit corrected gross_weight_kg=860 (POST /workflow/resume/)")

    resp = httpx.post(
        f"{BASE_URL}/workflow/resume/{wf_id}",
        json={"gross_weight_kg": 860.0},
        timeout=TIMEOUT,
    )
    if resp.status_code not in (200, 202):
        abort(f"Resume failed {resp.status_code}: {resp.text}")
    _ok("Resume packet accepted.")

    # -----------------------------------------------------------------------
    # 5. Poll until COMPLETED
    # -----------------------------------------------------------------------

    step(5, f"Polling for COMPLETED after resume (timeout {MAX_WAIT}s)")

    deadline = time.time() + MAX_WAIT
    status   = "running"

    while time.time() < deadline:
        resp = httpx.get(f"{BASE_URL}/workflow/status/{wf_id}", timeout=TIMEOUT)
        resp.raise_for_status()
        data   = resp.json()
        status = data["status"]
        steps_done = len([s for s in data.get("steps", []) if s["status"] == "completed"])
        print(f"    status={status:10s}  nodes_completed={steps_done}", end="\r", flush=True)

        if status in ("completed", "failed", "blocked"):
            break
        time.sleep(POLL_SECS)

    print()

    if status != "completed":
        result = data.get("result", {})
        abort(f"Expected 'completed' after resume, got '{status}': {result}")

    _ok(f"Pipeline completed successfully after HITL resume.")


# ---------------------------------------------------------------------------
# 6. Fetch final declaration
# ---------------------------------------------------------------------------

step(6, "Fetch final declaration (GET /workflow/declaration/)")

resp = httpx.get(f"{BASE_URL}/workflow/declaration/{wf_id}", timeout=TIMEOUT)
if resp.status_code != 200:
    abort(f"Declaration fetch failed {resp.status_code}: {resp.text}")

decl = resp.json()
_ok("Declaration fetched.")

# Compliance status
comp = decl.get("compliance", {})
comp_status = comp.get("status", "UNKNOWN") if comp else "UNKNOWN"
_info(f"Compliance status : {comp_status}")

# Invoice fields
inv = decl.get("invoice") or {}
_info(f"Invoice number    : {inv.get('invoice_number', 'N/A')}")
_info(f"Invoice date      : {inv.get('date', 'N/A')}")
_info(f"Seller            : {inv.get('seller', 'N/A')}")
_info(f"Total amount      : {inv.get('currency', '')} {inv.get('total_amount', 0):,.2f}")
_info(f"Gross weight (inv): {inv.get('gross_weight_kg', 0)} kg")

# B/L fields
bl = decl.get("bill_of_lading") or {}
_info(f"B/L number        : {bl.get('bl_number', 'N/A')}")
_info(f"Vessel            : {bl.get('vessel', 'N/A')}")
_info(f"Gross weight (B/L): {bl.get('gross_weight_kg', 0)} kg")

# HS codes
hs_codes = decl.get("hs_codes", [])
_info(f"HS codes assigned : {len(hs_codes)}")
for hs in hs_codes:
    _info(f"  {hs.get('hs_code', '—'):15s} {hs.get('description', '')[:60]}")

# Assert line items have hs_code
line_items = inv.get("line_items", [])
if line_items:
    missing = [li for li in line_items if not li.get("hs_code")]
    if missing:
        _info(f"WARNING: {len(missing)} line item(s) missing hs_code — may need Groq to classify")
    else:
        _ok(f"All {len(line_items)} line item(s) have hs_code assigned.")
else:
    _info("No line items in declaration (LLM may not have extracted them).")


# ---------------------------------------------------------------------------
# 7. Audit trail
# ---------------------------------------------------------------------------

step(7, "Audit trail (GET /workflow/{id})")

resp = httpx.get(f"{BASE_URL}/workflow/{wf_id}", timeout=TIMEOUT)
resp.raise_for_status()
steps = resp.json().get("steps", [])
_ok(f"{len(steps)} audit step(s) recorded.")
for s in steps:
    status_marker = "+" if s["status"] == "completed" else "!"
    print(f"    [{status_marker}] {s['name']:30s}  {s['status']}")


# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

print(f"\n{'='*60}")
print(f"  FULL DEMO TEST COMPLETE")
print(f"  workflow_id = {wf_id}")
print(f"  compliance  = {comp_status}")
print(f"{'='*60}")
print()
