// 片刻页：时间线，新在上。后端直连；空态文案人格化。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useState } from 'react'

import { InlineMedia } from '@/modules/media'
import { $moments, $momentsLoading, hydrateMoments } from '@/modules/memory'
import { useStrings } from '@/shared/strings'

import styles from './moments.module.css'

const DATE_FORMATTER = new Intl.DateTimeFormat('zh-CN')

function formatDate(iso: string): string {
  try {
    return DATE_FORMATTER.format(new Date(iso))
  } catch {
    return iso
  }
}

export function MomentsPage(): React.JSX.Element {
  const moments = useStore($moments)
  const loading = useStore($momentsLoading)
  const t = useStrings().living.moments
  const [expandedId, setExpandedId] = useState<null | string>(null)

  useEffect(() => {
    void hydrateMoments()
  }, [])

  const formattedMoments = useMemo(
    () =>
      moments.map(m => ({
        ...m,
        displayDate: formatDate(m.createdAt)
      })),
    [moments]
  )

  const getKindLabel = (kind: string): string => t.kindLabels[kind] ?? t.kindFallback

  if (loading && moments.length === 0) {
    return <p className={styles.empty}>{t.loading}</p>
  }

  if (moments.length === 0) {
    return <p className={styles.empty}>{t.empty}</p>
  }

  return (
    <div className={styles.list}>
      {formattedMoments.map(m => {
        const expanded = expandedId === m.id

        return (
          <article className={styles.card} key={m.id}>
            <button className={styles.cardToggle} onClick={() => setExpandedId(expanded ? null : m.id)} type="button">
              <div className={styles.cardHeader}>
                <span className={styles.kindBadge}>{getKindLabel(m.kind)}</span>
                <time className={styles.date} dateTime={m.createdAt}>
                  {m.displayDate}
                </time>
              </div>
              <h3 className={styles.title}>{m.title ?? t.noTitle}</h3>
              {m.body && (
                <p className={`${styles.body} ${expanded ? styles.bodyExpanded : styles.bodyClamp}`}>{m.body}</p>
              )}
            </button>
            {m.mediaUrl ? (
              <InlineMedia alt={m.title ?? ''} audioUrl={m.audioUrl} mediaType={m.mediaType} url={m.mediaUrl} />
            ) : null}
          </article>
        )
      })}
    </div>
  )
}
