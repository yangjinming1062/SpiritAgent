---
name: officecli
description: Read, create, edit, and validate Word (.docx), Excel (.xlsx), and PowerPoint (.pptx) files with officecli.
version: 1.0.0
author: iOfficeAI
license: MIT
platforms: [linux, macos, windows]
metadata:
  spiritagent:
    tags: [Office, Word, Excel, PowerPoint, Documents, Productivity, CLI]
    related_skills: [ocr-and-documents]
---
# officecli

Use `officecli` for Office files without requiring an Office installation. For PDF text extraction and scanned documents, use `ocr-and-documents`.

## Environment

Check `officecli --version`. If the command is missing, install it for the current platform, then verify again in a new terminal if necessary:

```powershell
# Windows
irm https://d.officecli.ai/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://d.officecli.ai/install.sh | bash
```

If installation or execution fails, report the actual dependency or error before proceeding.

## Workflow

1. Inspect an existing file with `officecli view <file> outline` or `officecli view <file> text`; use `get` or `query` for the relevant elements. Add `--json` when structured output is useful. Use returned paths, preferring stable IDs when available; positional paths are 1-based.
2. For creation or complex editing, load the appropriate authoring guidance before changing the artifact. Ordinary reading and simple localized edits do not require an authoring guide.
3. Use `create`, `add`, `set`, `move`, or `remove` as needed. Check `officecli help <command>` for syntax and `officecli help <format> <element>` for properties and supported values; use `docx`, `xlsx`, or `pptx` as the format. Read and edit through these structured commands before considering raw XML.
4. Re-read the changed content, then check `officecli view <file> issues` and `officecli validate <file>`. When layout matters, inspect an HTML or screenshot preview using `view`; schema validation alone does not establish visual quality.
5. Before delivery, upload, rendering by another program, or any other external file read, run `officecli save <file>` or `officecli close <file>`. Changes may still be held in a resident process: officecli's own reads see them immediately, while other programs read the last saved bytes. `save` keeps the resident running; `close` saves and releases it. Close the file when finished.

## Authoring guidance on demand

Run `officecli load_skill` to discover available guides and their triggers. Choose the most specific guide that fits the artifact; otherwise use the format default:

| Format | Default guide |
| --- | --- |
| Word | `officecli load_skill word` |
| Excel | `officecli load_skill excel` |
| PowerPoint | `officecli load_skill pptx` |

Specialized guides cover tasks such as academic papers, fundraising decks, financial models, dashboards, and Morph animation. Load the most specific matching guide, then follow its explicit requirements for base guides and references without loading unrelated guides. The command prints its instructions and reference manifest; read only the needed reference with `officecli load_skill <name> --path <relative-path>`. Reuse guidance already present in context.

## Advanced operations

For batch edits, live preview and selection, or document internals, read [references/advanced.md](references/advanced.md) using `skill_view(name="officecli", file_path="references/advanced.md")`.
