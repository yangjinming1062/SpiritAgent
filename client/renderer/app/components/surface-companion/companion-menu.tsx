import type { SurfaceCompanionPreference, SurfaceId } from '@ipc/contracts'
import { useStore } from '@nanostores/react'
import type React from 'react'
import { useLayoutEffect, useRef, useState } from 'react'

import { useInteractiveRegion } from '@/shared'
import { useDismissOnOutside } from '@/shared/hooks/use-dismiss-on-outside'
import { probeInteractiveRegions } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
import { $surfaceCompanions, setSurfaceCompanion } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import styles from './companion-menu.module.css'

export function CompanionMenu({ surface }: { surface: SurfaceId }): React.JSX.Element {
  const state = useStore($surfaceCompanions)[surface]
  const strings = useStrings().common.companionControl
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('surface-companion-menu', rootRef, undefined, undefined, 1)
  useInteractiveRegion('surface-companion-menu-popup', menuRef, undefined, undefined, 1)

  useLayoutEffect(() => {
    probeInteractiveRegions(1)
  }, [open])

  useDismissOnOutside(rootRef, open, () => setOpen(false))

  const change = async (preference: SurfaceCompanionPreference): Promise<void> => {
    if (savingRef.current) {
      return
    }

    savingRef.current = true
    setSaving(true)

    try {
      await setSurfaceCompanion(preference)
      setError(false)
      setOpen(false)
    } catch (cause) {
      log.warn('companion-menu', 'Could not save companion preference', cause)
      setError(true)
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }

  const notice =
    state.hiddenReason === 'maximized'
      ? strings.temporaryMaximized
      : state.hiddenReason === 'edge'
        ? strings.temporaryEdge
        : null

  return (
    <div className={styles.root} onDoubleClick={event => event.stopPropagation()} ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        className={styles.trigger}
        onClick={() => setOpen(value => !value)}
        title={strings.title}
        type="button"
      >
        {strings.title}
      </button>
      {open && (
        <div className={styles.menu} ref={menuRef} role="menu">
          <button
            aria-checked={state.preference.enabled}
            className={styles.item}
            disabled={saving}
            onClick={() => void change({ ...state.preference, enabled: !state.preference.enabled })}
            role="menuitemcheckbox"
            type="button"
          >
            <span className={styles.mark}>{state.preference.enabled ? '✓' : ''}</span>
            {strings.show}
          </button>
          <div className={styles.separator} />
          {(['left', 'right'] as const).map(side => (
            <button
              aria-checked={state.preference.side === side}
              className={styles.item}
              disabled={saving}
              key={side}
              onClick={() => void change({ ...state.preference, side })}
              role="menuitemradio"
              type="button"
            >
              <span className={styles.mark}>{state.preference.side === side ? '✓' : ''}</span>
              {side === 'left' ? strings.left : strings.right}
            </button>
          ))}
          {notice && <p className={styles.notice}>{notice}</p>}
          {error && (
            <p className={styles.error} role="alert">
              {strings.saveFailed}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
