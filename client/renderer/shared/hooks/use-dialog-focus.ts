import type { RefObject } from 'react'
import { useLayoutEffect } from 'react'

import { cycleTabFocus, FOCUSABLE_SELECTOR } from '@/shared/lib/focus-cycle'

export interface DialogFocusOptions {
  /** 监听挂在窗口捕获阶段（覆盖容器外的焦点）还是容器本身。 */
  scope?: 'container' | 'window'
}

/**
 * 模态的焦点约束：打开时把焦点移入容器，Tab 不逃逸，关闭时归还焦点。
 * 容器取自内容元素所在的 `[role="dialog"]`，因此向导内部再嵌弹层也能落到正确的容器。
 */
export function useDialogFocus(content: RefObject<HTMLElement | null>, options: DialogFocusOptions = {}): void {
  const { scope = 'container' } = options

  useLayoutEffect(() => {
    const previous = document.activeElement

    const dialog =
      content.current?.closest<HTMLElement>('[role="dialog"]') ??
      content.current?.querySelector<HTMLElement>('[role="dialog"]') ??
      content.current

    if (!dialog) {
      return
    }

    dialog.querySelector<HTMLElement>(FOCUSABLE_SELECTOR)?.focus({ preventScroll: true })

    const containFocus = (event: KeyboardEvent): void => {
      if (event.defaultPrevented) {
        return
      }

      cycleTabFocus(event, dialog, FOCUSABLE_SELECTOR, true)
    }

    if (scope === 'window') {
      window.addEventListener('keydown', containFocus, true)
    } else {
      dialog.addEventListener('keydown', containFocus)
    }

    return () => {
      if (scope === 'window') {
        window.removeEventListener('keydown', containFocus, true)
      } else {
        dialog.removeEventListener('keydown', containFocus)
      }

      if (previous instanceof HTMLElement && previous.isConnected && previous.getClientRects().length) {
        previous.focus({ preventScroll: true })
      }
    }
  }, [content, scope])
}
