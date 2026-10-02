import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { SettingCard, SettingRow } from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'

export function PresentationSettings(): React.JSX.Element {
  const state = useStore($presentation)
  const en = useStore($locale) === 'en'
  const [busy, setBusy] = useState(false)

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
            setBusy(true)
            void window.spiritagent.presentation
              .setMode(mode)
              .catch(error => notifyError(error, en ? 'Presentation' : '呈现方式'))
              .finally(() => setBusy(false))
          }}
          value={state.requestedMode}
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
          onClick={() => {
            setBusy(true)
            void window.spiritagent.presentation
              .setMode('desktop')
              .catch(error => notifyError(error, en ? 'Presentation' : '呈现方式'))
              .finally(() => setBusy(false))
          }}
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
