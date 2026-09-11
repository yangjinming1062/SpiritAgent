import { useStore } from '@nanostores/react'
import type React from 'react'

import { useStrings } from '@/shared/strings'

import { $spriteState } from './companion-store'
import styles from './sprite-status-badge.module.css'

export function SpriteStatusBadge(): React.JSX.Element {
  const spriteState = useStore($spriteState)
  const t = useStrings()

  const label =
    spriteState === 'thinking' || spriteState === 'working' ? t.companion.statusBusy : t.companion.statusCompanion

  return (
    <div className={styles.root}>
      <span aria-hidden="true" className={styles.dot} />
      <span>{label}</span>
    </div>
  )
}
