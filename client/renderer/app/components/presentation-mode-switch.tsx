import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { Monitor } from '@/shared/lib/icons'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import type { PresentationMode } from '@ipc/contracts'

export function usePresentationModeSwitch() {
  const state = useStore($presentation)
  const en = useStore($locale) === 'en'
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  const beginAsync = useAsyncGuard()

  const switchMode = async (mode: PresentationMode): Promise<void> => {
    const current = $presentation.get()

    if (pending.current || current.status === 'starting' || current.status === 'recovering') {
      return
    }

    pending.current = true
    const isLive = beginAsync()
    setBusy(true)

    try {
      await window.spiritagent.presentation.setMode(mode)
    } catch (error) {
      if (isLive()) {
        notifyError(error, en ? 'Desktop mode' : '桌面模式')
      }
    } finally {
      pending.current = false

      if (isLive()) {
        setBusy(false)
      }
    }
  }

  return { state, en, busy: busy || state.status === 'starting' || state.status === 'recovering', switchMode }
}

export function PresentationModeButton({ className }: { className?: string }): React.JSX.Element | null {
  const { state, en, busy, switchMode } = usePresentationModeSwitch()

  if (!state.supported || state.effectiveMode === 'desktop') {
    return null
  }

  return (
    <button
      className={`${className ?? ''} disabled:cursor-wait disabled:opacity-50`}
      disabled={busy}
      onClick={() => void switchMode('desktop')}
      title={en ? 'Switch to desktop mode' : '切换到桌面模式'}
      type="button"
    >
      <Monitor size={13} />
      <span>{en ? 'Desktop mode' : '桌面模式'}</span>
    </button>
  )
}
