import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

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
