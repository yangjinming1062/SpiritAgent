# macOS desktop interaction

Read this reference when operating macOS. The desktop driver uses the
Accessibility API and sends input to the target in the background when
the application supports it. This does not guarantee every app or
control accepts background input.

## Targeting and input

- Use names returned by `list_apps`; they may be localized. App matching
  uses a case-insensitive substring. Inspect the capture's window title
  when an app has several windows.
- `focus_app` selects the target without raising it. `bring_to_front`
  cannot raise windows on macOS. Hidden, minimized, or off-Space windows
  may be inaccessible; ask the user to bring an unavailable window to the
  current Space rather than switching Spaces.
- `som` returns a screenshot and numbered AX element list; the image has
  no number overlays. Select indices from the list. Use `ax` when only
  the element tree is needed.
- Dragging is unavailable. Modified clicks may be refused because they
  need the foreground; scroll modifiers are unsupported. Double-click
  supports the left button only. Prefer supported controls and
  `set_value` where appropriate; report an unsupported interaction when
  it prevents completion.

## Keys and limits

Use `cmd` for Command, `option` or `alt` for Option, and `ctrl` for
Control. A key combo contains modifiers plus one ordinary key, such as
`cmd+s` or Finder's `cmd+shift+g`. Named keys include `return`, `escape`,
`tab`, `space`, and the arrows.

The tool blocks shortcuts including Cmd+Q/W/M, force quit, screenshot
shortcuts, and empty-trash shortcuts. Follow a refusal without bypassing
its guard.

## Recovery

- **Desktop unavailable or permissions missing:** report the returned
  cause. The driver needs Accessibility and Screen Recording permission;
  leave system permission prompts to the user. Do not repeatedly retry a
  missing driver or denied permission.
- **Stale element or unchanged UI:** capture again and inspect the active
  window state before selecting a new action.
- **Action timed out or connection failed:** input may still have taken
  effect. Capture and inspect the result before retrying a mutation.
