import type { KeyboardEvent as ReactKeyboardEvent } from 'react'

/** 模态与侧栏里可参与 Tab 循环的元素。 */
export const FOCUSABLE_SELECTOR =
  'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, video[controls], [tabindex="0"]'

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

/**
 * React 事件版的焦点循环：容器就是事件所在的元素。
 * 与 `useDialogFocus` 的原生监听版对应，按需选用其一。
 */
export function createFocusCycleHandler(
  selector: string = FOCUSABLE_SELECTOR,
  wrapOutside = false
): (event: ReactKeyboardEvent<HTMLElement>) => void {
  return (event: ReactKeyboardEvent<HTMLElement>): void => {
    cycleTabFocus(event, event.currentTarget, selector, wrapOutside)
  }
}
