import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { $surfaceScreenLocked } from '@/shared/store/surfaces'

/** 阅读页的前台资格：调用方给出的页面级前景、未锁屏且文档处于活动状态。 */
export function useReadingForeground(foreground: boolean): boolean {
  const screenLocked = useStore($surfaceScreenLocked)

  const [documentActive, setDocumentActive] = useState(
    () => document.visibilityState === 'visible' && document.hasFocus()
  )

  useEffect(() => {
    const update = (): void => {
      setDocumentActive(document.visibilityState === 'visible' && document.hasFocus())
    }

    update()
    window.addEventListener('focus', update)
    window.addEventListener('blur', update)
    document.addEventListener('visibilitychange', update)

    return () => {
      window.removeEventListener('focus', update)
      window.removeEventListener('blur', update)
      document.removeEventListener('visibilitychange', update)
    }
  }, [])

  return foreground && !screenLocked && documentActive
}

// 仅在后台转前台的边沿触发一次；回调经渲染期同步的 ref 取最新闭包。
export function useForegroundRestored(foreground: boolean, onRestored: () => void): void {
  const wasForeground = useRef(foreground)
  const onRestoredRef = useRef(onRestored)
  onRestoredRef.current = onRestored

  useEffect(() => {
    const restored = foreground && !wasForeground.current
    wasForeground.current = foreground

    if (restored) {
      onRestoredRef.current()
    }
  }, [foreground])
}
