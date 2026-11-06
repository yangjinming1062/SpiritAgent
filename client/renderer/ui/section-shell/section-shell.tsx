import type React from 'react'
import { useLayoutEffect, useRef } from 'react'

import type { IconComponent } from '@/shared/lib/icons'

import styles from './section-shell.module.css'

export interface SectionEntry<T extends string> {
  id: T
  icon: IconComponent
  label: string
  unread?: boolean
}

// 一级页面的分区外壳：宽屏左侧分区栏、窄屏顶部按钮；只提供页签时不渲染侧栏。
// 每个分区各自记住滚动位置；内容自管滚动（bounded）或由壳滚动（默认）由页面决定。
export function SectionShell<T extends string>({
  activeId,
  ariaLabel,
  bounded = false,
  children,
  entries,
  onSelect,
  tabs
}: {
  activeId: T
  ariaLabel: string
  bounded?: boolean
  children: React.ReactNode
  entries?: ReadonlyArray<SectionEntry<T>>
  onSelect?: (id: T) => void
  tabs?: React.ReactNode
}): React.JSX.Element {
  const scroller = useRef<HTMLDivElement>(null)
  const directory = useRef<HTMLElement>(null)
  const positions = useRef<Partial<Record<T, number>>>({})

  useLayoutEffect(() => {
    const element = scroller.current
    const savedPositions = positions.current

    if (element) {
      element.scrollTop = savedPositions[activeId] ?? 0
    }

    const navigation = directory.current

    const revealSelection = (): void => {
      if (navigation && navigation.scrollWidth > navigation.clientWidth) {
        navigation.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
      }
    }

    const observer = new ResizeObserver(revealSelection)

    if (navigation) {
      observer.observe(navigation)
    }

    revealSelection()

    return () => {
      observer.disconnect()

      if (element) {
        savedPositions[activeId] = element.scrollTop
      }
    }
  }, [activeId])

  return (
    <div className={styles.layout}>
      <div className={styles.shell}>
        {entries && entries.length > 0 && (
          <nav aria-label={ariaLabel} className={styles.directory} data-section-navigation ref={directory}>
            {entries.map(({ id, icon: Icon, label, unread }) => (
              <button
                aria-current={activeId === id ? 'page' : undefined}
                className={styles.category}
                key={id}
                onClick={() => onSelect?.(id)}
                type="button"
              >
                <Icon aria-hidden="true" size={18} />
                <span className={styles.label}>{label}</span>
                {unread ? <span aria-hidden="true" className={styles.unreadDot} /> : null}
              </button>
            ))}
          </nav>
        )}
        <div className={styles.contentColumn}>
          {tabs ? <div className={styles.tabs}>{tabs}</div> : null}
          <div className={bounded ? styles.bounded : styles.scroller} ref={scroller}>
            <div className={bounded ? styles.boundedContent : styles.content}>{children}</div>
          </div>
        </div>
      </div>
    </div>
  )
}
