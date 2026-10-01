import { type RefObject, useEffect } from 'react'

import { useEscapeKey } from './use-escape-key'
import { useLatestRef } from './use-latest-ref'

// 弹层打开期间，点击 ref 元素外部或按 Esc 即关闭；两者都在 window 冒泡阶段监听且不拦截事件，不影响外层的点击与快捷键。
export function useDismissOnOutside(ref: RefObject<HTMLElement | null>, open: boolean, onDismiss: () => void): void {
  const dismissRef = useLatestRef(onDismiss)

  useEscapeKey(onDismiss, { capture: false, enabled: open, preventDefault: false, stopPropagation: false })

  useEffect(() => {
    if (!open) {
      return
    }

    const onPointerDown = (event: PointerEvent): void => {
      if (!ref.current?.contains(event.target as Node | null)) {
        dismissRef.current()
      }
    }

    window.addEventListener('pointerdown', onPointerDown)

    return () => window.removeEventListener('pointerdown', onPointerDown)
  }, [dismissRef, open, ref])
}
