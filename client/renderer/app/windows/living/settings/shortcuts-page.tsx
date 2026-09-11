import { DEFAULT_SHORTCUTS, type DesktopShortcutsConfig, type DesktopShortcutsState } from '@ipc/contracts'
import type React from 'react'
import { useEffect, useState } from 'react'

import { RefreshCw, Sparkles } from '@/shared/lib/icons'
import { BTN_SUBTLE, HINT_TEXT, SettingCard, SettingsSectionIntro, ShortcutRecorder } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

const INITIAL_STATE: DesktopShortcutsState = {
  config: { ...DEFAULT_SHORTCUTS },
  status: {
    openLiving: { registered: false },
    openWorkbench: { registered: false },
    toggleVisibility: { registered: false }
  }
}

const SHORTCUT_ROWS: readonly {
  descKey: 'openLivingDesc' | 'openWorkbenchDesc' | 'toggleVisibilityDesc'
  id: keyof DesktopShortcutsConfig
  labelKey: 'openLiving' | 'openWorkbench' | 'toggleVisibility'
}[] = [
  { descKey: 'toggleVisibilityDesc', id: 'toggleVisibility', labelKey: 'toggleVisibility' },
  { descKey: 'openLivingDesc', id: 'openLiving', labelKey: 'openLiving' },
  { descKey: 'openWorkbenchDesc', id: 'openWorkbench', labelKey: 'openWorkbench' }
]

export function ShortcutsPage(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.settings.shortcuts
  const [state, setState] = useState<DesktopShortcutsState>(INITIAL_STATE)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true

    void window.spiritagent.shortcuts
      ?.get()
      .then(res => {
        if (active && res) {
          setState(res)
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false)
        }
      })

    const off = window.spiritagent.shortcuts?.onChanged?.(next => {
      if (active && next) {
        setState(next)
      }
    })

    return () => {
      active = false
      off?.()
    }
  }, [])

  const handleChange = async (key: keyof DesktopShortcutsConfig, value: string): Promise<void> => {
    try {
      const res = await window.spiritagent.shortcuts.set({
        shortcuts: { [key]: value }
      })

      if (res) {
        setState(res)
      }
    } catch {
      // 保持当前状态；UI 会在下一次推送时对齐
    }
  }

  const handleResetAll = async (): Promise<void> => {
    try {
      const res = await window.spiritagent.shortcuts.set({
        shortcuts: { ...DEFAULT_SHORTCUTS }
      })

      if (res) {
        setState(res)
      }
    } catch {
      // 保持当前状态
    }
  }

  const isAllDefault = SHORTCUT_ROWS.every(({ id }) => state.config[id] === DEFAULT_SHORTCUTS[id])

  return (
    <div className="space-y-4">
      <SettingsSectionIntro hint={t.intro} title={t.heading} />
      <SettingCard>
        {SHORTCUT_ROWS.map(({ descKey, id, labelKey }) => (
          <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between" key={id}>
            <div className="min-w-0">
              <div className="text-[13px] font-medium text-strong">{t[labelKey]}</div>
              <div className="mt-0.5 text-[11px] leading-relaxed text-muted">{t[descKey]}</div>
            </div>
            <ShortcutRecorder
              defaultValue={DEFAULT_SHORTCUTS[id]}
              disabled={loading}
              error={state.status[id]?.error}
              onChange={val => void handleChange(id, val)}
              registered={state.status[id]?.registered}
              value={state.config[id]}
            />
          </div>
        ))}
      </SettingCard>

      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-1">
        <div className="flex items-center gap-1.5 text-faint">
          <Sparkles className="size-3.5 shrink-0 text-accent" />
          <p className={HINT_TEXT}>{t.pressKeysHint}</p>
        </div>

        {!isAllDefault && (
          <button className={BTN_SUBTLE} disabled={loading} onClick={() => void handleResetAll()} type="button">
            <RefreshCw className="size-3.5" />
            <span>{t.resetAll}</span>
          </button>
        )}
      </div>
    </div>
  )
}
