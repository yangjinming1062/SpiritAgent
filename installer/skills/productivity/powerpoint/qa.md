# Presentation validation

Use for created or edited decks. Commands use paths relative to this skill directory; resolve them from the installed skill location.

## QA (Required)

For created or edited decks, check content and render the affected slides to inspect layout. A new deck needs a full visual pass; shared layout or theme changes require checking all affected slides. Read-only extraction needs content verification without a rendering or editing cycle.

### Content QA

```bash
python -m markitdown output.pptx
```

Check for missing content, typos, wrong order.

**When using templates, check for leftover placeholder text:**

```bash
python -m markitdown output.pptx | grep -iE "xxxx|lorem|ipsum|this.*(page|slide).*layout"
```

If grep returns results, fix them before declaring success.

### Visual QA

Inspect the rendered slides directly. An independent review can help with complex decks when delegation is available and appropriate; it is not required for every edit.

Convert slides to images (see [Converting to Images](#converting-to-images)), then use this prompt:

```
Visually inspect these slides against the requested content and layout. Report observable issues with their locations; a clean result is valid.

Look for:
- Overlapping elements (text through shapes, lines through words, stacked elements)
- Text overflow or cut off at edges/box boundaries
- Decorative lines positioned for single-line text but title wrapped to two lines
- Source citations or footers colliding with content above
- Elements too close (< 0.3" gaps) or cards/sections nearly touching
- Uneven gaps (large empty area in one place, cramped in another)
- Insufficient margin from slide edges (< 0.5")
- Columns or similar elements not aligned consistently
- Low-contrast text (e.g., light gray text on cream-colored background)
- Low-contrast icons (e.g., dark icons on dark backgrounds without a contrasting circle)
- Text boxes too narrow causing excessive wrapping
- Leftover placeholder content

For each affected slide, identify any content or layout defects that need correction.

Read and analyze these images:
1. /path/to/slide-01.jpg (Expected: [brief description])
2. /path/to/slide-02.jpg (Expected: [brief description])

Report any issues found and state which slides were inspected.
```

### Verification Loop

1. Generate slides → Convert to images → Inspect
2. Compare the result with the requested content and layout
3. If defects are found, fix them and re-verify the affected slides
4. Finish when the required checks pass; report any checks that could not run

A clean first inspection needs no artificial fix-and-verify cycle.

---

## Converting to Images

Convert presentations to individual slide images for visual inspection:

```bash
python scripts/office/soffice.py --headless --convert-to pdf output.pptx
pdftoppm -jpeg -r 150 output.pdf slide
```

This creates `slide-01.jpg`, `slide-02.jpg`, etc.

To re-render specific slides after fixes:

```bash
pdftoppm -jpeg -r 150 -f N -l N output.pdf slide-fixed
```
