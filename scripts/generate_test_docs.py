#!/usr/bin/env python3
"""Generate minimal test PDFs for the smoke test.

Creates test_docs/weight_conflict_set/:
    invoice.pdf  — valid commercial invoice, gross_weight_kg = 850
    bl.pdf       — bill of lading with gross_weight_kg = 0
                   (intentionally blank → triggers BLOCK in deterministic_validate)

Requires: pymupdf  (already in backend/requirements.txt)
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import fitz  # pymupdf
except ImportError:
    print("ERROR: pymupdf not installed.  Run:  pip install pymupdf")
    sys.exit(1)

DEST_DIR = Path(__file__).parent.parent / "test_docs" / "weight_conflict_set"
DEST_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Document text content
# ---------------------------------------------------------------------------

INVOICE_TEXT = """\
COMMERCIAL INVOICE

Invoice Number : INV-TEST-2024-001
Date           : 2024-03-15
Country of Origin: United States of America

Seller : Test Exports LLC
         123 Industrial Way, Houston TX 77001, USA
         Tel: +1 713 555 0100

Buyer  : Demo Trading FZE
         Jebel Ali Free Zone (JAFZA), Dubai, UAE

LINE ITEMS
----------
No  Description                         Qty    Unit Price    Amount
1   Industrial Centrifugal Pump CP-500  10     USD 4,500.00  USD 45,000.00
    HS Code: (pending classification)
    Country of Origin: USA

TOTALS
------
Sub-total    : USD 45,000.00
Freight      : USD 2,500.00
Total Amount : USD 47,500.00
Currency     : USD
Gross Weight : 850 kg
Net Weight   : 820 kg
Packages     : 5 wooden crates (5 × 170 kg)

Payment Terms : 30 days net
Incoterms     : CIF Jebel Ali

I/We certify that the above information is true and correct.

Authorised Signatory: ________________________  Date: 2024-03-15
"""

BL_TEXT = """\
BILL OF LADING  (Original — Not Negotiable)

B/L Number     : BL-TEST-2024-001
Date of Issue  : 2024-03-17

Shipper        : Test Exports LLC
                 123 Industrial Way, Houston TX 77001, USA

Consignee      : Demo Trading FZE
                 JAFZA, Dubai, UAE

Notify Party   : Same as Consignee

Vessel / Voyage : MSC ADRIANA  /  Voyage 0123E
Port of Loading : Houston, Texas (USHOU)
Port of Discharge: Jebel Ali, UAE (AEJEA)
Place of Delivery: Jebel Ali Free Zone

PARTICULARS FURNISHED BY SHIPPER — CARRIER NOT RESPONSIBLE
-----------------------------------------------------------
Marks & Numbers : DEMO-2024-001 / 1-5
No of Packages  : 5 Wooden Crates
Description     : Industrial Centrifugal Pumps (Model CP-500)
                  SAID TO CONTAIN 10 UNITS
HS Code         : (to be assigned by customs broker)

Gross Weight    :       kg   <-- LEFT BLANK BY SHIPPER (smoke test BLOCK trigger)
Measurement     : 6.500 CBM

Freight         : PREPAID
B/L Clauses     : CLEAN ON BOARD

Place and Date of Issue: Houston, TX — 2024-03-17

Signed for and on behalf of the Master/Carrier:
________________________________________________
"""


# ---------------------------------------------------------------------------
# PDF writer
# ---------------------------------------------------------------------------

def _make_pdf(dest: Path, text: str, title: str) -> None:
    doc  = fitz.open()
    page = doc.new_page(width=595, height=842)   # ISO A4

    # Text block inside standard print margins.
    rect = fitz.Rect(72, 72, 523, 790)
    page.insert_textbox(
        rect,
        text,
        fontsize=10,
        fontname="helv",         # Helvetica — always available in fitz
        color=(0, 0, 0),
        align=0,                 # left-aligned
    )

    doc.set_metadata({"title": title, "creator": "Hackstrom generate_test_docs.py"})
    doc.ez_save(str(dest))
    doc.close()
    print(f"  ✓  {dest.relative_to(Path(__file__).parent.parent)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Generating test documents → {DEST_DIR}\n")
    _make_pdf(DEST_DIR / "invoice.pdf", INVOICE_TEXT, "Commercial Invoice INV-TEST-2024-001")
    _make_pdf(DEST_DIR / "bl.pdf",      BL_TEXT,      "Bill of Lading BL-TEST-2024-001")
    print(
        "\nDone.\n"
        "Upload  test_docs/weight_conflict_set/  to trigger the HITL flow.\n"
        "The B/L has a blank gross weight — this creates a BLOCK compliance issue.\n"
        "POST /resume/{run_id} with {\"gross_weight_kg\": 860} to resolve it.\n"
    )


if __name__ == "__main__":
    main()
