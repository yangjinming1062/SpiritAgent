import type React from 'react'
import { useLayoutEffect, useRef } from 'react'

import { SettingsSectionIntro } from '@/shared/panel'

import styles from './reading-page.module.css'

export function ReadingPage({
  children,
  hint,
  title,
  scrollTop,
  onScroll,
  restoreReady = true
}: {
  children: React.ReactNode
  hint: string
  title: string
  scrollTop?: number
  onScroll?: (top: number) => void
  restoreReady?: boolean
}): React.JSX.Element {
  const scroller = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (restoreReady && scrollTop !== undefined && scroller.current) {
      scroller.current.scrollTop = scrollTop
    }
  }, [scrollTop, restoreReady])

  return (
    <section className={styles.layout}>
      <div
        className={styles.scroller}
        onScroll={event => {
          if (restoreReady) {
            onScroll?.(event.currentTarget.scrollTop)
          }
        }}
        ref={scroller}
      >
        <div className={styles.content}>
          <SettingsSectionIntro hint={hint} title={title} />
          {children}
        </div>
      </div>
    </section>
  )
}
