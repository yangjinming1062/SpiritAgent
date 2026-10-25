import type React from 'react'
import { useEffect, useState } from 'react'

import { InlineMedia } from '@/modules/media'
import {
  hydratePosts,
  listVideoPublications,
  queryVideoPublication,
  resolveVideoPublication,
  type VideoPublicationRecovery
} from '@/modules/posts'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { cn } from '@/shared/lib/utils'
import { ActionRow, BTN_SUBTLE, ConfirmDialog } from '@/shared/panel'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import styles from './posts.module.css'

export function PostVideoRecovery(): React.JSX.Element | null {
  const t = useStrings().living.posts
  const [items, setItems] = useState<VideoPublicationRecovery[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [discardId, setDiscardId] = useState<string | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const beginAsync = useAsyncGuard()

  useEffect(() => {
    let cancelled = false
    const isLive = beginAsync()
    setItems([])
    setNextCursor(null)
    setBusy(null)
    setDiscardId(null)
    setLoadFailed(false)
    void listVideoPublications().then(result => {
      if (cancelled || !isLive()) {
        return
      }

      setLoadFailed(result === null)

      if (result) {
        setItems(result.items)
        setNextCursor(result.next_cursor)
      }
    })

    return () => {
      cancelled = true
    }
  }, [refreshKey, beginAsync])

  const query = async (id: string): Promise<void> => {
    const isLive = beginAsync()
    setBusy(id)
    const result = await queryVideoPublication(id)

    if (!isLive()) {
      return
    }

    setBusy(null)

    if (result) {
      if (['published', 'partial', 'failed', 'blocked'].includes(result.status)) {
        setItems(current => current.filter(item => item.publication_id !== id))
        await hydratePosts()
      } else {
        setItems(current => current.map(item => (item.publication_id === id ? result : item)))
      }
    } else {
      notify({ kind: 'error', message: t.videoQueryFailed, title: t.videoQueryFailed })
    }
  }

  const resolve = async (id: string): Promise<void> => {
    const isLive = beginAsync()
    setBusy(id)
    const ok = await resolveVideoPublication(id)

    if (!isLive()) {
      return
    }

    setBusy(null)
    setDiscardId(null)

    if (ok) {
      setItems(current => current.filter(item => item.publication_id !== id))
    } else {
      notify({
        kind: 'error',
        message: t.videoDiscardFailed,
        title: t.videoDiscardFailed
      })
    }
  }

  const loadMore = async (): Promise<void> => {
    if (nextCursor === null) {
      return
    }

    const isLive = beginAsync()
    setBusy('more')
    const result = await listVideoPublications(nextCursor)

    if (!isLive()) {
      return
    }

    setBusy(null)

    if (result) {
      setItems(current => [
        ...current,
        ...result.items.filter(item => !current.some(existing => existing.publication_id === item.publication_id))
      ])
      setNextCursor(result.next_cursor)
    } else {
      notify({ kind: 'error', message: t.loadFailed, title: t.loadFailed })
    }
  }

  if (loadFailed) {
    return (
      <div className={cn('liquid-glass-card', styles.card)}>
        <p className={styles.body}>{t.videoRecoveryLoadFailed}</p>
        <button className={BTN_SUBTLE} onClick={() => setRefreshKey(current => current + 1)} type="button">
          {t.videoRefresh}
        </button>
      </div>
    )
  }

  if (!items.length) {
    return null
  }

  return (
    <section className={cn('liquid-glass-card', styles.card)}>
      <div className="flex items-center justify-between gap-2">
        <h3 className={styles.title}>{t.videoRecoveryHeading}</h3>
        <button
          className={BTN_SUBTLE}
          disabled={busy !== null}
          onClick={() => setRefreshKey(current => current + 1)}
          type="button"
        >
          {t.videoRefresh}
        </button>
      </div>
      <p className={styles.body}>{t.videoRecoveryHint}</p>
      {items.map(item => (
        <div className="space-y-2 border-t border-line-standard pt-3" key={item.publication_id}>
          <p className={styles.title}>{item.title}</p>
          <p className={styles.body}>
            {item.status === 'discarded' && item.can_discard
              ? t.videoCleanupPending
              : t.videoRecoveryStatuses[item.video_status]}
          </p>
          {item.error && <p className={styles.body}>{item.error}</p>}
          {item.media_url && <InlineMedia alt={item.title} audioUrl={null} mediaType="video" url={item.media_url} />}
          <ActionRow
            actions={[
              {
                className: BTN_SUBTLE,
                disabled: busy !== null,
                label: t.videoQuery,
                onClick: () => void query(item.publication_id)
              },
              {
                className: BTN_SUBTLE,
                disabled: busy !== null || !item.can_discard,
                label: item.status === 'discarded' ? t.videoRetryCleanup : t.videoDiscard,
                onClick: () => setDiscardId(item.publication_id)
              }
            ]}
          />
        </div>
      ))}
      {nextCursor !== null && (
        <button className={BTN_SUBTLE} disabled={busy !== null} onClick={() => void loadMore()} type="button">
          {t.loadMore}
        </button>
      )}
      <ConfirmDialog
        confirmLabel={t.videoDiscard}
        description={t.videoDiscardDescription}
        onConfirm={() => {
          if (discardId) {
            void resolve(discardId)
          }
        }}
        onOpenChange={open => {
          if (!open) {
            setDiscardId(null)
          }
        }}
        open={discardId !== null}
        title={t.videoDiscardTitle}
      />
    </section>
  )
}
