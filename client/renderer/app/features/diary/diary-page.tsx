import { useStore } from '@nanostores/react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type React from 'react'

import { ReadingPage } from '@/app/components/reading-page'
import { $persona } from '@/modules/character'
import { $diaryByDate, $diaryLoading, hydrateDiary, markDiaryRead } from '@/modules/diary'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { ArrowLeft, ArrowRight, BookOpen } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_ICON, BTN_SUBTLE } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { type Dictionary, useStrings } from '@/shared/strings'

import { useReadingForeground } from '../use-reading-foreground'

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

export function DiaryPage({ foreground: suppliedForeground }: { foreground?: boolean } = {}): React.JSX.Element {
  const persona = useStore($persona)
  const diaryByDate = useStore($diaryByDate)
  const loading = useStore($diaryLoading)
  const auth = useStore($auth)
  const authKind = auth.kind
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const dict = useStrings()
  const locale = useStore($locale)
  const calendarHeadingId = useId()
  const t = dict.living.diary
  const tRail = dict.living.rail
  const [selectedDate, setSelectedDate] = useState<string>(todayKey())
  const [cursor, setCursor] = useState<Date>(new Date())
  const [loadFailed, setLoadFailed] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [readSnapshot, setReadSnapshot] = useState<string[] | null>(null)
  const [readBlocked, setReadBlocked] = useState(false)
  const [confirmedReadIds, setConfirmedReadIds] = useState(new Set<string>())

  const readRequest = useRef<Promise<boolean> | undefined>(undefined)
  const snapshotCaptured = useRef(false)
  const beginAsync = useAsyncGuard()

  const foreground = useReadingForeground(suppliedForeground)
  const wasForeground = useRef(foreground)

  useEffect(() => {
    readRequest.current = undefined
    snapshotCaptured.current = false
    setReadSnapshot(null)
    setReadBlocked(false)
    setConfirmedReadIds(new Set())
  }, [sessionId, reloadKey])

  useEffect(
    () =>
      $gatewayState.listen(state => {
        if (state === 'open') {
          setReloadKey(key => key + 1)
        }
      }),
    []
  )

  useEffect(() => {
    const restored = foreground && !wasForeground.current
    wasForeground.current = foreground

    if (restored) {
      setReadBlocked(false)

      if (loadFailed) {
        setReloadKey(key => key + 1)
      }
    }
  }, [foreground, loadFailed])

  const displayName = persona?.name || tRail.companionFallback
  const isToday = selectedDate === todayKey()

  // 月份翻阅只加载正文，首轮成功加载捕获进入页面时的未读快照。
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

    void hydrateDiary({ from: startKey, to: endKey }).then(snapshot => {
      if (!cancelled) {
        setLoadFailed(snapshot === null)

        if (snapshot !== null && !snapshotCaptured.current) {
          snapshotCaptured.current = true
          setReadSnapshot(snapshot)
        }
      }
    })

    return () => {
      cancelled = true
    }
  }, [cursor, authKind, sessionId, reloadKey])

  const days = useMemo(() => daysInMonth(cursor), [cursor])
  const firstDayOffset = days[0] ? (days[0].getDay() + 6) % 7 : 0
  const entry = diaryByDate[selectedDate]

  useEffect(() => {
    if (
      authKind !== 'authenticated' ||
      !foreground ||
      loading ||
      loadFailed ||
      !snapshotCaptured.current ||
      readSnapshot === null ||
      readBlocked ||
      readRequest.current !== undefined ||
      document.visibilityState !== 'visible' ||
      !document.hasFocus()
    ) {
      return
    }

    // 进入前的提醒统一确认；进入后发布的内容只确认当前实际展示的正文。
    const ids = [...new Set([...readSnapshot, ...(entry ? [entry.id] : [])])].filter(id => !confirmedReadIds.has(id))

    if (ids.length === 0) {
      return
    }

    const isLive = beginAsync()
    const request = markDiaryRead(ids)
    readRequest.current = request
    void request.then(ok => {
      if (!isLive() || readRequest.current !== request) {
        return
      }

      readRequest.current = undefined

      if (ok) {
        setConfirmedReadIds(current => new Set([...current, ...ids]))
      } else {
        setReadBlocked(true)
      }
    })
  }, [authKind, foreground, loading, loadFailed, readSnapshot, readBlocked, entry, confirmedReadIds, beginAsync])

  return (
    <ReadingPage hint={t.intro} title={tRail.diary}>
      <div className={styles.shell}>
        <aside aria-labelledby={calendarHeadingId} className={cn('liquid-glass-card', styles.calendar)}>
          <div className={styles.calendarHeader}>
            <button
              aria-label={t.previousMonth}
              className={BTN_ICON}
              onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
              type="button"
            >
              <ArrowLeft aria-hidden="true" size={16} />
            </button>
            <h3 className={styles.monthLabel} id={calendarHeadingId}>
              {new Intl.DateTimeFormat(locale, { year: 'numeric', month: 'long' }).format(cursor)}
            </h3>
            <button
              aria-label={t.nextMonth}
              className={BTN_ICON}
              onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
              type="button"
            >
              <ArrowRight aria-hidden="true" size={16} />
            </button>
          </div>

          <div className={styles.weekHeader}>
            {t.weekHeader.map((d, index) => (
              <span className={styles.weekDay} key={t.weekDayNames[(index + 1) % 7]}>
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
                  aria-current={key === todayKey() ? 'date' : undefined}
                  aria-label={formatSelectedDate(key, t)}
                  aria-pressed={selected}
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

        <article className={cn('liquid-glass-card', styles.entry)}>
          <header className={styles.entryHeader}>
            <h3 className={styles.entryDate}>{formatSelectedDate(selectedDate, t)}</h3>
            {isToday && <span className={styles.todayBadge}>{t.todayBadge}</span>}
          </header>

          {loading ? (
            <p className={styles.loading}>{t.loading}</p>
          ) : entry ? (
            <div className={styles.contentArea}>
              {entry.title ? <h4 className={styles.entryTitle}>{entry.title}</h4> : null}
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
        </article>
      </div>
    </ReadingPage>
  )
}
