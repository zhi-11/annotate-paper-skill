#!/usr/bin/env python3

"""
annotate_pdf.py

Workflow:
1. 读取 active_pdf_info.json (当前 PDF 路径和 Zotero attachment ID)
2. 读取 annotation_plan.json (待标注文本列表)
3. 在 PDF 中定位每一条文本 (search_for -> 精确token -> 字母归一化模糊匹配)
4. 生成 resolved_annotations.json 和 write_annotations.js
5. 在 Zotero 中运行 JS 完成批注

特点:
- 优先使用 PyMuPDF 的 search_for (最快)
- 其次使用精确 token 匹配 (忽略标点、大小写、连字符)
- 最后使用字母归一化 + rapidfuzz 模糊匹配 (仅当快速方法失败时)
- 每条标注超时 10 秒自动跳过
- 详细的进度打印
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Sequence
import os
import fitz

# 尝试导入 rapidfuzz (可选加速)
try:
    from rapidfuzz import fuzz as rapidfuzz
    USE_RAPIDFUZZ = True
except ImportError:
    USE_RAPIDFUZZ = False

# =========================================================
# CONFIG
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
ACTIVE_INFO = BASE_DIR / "active_pdf_info.json"
DEFAULT_PLAN = BASE_DIR / "annotation_plan.json"
DEFAULT_OUTPUT = BASE_DIR / "resolved_annotations.json"
DEFAULT_JS = BASE_DIR / "write_annotations.js"
AI_TAG = "#ai批注"
MAX_SECONDS_PER_ANNOTATION = 10   # 单条标注最大匹配时间（秒）
FUZZY_THRESHOLD = 0.65            # 模糊匹配最低相似度 (0~1)

# =========================================================
# Unicode helpers
# =========================================================

HYPHENS = {"\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"}

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    for ch in HYPHENS:
        text = text.replace(ch, "-")
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def normalize_token(token: str) -> str:
    token = normalize_text(token)
    token = token.strip(" \t\r\n.,;:!?()[]{}<>\"'`~|/\\-")
    return token.casefold()

def tokenize(text: str) -> List[str]:
    parts = re.split(r'[\s-]+', text)
    return [tok for part in parts if (tok := normalize_token(part))]

# =========================================================
# IO
# =========================================================

def load_json(path):
    for enc in ("utf-8", "utf-8-sig"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except Exception:
            continue
    raise FileNotFoundError(f"Cannot read {path}")

def save_json(obj: Any, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def read_active_pdf_info():
    info = load_json(ACTIVE_INFO)
    pdf_path = Path(info["path"])
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    return info

def load_annotation_plan(path: str):
    payload = load_json(path)
    annotations = payload.get("annotations")
    if not isinstance(annotations, list):
        raise ValueError("'annotations' must be a list")
    return annotations

# =========================================================
# PDF extraction
# =========================================================

def page_words(page: fitz.Page):
    words = page.get_text("words")
    words.sort(key=lambda w: (w[5], w[6], w[7], w[1], w[0]))
    out = []
    for x0, y0, x1, y1, text, block, line, word in words:
        norm = normalize_token(text)
        if not norm:
            continue
        sub_tokens = norm.split('-')
        if len(sub_tokens) > 1:
            for sub in sub_tokens:
                if sub:
                    out.append({
                        "rect": (float(x0), float(y0), float(x1), float(y1)),
                        "text": sub,
                        "norm": sub,
                        "block": int(block),
                        "line": int(line),
                        "word": int(word),
                    })
        else:
            out.append({
                "rect": (float(x0), float(y0), float(x1), float(y1)),
                "text": text,
                "norm": norm,
                "block": int(block),
                "line": int(line),
                "word": int(word),
            })
    return out

# =========================================================
# Rectangle helpers
# =========================================================

def merge_rects(words: Sequence[Dict[str, Any]]):
    merged = []
    current = None
    current_key = None
    for item in words:
        x0, y0, x1, y1 = item["rect"]
        key = (item["block"], item["line"])
        if current is None or key != current_key:
            if current:
                merged.append(current)
            current = [x0, y0, x1, y1]
            current_key = key
            continue
        current[0] = min(current[0], x0)
        current[1] = min(current[1], y0)
        current[2] = max(current[2], x1)
        current[3] = max(current[3], y1)
    if current:
        merged.append(current)
    return [[round(v, 3) for v in rect] for rect in merged]

def fitz_to_zotero(rects, page_height):
    return [[round(x0,3), round(page_height - y1,3), round(x1,3), round(page_height - y0,3)] for x0,y0,x1,y1 in rects]

# =========================================================
# Matching (exact)
# =========================================================

def exact_match(norm_words, target_tokens):
    tlen = len(target_tokens)
    for i in range(len(norm_words) - tlen + 1):
        if norm_words[i:i+tlen] == list(target_tokens):
            return (i, i+tlen)
    return None

def locate_on_page(words, target_tokens):
    norm_words = [w["norm"] for w in words]
    ex = exact_match(norm_words, target_tokens)
    if ex:
        start, end = ex
        return {"words": list(words[start:end]), "score": 1.0, "method": "exact"}
    return None

# =========================================================
# Global locate with multi-stage fallback
# =========================================================


def take_first_contiguous_quads(quads, page):
    if len(quads) <= 1:
        return quads
    sorted_quads = sorted(quads, key=lambda q: (q.rect.y0, q.rect.x0))
    seen_texts = set()
    result = []
    for q in sorted_quads:
        text_in_quad = page.get_textbox(q.rect).strip()
        normalized = " ".join(text_in_quad.split())
        if normalized in seen_texts:
            break
        seen_texts.add(normalized)
        result.append(q)
    return result

def not_found_result(raw_text, target, reason):
    return {
        "text": raw_text,
        "comment": target["comment"],
        "color": target["color"],
        "status": "not_found",
        "reason": reason,
    }

def build_match_result(raw_text, target, page_index, page, quads, method):
    fitz_rects = []
    for q in quads:
        r = q.rect
        fitz_rects.append([r.x0, r.y0, r.x1, r.y1])
    # 不再合并多个矩形，保留原样
    page_height = float(page.rect.height)
    zotero_rects = fitz_to_zotero(fitz_rects, page_height)
    # 计算排序用的 top_y 和 x0（取所有矩形的最小值）
    top_y = min(r[1] for r in fitz_rects)
    x0 = min(r[0] for r in fitz_rects)
    print(f"     ✓ found at page {page_index+1} via {method} (rects: {len(fitz_rects)})")
    return {
        "text": raw_text,
        "comment": target["comment"],
        "color": target["color"],
        "category": target.get("category"),
        "pageIndex": page_index,
        "pageLabel": str(page_index+1),
        "pageHeight": round(page_height,3),
        "matchText": raw_text,
        "fitzRects": fitz_rects,
        "zoteroRects": zotero_rects,
        "sortKey": [page_index, round(top_y,3), round(x0,3)],
        "score": 1.0,
        "matchMethod": method,
        "status": "matched",
    }


def build_match_result_from_words(raw_text, target, page_index, page, located, method):
    match_words = located["words"]
    fitz_rects = merge_rects(match_words)
    page_height = float(page.rect.height)
    zotero_rects = fitz_to_zotero(fitz_rects, page_height)
    text_found = " ".join(w["text"] for w in match_words)
    top_y = min(rect[1] for rect in fitz_rects)
    x0 = min(rect[0] for rect in fitz_rects)
    print(f"     ✓ found at page {page_index+1} via {method}")
    return {
        "text": raw_text,
        "comment": target["comment"],
        "color": target["color"],
        "category": target.get("category"),
        "pageIndex": page_index,
        "pageLabel": str(page_index+1),
        "pageHeight": round(page_height,3),
        "matchText": text_found,
        "fitzRects": fitz_rects,
        "zoteroRects": zotero_rects,
        "sortKey": [page_index, round(top_y,3), round(x0,3)],
        "score": 1.0,
        "matchMethod": method,
        "status": "matched",
    }

def locate_target(doc: fitz.Document, target: Dict[str, Any]):
    raw_text = target["text"]
    search_text = raw_text.strip()
    tokens = tokenize(raw_text)
    start_time = time.time()

    # ---------- Stage 1: search_for ----------
    print(f"  -> [search_for] {raw_text[:60]}...")
    for page_index in range(len(doc)):
        if time.time() - start_time > MAX_SECONDS_PER_ANNOTATION:
            print(f"  ⏱️ TIMEOUT (> {MAX_SECONDS_PER_ANNOTATION}s) for: {raw_text[:60]}")
            return not_found_result(raw_text, target, "timeout")
        page = doc[page_index]
        quads = page.search_for(search_text, quads=True)
        if quads:
            return build_match_result(raw_text, target, page_index, page, take_first_contiguous_quads(quads, page), "search_for")

    # ---------- Stage 2: exact token match ----------
    print(f"  -> [exact token] {raw_text[:60]}...")
    for page_index in range(len(doc)):
        if time.time() - start_time > MAX_SECONDS_PER_ANNOTATION:
            print(f"  ⏱️ TIMEOUT (> {MAX_SECONDS_PER_ANNOTATION}s) for: {raw_text[:60]}")
            return not_found_result(raw_text, target, "timeout")
        page = doc[page_index]
        words = page_words(page)
        located = locate_on_page(words, tokens)
        if located:
            return build_match_result_from_words(raw_text, target, page_index, page, located, "exact_token")

    # ---------- Stage 3: word‑level fuzzy matching (letters only) ----------
    print(f"  -> [fuzzy letter] {raw_text[:60]}...")
    target_letters = re.sub(r'[^a-zA-Z]', '', raw_text).lower()
    if len(target_letters) < 5:
        print(f"  ✗ target too short after letter normalization")
        return not_found_result(raw_text, target, "too_short")

    best_score = 0.0
    best_page = None
    best_start_word = None
    best_end_word = None
    best_words = None

    for page_index in range(len(doc)):
        if time.time() - start_time > MAX_SECONDS_PER_ANNOTATION:
            print(f"  ⏱️ TIMEOUT (> {MAX_SECONDS_PER_ANNOTATION}s) for: {raw_text[:60]}")
            return not_found_result(raw_text, target, "timeout")

        page = doc[page_index]
        words = page_words(page)
        if not words:
            continue

        # 为每个单词计算纯字母形式（去除标点数字等）
        word_letters_list = []
        for w in words:
            letters = re.sub(r'[^a-zA-Z]', '', w["text"]).lower()
            word_letters_list.append(letters)

        # 估算窗口大小（单词数量）
        valid_lengths = [len(l) for l in word_letters_list if l]
        if not valid_lengths:
            continue
        avg_word_len = max(1, sum(valid_lengths) // len(valid_lengths))
        min_words = max(1, len(target_letters) // avg_word_len - 2)
        max_words = min(len(words), len(target_letters) // avg_word_len + 5)

        best_local_score = 0.0
        best_local_start_w = 0
        best_local_end_w = 0

        # 滑动窗口遍历单词
        for start_w in range(len(words)):
            if start_w + min_words > len(words):
                break
            curr_letters = []
            for end_w in range(start_w, min(len(words), start_w + max_words)):
                letters = word_letters_list[end_w]
                if letters:
                    curr_letters.append(letters)
                curr_str = ''.join(curr_letters)
                if len(curr_str) > len(target_letters) * 1.5:
                    break
                if USE_RAPIDFUZZ:
                    score = rapidfuzz.ratio(curr_str, target_letters) / 100.0
                else:
                    score = SequenceMatcher(None, curr_str, target_letters).ratio()
                if score > best_local_score:
                    best_local_score = score
                    best_local_start_w = start_w
                    best_local_end_w = end_w
                    if best_local_score > 0.98:
                        break
            if best_local_score > 0.98:
                break

        if best_local_score > best_score:
            best_score = best_local_score
            best_page = page_index
            best_start_word = best_local_start_w
            best_end_word = best_local_end_w
            best_words = words

    if best_score >= FUZZY_THRESHOLD and best_page is not None and best_words is not None:
        matched_words = best_words[best_start_word:best_end_word+1]
        if matched_words:
            fitz_rects = merge_rects(matched_words)
            page = doc[best_page]
            page_height = float(page.rect.height)
            zotero_rects = fitz_to_zotero(fitz_rects, page_height)
            text_found = " ".join(w["text"] for w in matched_words)
            top_y = min(rect[1] for rect in fitz_rects)
            x0 = min(rect[0] for rect in fitz_rects)
            print(f"     ✓ found at page {best_page+1} via fuzzy letter (score={best_score:.2f})")
            return {
                "text": raw_text,
                "comment": target["comment"],
                "color": target["color"],
                "category": target.get("category"),
                "pageIndex": best_page,
                "pageLabel": str(best_page+1),
                "pageHeight": round(page_height,3),
                "matchText": text_found,
                "fitzRects": fitz_rects,
                "zoteroRects": zotero_rects,
                "sortKey": [best_page, round(top_y,3), round(x0,3)],
                "score": round(best_score,4),
                "matchMethod": "fuzzy_letter",
                "status": "matched",
            }

    print(f"  ✗ NOT FOUND: {raw_text[:60]}")
    return not_found_result(raw_text, target, "not_found_in_doc")

# =========================================================
# JS generation
# =========================================================

def build_write_js(attachment_id, annotations):
    items_json = json.dumps(annotations, ensure_ascii=False, indent=2)
    return f"""
const attachment = Zotero.Items.get({attachment_id});
const items = {items_json};
const created = [];
for (const item of items) {{
  try {{
    const rects = item.zoteroRects;
    const pageIndex = item.pageIndex;
    const pageHeight = item.pageHeight;
    const topY = ('00000' + Math.round(pageHeight - rects[0][3])).slice(-5);
    const ann = new Zotero.Item('annotation');
    ann.libraryID = attachment.libraryID;
    ann.parentItemID = attachment.id;
    ann.annotationType = 'highlight';
    ann.annotationText = item.text;
    ann.annotationComment = item.comment;
    ann.annotationColor = item.color;
    ann.annotationPageLabel = item.pageLabel;
    ann.annotationSortIndex = `${{('00000' + pageIndex).slice(-5)}}|000000|${{topY}}`;
    ann.annotationPosition = JSON.stringify({{ pageIndex, rects }});
    ann.addTag('{AI_TAG}');
    const id = await ann.saveTx();
    created.push({{ id, text: item.text, page: pageIndex + 1 }});
  }} catch (err) {{
    Zotero.logError(err);
  }}
}}
Services.prompt.alert(null, "Annotation Complete", JSON.stringify(created, null, 2));
"""

# =========================================================
# Main
# =========================================================

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default=DEFAULT_PLAN, help="annotation_plan.json")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--js-output", default=DEFAULT_JS)
    args = parser.parse_args()

    active = read_active_pdf_info()
    pdf_path = active["path"]
    attachment_id = active["attachmentID"]

    print(f"[PDF]\n{pdf_path}")
    print(f"\n[Attachment]\n{attachment_id}")

    annotations = load_annotation_plan(args.plan)
    print(f"\n[Plan] {len(annotations)} annotations")

    doc = fitz.open(pdf_path)
    resolved = []
    total = len(annotations)

    print(f"\n[Processing {total} annotations]")
    for idx, ann in enumerate(annotations, 1):
        text_short = ann["text"][:80]
        print(f"\n[{idx}/{total}] {text_short}")
        result = locate_target(doc, ann)
        if result["status"] != "matched":
            reason = result.get("reason", "not_found")
            print(f"  ✗ FAILED: {ann['text'][:60]} | comment: {ann['comment'][:40]} | color: {ann['color']} | reason: {reason}")
            continue
        resolved.append(result)

    resolved.sort(key=lambda x: x["sortKey"])
    print(f"\n[Resolved] {len(resolved)} annotations")
#    save_json(resolved, args.output)
#    print(f"\nSaved:\n{args.output}")

    js_code = build_write_js(attachment_id, resolved)
    with open(args.js_output, "w", encoding="utf-8") as f:
        f.write(js_code)
    print(f"\nGenerated:\n{args.js_output}")

    # 自动删除输入文件（annotation_plan.json 和 active_pdf_info.json）
    try:
        if os.path.exists(args.plan):
            os.remove(args.plan)
            print(f"Deleted: {args.plan}")
        if os.path.exists(ACTIVE_INFO):
            os.remove(ACTIVE_INFO)
            print(f"Deleted: {ACTIVE_INFO}")
    except Exception as e:
        print(f"Warning: could not delete files: {e}")

if __name__ == "__main__":
    main()

