import type { ReactNode } from 'react'

import { ArrowLeft } from '@/shared/lib/icons'
import { BTN_SUBTLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import styles from './desktop-life.module.css'

export function DesktopLifeHeader({
  title,
  onBack,
  children
}: {
  title: string
  onBack?: () => void
  children?: ReactNode
}): React.JSX.Element {
  const strings = useStrings()

  return (
    <header className={styles.workspaceHeader}>
      <div className={styles.headerTitle}>
        {onBack && (
          <button className={`${BTN_SUBTLE} h-8 shrink-0 px-2`} onClick={onBack} type="button">
            <ArrowLeft className="size-4" />
            {strings.common.back}
          </button>
        )}
        <h2 className={styles.workspaceTitle}>{title}</h2>
      </div>
      {children && <div className={styles.headerActions}>{children}</div>}
    </header>
  )
}
