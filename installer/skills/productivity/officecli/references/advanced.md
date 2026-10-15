# Advanced officecli workflows

Read only the section needed for the current task. Query the installed CLI's help for exact options and version-specific behavior.

## Batch edits

Use `officecli help batch` before preparing a batch. Pass a JSON array through `--input <json-file>` or stdin to avoid shell quoting problems with multiline text, currency symbols, and XML. Each item is an object whose `command` is the bare verb; arguments are sibling fields, not a CLI command string:

```json
[
  {"command":"set","path":"/Sheet1/A1","props":{"value":"Name"}},
  {"command":"set","path":"/Sheet1/A2","props":{"value":"Alice"}}
]
```

```text
officecli batch data.xlsx --input updates.json --json
```

Check each result. Default batches roll back the whole batch if an item fails; `--best-effort` keeps successful items and therefore requires accounting for partial changes. A live resident can defer the disk write even after batch success; apply the entrypoint's save/close rule before another program reads the file.

## Live preview and selection

For a one-time visual check, use `officecli view <file> html` or the screenshot mode. Use `officecli watch <file>` when the task needs live preview or browser selection; open the URL returned by the command. The preview refreshes for officecli edits, so do not assume edits made by other programs are reflected.

Read current selection with `officecli get <file> selected --json` and operate on the returned paths rather than inferring an element from its appearance. Check the installed help and returned results for format-specific selection coverage. No selection is an empty result; an unavailable watch is an error.

Use `officecli help watch` for navigation and advisory marks. Marks are proposals held in the watch process; they do not apply document edits. Stop a preview started for the task with `officecli unwatch <file>` when it is no longer needed, and close the document resident when finished.

## Raw XML

Use raw operations only when structured commands cannot express the required change. Inspect the relevant part first and preserve its relationships and namespaces:

```text
officecli raw <file> <part>
officecli help raw-set
officecli help add-part
```

Use the returned part paths and the installed help to construct a targeted `raw-set` or `add-part` operation. Afterward, re-read the affected content, run `validate` and `view issues`, and inspect the rendered result when the change affects layout. Save or close before external validation or delivery.
