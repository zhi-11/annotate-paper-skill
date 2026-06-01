#!/usr/bin/env python3
"""
annotate_paragraphs.py

Reads annotation_plan.json, searches the active Zotero PDF for each
"The first two sentences" entry, computes the best margin position
(left or right), and generates write_annotations.js with free-text
annotations placed next to each paragraph.

Single-pass: no intermediate paragraphs.json needed.
"""

import json
import os
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
ACTIVE_INFO = BASE_DIR / "active_pdf_info.json"
PLAN_FILE = BASE_DIR / "annotation_plan.json"
JS_OUTPUT = BASE_DIR / "write_annotations.js"
AI_TAG = "#ai段落概述"

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------
# ANNOTATION_MAX_WIDTH removed
ANNOTATION_MIN_WIDTH = 28
ANNOTATION_MAX_HEIGHT = 200
ANNOTATION_MIN_HEIGHT = 18
MARGIN_GAP = 5
PAGE_EDGE_GAP = 3
FUZZY_THRESHOLD = 0.65
MAX_SECONDS_PER_ANNOTATION = 10&
# Text layout estimation for dynamic box sizing
FONT_SIZE = 8
CHAR_WIDTH_EST = 9.0   # approx pt width of one CHK char at fontSize 8
LINE_HEIGHT_EST = 12.0  # approx line height at fontSize 8
PADDING = 4.0           # vertical padding inside the annotation box

#Text layout estimation for dynamic box sizing
FONT_SIZE = 8
CHAR_WIDTH_EST = 9.0
LINE_HEIGHT_EST = 12.0
PADDING = 4.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    for enc in ("utf-8", "utf-8-sig"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return json.load(fh)
        except Exception:
            continue
    raise FileNotFoundError(f"Cannot read {path}")


def normalize_text(text: str) -> str:
    """NFKC normalize, collapse whitespace, lowercase."""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def clean_hyphenation(text: str) -> str:
    """Fix end-of-line hyphenation: 'ecologi-\ncal' -> 'ecological'."""
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


# ===================================================================
# Text search in PDF (adapted from annotate_pdf.py)
# ===================================================================

def search_text_in_pdf(
    doc: fitz.Document,
    target: str,
    start_page: int = 0,
) -> Optional[Tuple[int, fitz.Rect]]:
    """
    Search for `target` text in the PDF.
    Returns (page_index, rect) or None.
    Strategy: PyMuPDF search_for -> token match -> fuzzy fallback.
    """
    target_norm = normalize_text(target)
    if not target_norm:
        return None

    # 1) PyMuPDF native search
    for page_idx in range(start_page, len(doc)):
        page = doc[page_idx]
        rects = page.search_for(target_norm[:80])
        if rects:
            return (page_idx, rects[0])

    # 2) Token-based search
    tokens = target_norm.split()
    if len(tokens) >= 3:
        anchor = " ".join(tokens[:3])
        for page_idx in range(start_page, len(doc)):
            page = doc[page_idx]
            rects = page.search_for(anchor)
            if rects:
                return (page_idx, rects[0])

    # 3) Fuzzy fallback – refine to word-level rect when possible
    for page_idx in range(start_page, len(doc)):
        page = doc[page_idx]
        blocks = page.get_text("blocks")
        for b in blocks:
            if b[6] != 0:
                continue
            block_text_norm = normalize_text(b[4])
            if not block_text_norm:
                continue
            ratio = SequenceMatcher(None, target_norm[:200], block_text_norm[:200]).ratio()
            if ratio >= FUZZY_THRESHOLD:
                # Try to refine with word-level search on this page
                tokens = target_norm.split()
                if len(tokens) >= 3:
                    anchor = " ".join(tokens[:3])
                    rects = page.search_for(anchor)
                    if rects:
                        return (page_idx, rects[0])
                return (page_idx, fitz.Rect(b[:4]))

    return None


# ===================================================================
# Margin-space finder
# ===================================================================

def find_margin_rect(
    match_rect: fitz.Rect,
    page: fitz.Page,
) -> Tuple[Optional[List[float]], str]:
    """
    Given a text match rect, find the margin annotation position.
    Side is determined by whether the matched text is on the left or right
    half of the page. Falls back to the other side if there is not enough room.
    Annotation top aligns with the first line of the matched text.
    """
    px0, py0, px1, py1 = match_rect.x0, match_rect.y0, match_rect.x1, match_rect.y1
    page_x0, page_y0, page_x1, page_y1 = (
        page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1,
    )
    page_center = (page_x0 + page_x1) / 2

    # Preferred side: follow the text position on the page
    preferred = "left" if px0 < page_center else "right"

    for side in (preferred, "right" if preferred == "left" else "left"):
        if side == "right":
            available_w = page_x1 - px1 - PAGE_EDGE_GAP - MARGIN_GAP
        else:
            available_w = px0 - page_x0 - PAGE_EDGE_GAP - MARGIN_GAP

        if available_w < ANNOTATION_MIN_WIDTH:
            continue

        box_w = min(available_w, ANNOTATION_MAX_WIDTH)

        if side == "right":
            ann_x0 = px1 + MARGIN_GAP
            ann_x1 = ann_x0 + box_w
        else:
            ann_x1 = px0 - MARGIN_GAP
            ann_x0 = ann_x1 - box_w

        ann_x0 = max(ann_x0, page_x0 + PAGE_EDGE_GAP)
        ann_x1 = min(ann_x1, page_x1 - PAGE_EDGE_GAP)

        if ann_x1 - ann_x0 < ANNOTATION_MIN_WIDTH:
            continue

        ann_y0 = py0
        ann_y1 = min(py0 + ANNOTATION_MAX_HEIGHT, page_y1 - PAGE_EDGE_GAP)

        if ann_y1 - ann_y0 < ANNOTATION_MIN_HEIGHT:
            continue

        # Convert Y from PyMuPDF (y=0 at top) to Zotero (y=0 at bottom)
        page_h = float(page.rect.height)
        z_y0 = page_h - ann_y1
        z_y1 = page_h - ann_y0

        return [round(ann_x0, 1), round(z_y0, 1), round(ann_x1, 1), round(z_y1, 1)], side

    return None, ""

# ===================================================================
# JS generation
# ===================================================================

def build_write_js(attachment_id: int, items: List[Dict[str, Any]]) -> str:
    items_json = json.dumps(items, ensure_ascii=False, indent=2)
    return f"""
const attachment = Zotero.Items.get({attachment_id});
const items = {items_json};
const created = [];

for (const item of items) {{
  try {{
    const ann = new Zotero.Item('annotation');
    ann.libraryID = attachment.libraryID;
    ann.parentItemID = attachment.id;
    ann.annotationType = 'text';
    ann.annotationComment = item.comment;
    ann.annotationColor = item.color;
    ann.annotationPageLabel = String(item.pageIndex + 1);
    ann.annotationPosition = JSON.stringify({{
      pageIndex: item.pageIndex,
      fontSize: 8,
      rotation: 0,
      rects: [[item.rects[0], item.rects[1], item.rects[2], item.rects[3]]]
    }});
    ann.annotationSortIndex = `${{String(item.pageIndex).padStart(5, '0')}}|${{String(Math.round(item.rects[1])).padStart(6, '0')}}|00000`;
    ann.addTag('{AI_TAG}');
    const id = await ann.saveTx();
    created.push({{ id, page: item.pageIndex + 1, side: item.side }});
  }} catch (err) {{
    Zotero.logError(err);
  }}
}}

const summary = created.length
  ? `Created ${{created.length}} paragraph annotations.`
  : 'No annotations were created.';
Services.prompt.alert(null, 'Paragraph Annotations', summary + '\\n' + JSON.stringify(created, null, 2));
"""


# ===================================================================
# Main
# ===================================================================

def main() -> None:
    # 1) Load inputs
    active = load_json(ACTIVE_INFO)
    plan = load_json(PLAN_FILE)

    pdf_path = active["path"]
    attachment_id = active["attachmentID"]
    annotations = plan.get("annotations", [])

    if not annotations:
        print("ERROR: annotation_plan.json contains no annotations")
        sys.exit(1)

    if not Path(pdf_path).exists():
        print(f"ERROR: PDF not found: {pdf_path}")
        sys.exit(1)

    print(f"PDF  : {pdf_path}")
    print(f"Plan : {len(annotations)} entries")

    # 2) Open PDF
    doc = fitz.open(pdf_path)

    # 3) Process each annotation
    resolved: List[Dict[str, Any]] = []
    matched = 0
    skipped = 0

    for entry in annotations:
        target_text = entry.get("The first two sentences", "")
        comment = entry.get("Paragraph Overview", "")
        color = entry.get("color", "")

        if not target_text or not comment:
            skipped += 1
            continue

        # Search for text in PDF
        result = search_text_in_pdf(doc, target_text)
        if result is None:
            print(f"  SKIP: could not locate -> \"{target_text[:60]}...\"")
            skipped += 1
            continue

        page_idx, match_rect = result
        page = doc[page_idx]

        # Find margin position
        margin_result = find_margin_rect(match_rect, page)
        if margin_result[0] is None:
            print(f"  SKIP: no margin space on page {page_idx + 1}")
            skipped += 1
            continue

        ann_rect, side = margin_result

        resolved.append({
            "pageIndex": page_idx,
            "rects": ann_rect,
            "comment": comment,
            "color": color,
            "side": side,
        })
        matched += 1

    doc.close()

    if not resolved:
        print(f"ERROR: 0/{len(annotations)} annotations resolved.")
        sys.exit(1)

    # 4) Generate JS
    js_code = build_write_js(attachment_id, resolved)
    with open(JS_OUTPUT, "w", encoding="utf-8") as fh:
        fh.write(js_code)

    print(f"Done: {matched} matched, {skipped} skipped -> {JS_OUTPUT}")

    # 5) Clean up input files (COMMENTED OUT for debugging)\n    # for fp in (PLAN_FILE, ACTIVE_INFO):\n    #     try:\n    #         os.remove(fp)\n    #     except Exception:\n    #         pass
if __name__ == "__main__":
    main()



