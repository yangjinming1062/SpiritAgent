// 日记页：左月历（带点）+ 右当天日记正文（精灵编写，只读浏览）+ 心情徽标 + 伙伴署名。

import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'
import type React from 'react'

import { $persona } from '@/modules/character'
import { $diaryByDate, $diaryLoading, hydrateDiary } from '@/modules/memory'
import { BookOpen } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_SUBTLE } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { type Dictionary, useStrings } from '@/shared/strings'

import styles from './diary.module.css'

function localDateKey(d: Date): string {
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')

  return `${year}-${month}-${day}`
}

function todayKey(): string {
  return localDateKey(new Date())
}

function monthKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

function daysInMonth(date: Date): Date[] {
  const year = date.getFullYear()
  const month = date.getMonth()
  const count = new Date(year, month + 1, 0).getDate()

  return Array.from({ length: count }, (_, i) => new Date(year, month, i + 1))
}

function cursorMonthStart(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}

function formatSelectedDate(dateStr: string, t: Dictionary['living']['diary']): string {
  const parts = dateStr.split('-').map(Number)

  if (parts.length !== 3 || parts.some(isNaN)) {
    return dateStr
  }

  const [y, m, d] = parts
  const date = new Date(y, m - 1, d)
  const weekDay = t.weekDayNames[date.getDay()]

  return weekDay ? t.dateFormat(dateStr, weekDay) : dateStr
}

export function DiaryPage(): React.JSX.Element {
  const persona = useStore($persona)
  const diaryByDate = useStore($diaryByDate)
  const loading = useStore($diaryLoading)
  const authKind = useStore($auth).kind
  const dict = useStrings()
  const t = dict.living.diary
  const tRail = dict.living.rail
  const [selectedDate, setSelectedDate] = useState<string>(todayKey())
  const [cursor, setCursor] = useState<Date>(new Date())
  const [loadFailed, setLoadFailed] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const displayName = persona?.name || tRail.companionFallback
  const isToday = selectedDate === todayKey()

  // 月份切换时若选中日期超出当月范围则吸到该月首日，同时拉取当月数据。冷启动默认视图可能是日记（hash/localStorage 持久化），hydrateAuth 的 IPC 往返未完成时 authedApi 会以 unauth 静默跳过——等 auth 就绪再水合（authKind 变化重跑）。
  useEffect(() => {
    const cursorStart = cursorMonthStart(cursor)
    const cursorEnd = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0)
    const startKey = localDateKey(cursorStart)
    const endKey = localDateKey(cursorEnd)

    setSelectedDate(prev => (prev < startKey || prev > endKey ? startKey : prev))

    if (authKind !== 'authenticated') {
      return
    }

    let cancelled = false
    setLoadFailed(false)

    void hydrateDiary({ from: startKey, to: endKey }).then(ok => {
      if (!cancelled) {
        setLoadFailed(!ok)
      }
    })

    return () => {
      cancelled = true
    }
  }, [cursor, authKind, reloadKey])

  const days = useMemo(() => daysInMonth(cursor), [cursor])
  const firstDayOffset = days[0] ? (days[0].getDay() + 6) % 7 : 0
  const entry = diaryByDate[selectedDate]

  return (
    <div className={styles.shell}>
      <aside className={styles.calendar}>
        <div className={styles.calendarHeader}>
          <button
            className={styles.monthButton}
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
            type="button"
          >
            ←
          </button>
          <span className={styles.monthLabel}>{monthKey(cursor)}</span>
          <button
            className={styles.monthButton}
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
            type="button"
          >
            →
          </button>
        </div>

        <div className={styles.weekHeader}>
          {t.weekHeader.map(d => (
            <span className={styles.weekDay} key={d}>
              {d}
            </span>
          ))}
        </div>

        <div className={styles.grid}>
          {days.map((d, index) => {
            const key = localDateKey(d)
            const hasEntry = Boolean(diaryByDate[key])
            const selected = key === selectedDate

            return (
              <button
                className={cn(styles.dayCell, selected && styles.dayCellSelected, hasEntry && styles.dayCellHasEntry)}
                key={key}
                onClick={() => setSelectedDate(key)}
                style={index === 0 && firstDayOffset > 0 ? { gridColumnStart: firstDayOffset + 1 } : undefined}
                type="button"
              >
                {d.getDate()}
                {hasEntry && <span className={styles.dot} />}
              </button>
            )
          })}
        </div>
      </aside>

      <main className={styles.entry}>
        <header className={styles.entryHeader}>
          <h2 className={styles.entryDate}>{formatSelectedDate(selectedDate, t)}</h2>
          {isToday && <span className={styles.todayBadge}>{t.todayBadge}</span>}
          {entry?.mood && <span className={styles.mood}>{t.mood(entry.mood)}</span>}
        </header>

        {loading ? (
          <p className={styles.loading}>{t.loading}</p>
        ) : entry ? (
          <div className={styles.contentArea}>
            {entry.title ? <h3 className={styles.entryTitle}>{entry.title}</h3> : null}
            <p className={styles.bodyText}>{entry.body}</p>
            <div className={styles.signature}>{t.signature(displayName)}</div>
          </div>
        ) : loadFailed ? (
          <div className={styles.emptyContainer}>
            <p className={styles.emptyTitle}>{t.loadFailed}</p>
            <button className={cn(BTN_SUBTLE, 'mt-2')} onClick={() => setReloadKey(key => key + 1)} type="button">
              {dict.common.retry}
            </button>
          </div>
        ) : (
          <div className={styles.emptyContainer}>
            <BookOpen className={styles.emptyIcon} size={36} />
            <p className={styles.emptyTitle}>{t.emptyTitle}</p>
            <p className={styles.emptyHint}>{isToday ? t.emptyHintToday : t.emptyHintOther}</p>
          </div>
        )}
      </main>
    </div>
  )
}
