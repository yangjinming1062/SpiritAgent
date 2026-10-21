import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { Monitor } from '@/shared/lib/icons'
import { shellApi } from '@/shared/platform/desktop'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { useStrings } from '@/shared/strings'
import type { PresentationMode } from '@ipc/contracts'

export function usePresentationModeSwitch() {
  const state = useStore($presentation)
  const strings = useStrings()
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  const beginAsync = useAsyncGuard()

  const switchMode = async (mode: PresentationMode): Promise<void> => {
    const current = $presentation.get()

    if (
      pending.current ||
      current.status === 'starting' ||
      current.status === 'recovering' ||
      current.switchBlockedReason
    ) {
      return
    }

    pending.current = true
    const isLive = beginAsync()
    setBusy(true)

    try {
      await shellApi().presentation.setMode(mode)
    } catch (error) {
      if (isLive()) {
        notifyError(error, strings.desktopShell.switchFailed)
      }
    } finally {
      pending.current = false

      if (isLive()) {
        setBusy(false)
      }
    }
  }

  return {
    state,
    busy: busy || state.status === 'starting' || state.status === 'recovering' || Boolean(state.switchBlockedReason),
    switchMode
  }
}

export function PresentationModeButton({ className }: { className?: string }): React.JSX.Element | null {
  const strings = useStrings()
  const { state, busy, switchMode } = usePresentationModeSwitch()

  if (!state.supported || state.effectiveMode === 'desktop') {
    return null
  }

  return (
    <button
      className={`${className ?? ''} disabled:cursor-wait disabled:opacity-50`}
      disabled={busy}
      onClick={() => void switchMode('desktop')}
      title={state.switchBlockedReason ?? strings.desktopShell.switchToDesktopMode}
      type="button"
    >
      <Monitor size={13} />
      <span>{strings.desktopShell.desktopMode}</span>
    </button>
  )
}
