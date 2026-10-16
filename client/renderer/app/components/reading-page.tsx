import type React from 'react'

import { SettingsSectionIntro } from '@/shared/panel'

import styles from './reading-page.module.css'

export function ReadingPage({
  children,
  hint,
  title
}: {
  children: React.ReactNode
  hint: string
  title: string
}): React.JSX.Element {
  return (
    <section className={styles.layout}>
      <div className={styles.scroller}>
        <div className={styles.content}>
          <SettingsSectionIntro hint={hint} title={title} />
          {children}
        </div>
      </div>
    </section>
  )
}
