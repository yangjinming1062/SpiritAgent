import type { RefObject } from 'react'
import { useLayoutEffect } from 'react'

import { cycleTabFocus, FOCUSABLE_SELECTOR } from '@/shared/lib/focus-cycle'

export interface DialogFocusOptions {
  /** 打开时聚焦的元素。缺省聚焦容器内第一个可聚焦元素。 */
  focusRef?: RefObject<HTMLElement | null>
  /** 关闭时是否把焦点还给打开前的元素。 */
  restoreFocus?: boolean
  /** 监听挂在窗口捕获阶段（覆盖容器外的焦点）还是容器本身。 */
  scope?: 'container' | 'window'
  /** 可聚焦元素选择器。 */
  selector?: string
  /** 焦点在容器外时是否也回环。 */
  wrapOutside?: boolean
}

/**
 * 模态的焦点约束：打开时把焦点移入容器，Tab 不逃逸，关闭时按需归还焦点。
 * 容器取自内容元素所在的 `[role="dialog"]`，因此向导内部再嵌弹层也能落到正确的容器。
 */
export function useDialogFocus(content: RefObject<HTMLElement | null>, options: DialogFocusOptions = {}): void {
  const {
    focusRef,
    restoreFocus = false,
    scope = 'container',
    selector = FOCUSABLE_SELECTOR,
    wrapOutside = true
  } = options

  useLayoutEffect(() => {
    const previous = document.activeElement

    const dialog =
      content.current?.closest<HTMLElement>('[role="dialog"]') ??
      content.current?.querySelector<HTMLElement>('[role="dialog"]') ??
      content.current

    if (!dialog) {
      return
    }

    const target = focusRef?.current ?? dialog.querySelector<HTMLElement>(selector)
    target?.focus({ preventScroll: true })

    const containFocus = (event: KeyboardEvent): void => {
      if (event.defaultPrevented) {
        return
      }

      cycleTabFocus(event, dialog, selector, wrapOutside)
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

      if (restoreFocus && previous instanceof HTMLElement && previous.isConnected && previous.getClientRects().length) {
        previous.focus({ preventScroll: true })
      }
    }
  }, [content, focusRef, restoreFocus, scope, selector, wrapOutside])
}
