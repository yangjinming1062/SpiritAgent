import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { $surfaceOpen, $surfaceOpenVisible, $surfaceScreenLocked } from '@/shared/store/surfaces'

export function useReadingForeground(suppliedForeground?: boolean): boolean {
  const surfaceOpen = useStore($surfaceOpen)
  const surfaceVisible = useStore($surfaceOpenVisible)
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

  return (suppliedForeground ?? (surfaceOpen && surfaceVisible)) && !screenLocked && documentActive
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
