import type React from 'react'
import { useRef } from 'react'

import { SurfaceCompanion } from '@/app/components/surface-companion/surface-companion'
import { useDialogFocus } from '@/shared/hooks/use-dialog-focus'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { X } from '@/shared/lib/icons'
import { BTN_ICON } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { LifeOverview } from './life-overview'
import styles from './life.module.css'

export function LifeArea({
  onClose,
  visible,
  onNavigate
}: {
  onClose: () => void
  visible: boolean
  onNavigate?: () => void
}): React.JSX.Element {
  const t = useStrings().life

  return (
    <aside aria-label={t.title} className={styles.lifeArea}>
      <header className={styles.areaHeading}>
        <span>{t.title}</span>
        <button aria-label={t.hide} className={BTN_ICON} onClick={onClose} type="button">
          <X size={16} />
        </button>
      </header>
      <SurfaceCompanion visible={visible} />
      <LifeOverview onNavigate={onNavigate} visible={visible} />
    </aside>
  )
}

export function LifeDrawer({
  onClose,
  visible,
  side
}: {
  onClose: () => void
  visible: boolean
  side: 'left' | 'right'
}): React.JSX.Element {
  const root = useRef<HTMLDivElement>(null)
  const t = useStrings().life
  useDialogFocus(root, { restoreFocus: true, scope: 'window' })
  useEscapeKey(onClose, { enabled: true })

  return (
    <div className={styles.drawerLayer}>
      <button aria-label={t.close} className={styles.drawerBackdrop} onClick={onClose} type="button" />
      <div aria-label={t.title} aria-modal="true" className={styles.drawer} data-side={side} ref={root} role="dialog">
        <LifeArea onClose={onClose} onNavigate={onClose} visible={visible} />
      </div>
    </div>
  )
}
