import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { shellApi } from '@/shared/platform/desktop'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { useStrings } from '@/shared/strings'

export function AccessibilityPermissionButton({
  className,
  compact = false
}: {
  className?: string
  compact?: boolean
}): React.JSX.Element | null {
  const state = useStore($presentation)
  const t = useStrings().desktopShell
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  const beginAsync = useAsyncGuard()

  if (state.accessibility.granted || !state.accessibility.requestable) {
    return null
  }

  const request = async (): Promise<void> => {
    if (pending.current) {
      return
    }

    pending.current = true
    const isLive = beginAsync()
    setBusy(true)

    try {
      await shellApi().presentation.requestAccessibility()
    } catch (error) {
      if (isLive()) {
        notifyError(error, t.accessibilityRequest)
      }
    } finally {
      pending.current = false

      if (isLive()) {
        setBusy(false)
      }
    }
  }

  return (
    <button
      aria-label={t.accessibilityRequest}
      className={className}
      data-permission-request
      disabled={busy}
      onClick={() => void request()}
      title={t.accessibilityDescription}
      type="button"
    >
      {compact ? '!' : t.accessibilityRequest}
    </button>
  )
}
