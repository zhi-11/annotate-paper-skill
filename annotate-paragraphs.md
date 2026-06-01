---
id: annotate-paragraphs
description: Generate paragraph summaries as margin text annotations in Zotero PDFs.
version: 1
contexts: single-paper
activation: auto
match: /\b(paragraph|段落).*\b(summary|概述|annotat|批注|标注|margin)\b/i
match: /(段落批注|段落概述|段落标注|页边批注|margin.*annotat)/
---

# Goal

Your ONLY responsibility is:

1. Read the paper (full text or only the sections specified by the user)
2. Select meaningful body-text paragraphs and generate a one-sentence Chinese summary for each
3. Classify each paragraph by type and assign the corresponding color from the Color Map
4. Write `annotation_plan.json` using `file_io`

All text locating, margin-space computation, coordinate generation, and Zotero writing are handled by `annotate_paragraphs.py`.

The working path of the script is `your_path`

Do NOT:
- Modify `annotate_paragraphs.py`
- generate coordinates, rects, or annotation positions
- generate JS manually
- interact with Zotero APIs
- create helper scripts

---

# Color Map

| Category | Color | Meaning |
|---|---|---|
| abstract | `#ffd400` | Abstract or summary content |
| background | `#5fb236` | Research background, literature review, current state |
| method | `#2ea8e5` | Methods, experimental design, techniques |
| result | `#f19837` | Key findings, experimental results, data conclusions |
| author_view | `#6c8f4b` | Author opinions, discussion, interpretation, speculation |
| figure_table | `#d2d8e2` | Figure/table descriptions or references |

---

# Workflow

## 1. Read the paper

- Read the full paper or only the sections specified by the user
- Skip title lines, figure/table captions, and reference sections entirely
- For each body-text paragraph, generate a one-sentence Chinese summary

**What to annotate** — only body-text paragraphs with substantive content:
- Major claims or conclusions
- Important methods or experimental designs
- Key results or data findings
- Conceptual insights, definitions, or arguments

**What to skip** — do NOT include in the annotations array:
- Title, authors, affiliations, dates, journal info
- Section headers — e.g. "Introduction", "Results", "Discussion"
- Figure and table captions — anything starting with "Fig.", "Figure", or "Table"
- Table body rows — tabular data
- References / bibliography entries
- Acknowledgements, Funding, Competing interests, Author contributions, Supplementary material
- Highlights or Graphical abstract bullet points
- Purely transitional paragraphs

---

## 2. Generate annotation_plan.json

Build a JSON object following the format below, then write it with `file_io`:

file_io({ action: 'write', filePath: '<your_skills_path>\annotate-paragraphs\annotation_plan.json', data: <the JSON object> })

IMPORTANT: use `action` (not mode) and `data` (not content). Pass the JSON object directly — do NOT stringify it.

```json
{
  "annotations": [
    {
      "paragraphs_id": "1",
      "Paragraph Overview": "在实验性移除micromeres或PMCs后，SMCs可转分化为PMCs并展现其全部表型特征。",
      "The first two sentences": "During normal embryogenesis, only the large micromeres give rise to skeleton-forming cells. Under experimental conditions, however, the same developmental program can be activated in other cell lineages.",
      "color": "#5fb236"
    }
  ]
}
```

Rules for each field:

- `paragraphs_id`: Sequential number, starting from `"1"`. For tracking only.

- `Paragraph Overview`: **One sentence in Chinese** (<30 characters). Keep factual and specific. No boilerplate.

- `The first two sentences`: Copy the **first 1–2 sentences** verbatim from the paragraph. The script uses this to locate the paragraph — it must match the original exactly. Do not modify.

- `color`: Assign a color **only** from the Color Map above. Pick the single best-fit category.

---

## 3. Execute the pipeline

### Step 1 — Get active PDF info

Use `zotero_script` + `file_io` to generate `active_pdf_info.json` automatically.

- Call `zotero_script` with `mode='read'` and the script below.
- Write the returned JSON object to `active_pdf_info.json` using `file_io`.

```javascript
const readers = Zotero.Reader._readers;
if (!readers || readers.length === 0) {
    return { error: "No active PDF reader" };
}
const reader = readers[0];
const item = Zotero.Items.get(reader.itemID);
const parentItem = item.parentItemID ? Zotero.Items.get(item.parentItemID) : null;

return {
    attachmentID: item.id,
    path: item.getFilePath(),
    title: item.getField("title"),
    parentItemID: item.parentItemID || null,
    parentTitle: parentItem ? parentItem.getField("title") : null,
    libraryID: item.libraryID,
};
```

---

### Step 2 — Generate annotations

Run:

```powershell
python annotate_paragraphs.py
```

The script reads `annotation_plan.json` + `active_pdf_info.json`, searches the PDF for each `The first two sentences`, computes the best margin position (left or right), and generates `write_annotations.js`.

---

### Step 3 — Execute in Zotero

Run `write_annotations.js` inside Zotero. Run only once.

---

# Failure Policy

If failure occurs, report ONLY:

- paper read failure
- Step 1 (zotero_script) failure
- `annotate_paragraphs.py` failure
- `write_annotations.js` failure
