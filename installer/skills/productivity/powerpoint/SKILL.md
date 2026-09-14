---
name: powerpoint
description: "Create or edit .pptx decks with PptxGenJS or OOXML, or inspect their content and layout."
license: Proprietary. LICENSE.txt has complete terms
platforms: [linux, macos, windows]
---

# Powerpoint Skill

## When to use

Use for creating, editing, or inspecting a .pptx artifact with the workflows below. General presentation advice or drafting an outline does not require this skill. When the user chooses officecli, use that workflow; load this skill only if the task also needs its PptxGenJS, OOXML, or rendering guidance.

## Quick Reference

| Task | Guide |
|------|-------|
| Read/analyze content | `python -m markitdown presentation.pptx` |
| Edit or create from template | Read [editing.md](editing.md) |
| Create from scratch | Read [pptxgenjs.md](pptxgenjs.md) and [design.md](design.md) |
| Change visual design | Read [design.md](design.md) |
| Verify created or edited slides | Read [qa.md](qa.md) |

---

## Reading Content

```bash
# Text extraction
python -m markitdown presentation.pptx

# Visual overview
python scripts/thumbnail.py presentation.pptx

# Raw XML
python scripts/office/unpack.py presentation.pptx unpacked/
```

---

## Editing Workflow

For template or OOXML edits, read [editing.md](editing.md). Content extraction alone does not require it.

1. Analyze template with `thumbnail.py`
2. Unpack → manipulate slides → edit content → clean → pack

---

## Creating from Scratch

For PptxGenJS creation, read [pptxgenjs.md](pptxgenjs.md).

Use when no template or reference presentation is available.

---

For created or edited decks, verify content and the rendered layout of affected slides using [qa.md](qa.md). Read-only extraction needs content verification only. Commands use paths relative to the installed skill directory.

## Dependencies

- `pip install "markitdown[pptx]"` - text extraction
- `pip install Pillow` - thumbnail grids
- `npm install -g pptxgenjs` - creating from scratch
- LibreOffice (`soffice`) - PDF conversion (auto-configured for sandboxed environments via `scripts/office/soffice.py`)
- Poppler (`pdftoppm`) - PDF to images
