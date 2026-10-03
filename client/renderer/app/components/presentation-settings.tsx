import type { PresentationMode } from '@ipc/contracts'
import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { Monitor } from '@/shared/lib/icons'
import { SettingCard, SettingRow } from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'

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
        notifyError(error, en ? 'Presentation' : '呈现方式')
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

export function PresentationSettings(): React.JSX.Element {
  const { state, en, busy, switchMode } = usePresentationModeSwitch()

  return (
    <SettingCard>
      <SettingRow
        description={
          en
            ? 'Desktop combines conversations, whisper and your companion.'
            : '桌面模式整合对话、轻语与伙伴，提供完整桌面体验。'
        }
        label={en ? 'Presentation' : '呈现方式'}
      >
        <select
          aria-label={en ? 'Presentation' : '呈现方式'}
          className="rounded-lg border border-line-standard bg-surface-card px-3 py-1.5 text-xs text-body"
          disabled={busy}
          onChange={event => {
            const mode = event.target.value === 'desktop' ? 'desktop' : 'window'
            void switchMode(mode)
          }}
          value={state.status === 'starting' ? state.requestedMode : state.effectiveMode}
        >
          <option value="window">{en ? 'Window' : '窗口'}</option>
          <option disabled={!state.supported} value="desktop">
            {en ? 'Desktop (Windows)' : '桌面（Windows）'}
          </option>
        </select>
      </SettingRow>
      {state.status === 'failed' && state.requestedMode === 'desktop' && state.supported && (
        <button
          className="mx-4 mb-3 rounded-lg border border-line-standard px-3 py-1.5 text-xs"
          disabled={busy}
          onClick={() => void switchMode('desktop')}
          type="button"
        >
          {en ? 'Retry desktop' : '重试桌面模式'}
        </button>
      )}
      {state.failureReason && (
        <p className="px-4 pb-3 text-xs text-danger-fg" role="status">
          {state.failureReason}
        </p>
      )}
    </SettingCard>
  )
}
