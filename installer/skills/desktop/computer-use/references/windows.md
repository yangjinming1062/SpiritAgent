# Windows desktop interaction

Read this reference when operating Windows. Elements come from UI
Automation; mouse clicks, scrolling, dragging, and keyboard input affect
the actual desktop. A window screenshot may show a covered window while
clicks still hit whatever is visible at that screen position.

## Targeting and input

- Use `list_apps` for process names. `capture` also matches window titles;
  inspect the returned app and window title to confirm the target.
- `focus_app` selects a target without raising it by default. Before
  typing or interacting with a covered target, use
  `focus_app(app="…", bring_to_front=true)`, then capture the app again.
- `som` shows numbered UIA elements on the image and in the element list.
  If markings obscure the content, use `vision` for the plain image.
- Target scrolling with an element or coordinate when possible; without
  either, the wheel acts at the current mouse pointer. Dragging is
  supported, with both endpoints taken from the current capture.
- Non-ASCII `type` input uses the clipboard and Ctrl+V, then attempts to
  restore the previous clipboard text. Verify the intended field received
  the text.

## Keys and limits

Join key names with `+`: `ctrl+s`, `ctrl+c`, `ctrl+v`, `ctrl+z`. Use `ctrl`
for Control and `win` for the Windows key; `cmd` and `meta` also mean the
Windows key. Named keys include `return`, `escape`, arrows, `backspace`,
`delete`, `home`, `end`, `pageup`, `pagedown`, and `f1`–`f12`.

The tool blocks shortcuts including Alt+F4, Ctrl+W, Ctrl+Alt+Delete, and
Win+L/D/E/R/I/M/X. Follow a refusal rather than replacing the blocked
shortcut with another way to perform the same unauthorized operation.
Screen corners are reserved for the user's mouse abort gesture.

## Recovery

- **Keystrokes refused:** raise the selected target and re-capture before
  retrying; do not type into a different foreground app.
- **Wrong or missing window:** recheck `list_apps` and the capture title.
  Hidden or minimized windows may be inaccessible. If the target is on
  another virtual desktop, ask the user to bring it to the current one.
- **Click had no effect or a stale element:** capture again and inspect
  the current UI, including any modal. Handle it only within the task's
  authorization before choosing another action.
- **Mouse abort or desktop unavailable:** stop input and report the
  condition. Do not override the abort or repeatedly retry an unavailable
  desktop.
