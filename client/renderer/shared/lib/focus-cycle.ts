export function cycleTabFocus(
  event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'preventDefault'>,
  container: HTMLElement | null | undefined,
  selector: string,
  wrapOutside = false
): void {
  if (event.key !== 'Tab' || !container) {
    return
  }

  const focusable = Array.from(container.querySelectorAll<HTMLElement>(selector)).filter(
    element => !element.hasAttribute('disabled') && element.getClientRects().length > 0
  )

  const first = focusable[0]
  const last = focusable.at(-1)
  const active = document.activeElement
  const outside = wrapOutside && !container.contains(active)

  if (event.shiftKey && (active === first || outside)) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && (active === last || outside)) {
    event.preventDefault()
    first?.focus()
  }
}
