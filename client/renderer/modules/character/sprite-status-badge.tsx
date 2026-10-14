import { useStore } from '@nanostores/react'
import type React from 'react'

import { useStrings } from '@/shared/strings'

import { $spriteState } from './companion-store'
import styles from './sprite-status-badge.module.css'

export function SpriteStatusBadge({ compact = false }: { compact?: boolean }): React.JSX.Element {
  const spriteState = useStore($spriteState)
  const t = useStrings()

  const busy = spriteState === 'thinking' || spriteState === 'working'
  const label = busy ? t.companion.statusBusy : t.companion.statusCompanion

  if (compact) {
    return (
      <span aria-label={label} className={styles.indicator} data-busy={busy} role="status" title={label}>
        <span aria-hidden="true" className={styles.dot} />
      </span>
    )
  }

  return (
    <div className={styles.root}>
      <span aria-hidden="true" className={styles.dot} />
      <span>{label}</span>
    </div>
  )
}
