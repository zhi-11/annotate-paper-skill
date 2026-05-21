---
id: annotate-paper
description: Generate semantic Zotero PDF annotations.
version: 4
contexts: single-paper
activation: auto
match: /\b(annotate|annotation|highlight|markup)\b.*\b(paper|article|pdf)\b/i
match: /(标注|批注|注释|高亮).*(论文|文章|文献|PDF)/i
---


# Goal

Your ONLY responsibility is:

1. Read the paper
2. Select meaningful annotation targets
3. Generate `annotation_plan.json`

All locating, coordinate generation, and Zotero writing are handled by deterministic scripts.
The working path of the script is `your_path`

Do NOT:
- Modify `annotate_pdf.py`
- generate coordinates
- generate rects
- generate annotationPosition
- generate JS manually
- interact with Zotero APIs
- create helper scripts

---

# Color Map

| Category | Color | Meaning |
|---|---|---|
| highlight | `#ffd400` | General important content (no specific category) |
| background | `#5fb236` | Research background, current state of the field, known knowledge |
| research_gap | `#bae9a3` | Research gap, unresolved issues, limitations |
| method | `#2ea8e5` | Experimental methods, techniques, data analysis pipelines |
| result | `#f19837` | Key findings, experimental results, data conclusions |
| terminology | `#e56eee` | Core terms, key concept definitions |
| vocabulary | `#ff6666` | Unfamiliar words (non-core terms) |
| question | `#aaaaaa` | Questions, hypotheses to be tested, open problems |
| author_view | `#6c8f4b` | Author's perspective, speculation, interpretation |
| figure | `#d2d8e2` | Figure conclusions, graphical information |
| citation | `#441ae1` | Important citations, prior work |
| data | `#6b90cc` | Datasets, resources, code, supplementary materials, gene IDs |

---

# Workflow

## 1. Read the paper

Focus on:
- important results
- methods
- biological insights
- research gaps
- author interpretations
- important terminology
- datasets
- figure conclusions

---

## 2. Generate annotation_plan.json

Structure:

```json
{
  "annotations": [
    {
      "text": "By introducing these heterologous regulatory regions into developing sea urchin embryos we provide evidence of their remarkable conservation across ~500 million years of evolution.",
      "comment": "跨物种实验证明kirrelL调控区在5亿年演化中高度保守。",
      "color": "#f19837"
    }
  ]
}
```

Rules:

- `text`
  should match the paper text as closely as possible

- `comment`: **One sentence in Chinese** explaining the `text`.
  - Do NOT translate or paraphrase the `text`.
  - The explanation may include either or both of the following:
    1. **Clarify meaning** (optional): If the original `text` is complex or technical, simplify it in plain language.
    2. **Describe what it tells us**: Based on the `text` and the full paper, explain what the sentence means or contributes (e.g., a fact, a result, a method, a gap, an opinion, a contrast, a conclusion). Be concise and natural.

- `color`
  must use ONLY colors from the color map

Do NOT include:
- pages
- coordinates
- rects
- IDs
- metadata

---

## 3. Execute pipeline

Run in order:

### Step 1 – Get active PDF info

Use `zotero_script` + `file_io` to generate `active_pdf_info.json` automatically.


- Call `zotero_script` with `mode='read'` and the script below.
- Write the returned JSON object to `active_pdf_info.json` using `file_io`.


```javascript
// zotero_script content
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

### Step 2

Run:

```powershell
python annotate_pdf.py
```

Generates:

- write_annotations.js

(Input files `annotation_plan.json` and `active_pdf_info.json` are automatically deleted by the script.)

---

### Step 3

Run:

```text
write_annotations.js
```

inside Zotero.

This process runs only once; do not run it repeatedly.

---

# Annotation Quality

Prefer:
- biologically meaningful statements
- major conclusions
- important methods
- unresolved questions
- conceptual insights
- useful terminology

Avoid:
- filler text
- repeated statements
- generic introductions
- rhetorical transitions
- low-information sentences

---

# Density Limits

- Max 3 result annotations per ~500 words
- Max 3 method annotations per ~500 words
- Max 5 terminology/vocabulary annotations per ~500 words
- Avoid overlapping highlights
- Do not repeatedly annotate the same term

---

# Restrictions

Never:
- create helper scripts
- manually locate coordinates
- manually generate write JS
- create test annotations
- modify deterministic scripts
- probe Zotero storage paths

---

# Failure Policy

If failure occurs, report ONLY:

- paper read failure
- annotation generation failure
- Step 1 (zotero_script) failure
- annotate_pdf.py failure
- write_annotations.js failure
