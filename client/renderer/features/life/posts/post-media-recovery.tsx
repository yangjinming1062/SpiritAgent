import type React from 'react'
import { useEffect, useState } from 'react'

import { InlineMedia } from '@/features/media'
import { MediaReviewButton } from '@/features/media-review'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import { gatewayApi } from '@/shared/platform/desktop'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import { ActionRow, BTN_SUBTLE, ConfirmDialog, Pager } from '@/ui'

import styles from './posts.module.css'
import {
  discardMediaPublication,
  listMediaPublications,
  type MediaPublicationRecovery,
  queryMediaPublication
} from './recovery'
import { hydratePosts } from './store'

export function PostMediaRecovery(): React.JSX.Element | null {
  const t = useStrings().living.posts
  const [items, setItems] = useState<MediaPublicationRecovery[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [discardId, setDiscardId] = useState<string | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const beginAsync = useAsyncGuard()
  useEffect(
    () =>
      gatewayApi().onGatewayEvent(({ event }) => {
        if (event.type === 'media.review.updated') {
          setRefreshKey(value => value + 1)
        }
      }),
    []
  )

  useEffect(() => {
    let cancelled = false
    const isLive = beginAsync()
    setItems([])
    setNextCursor(null)
    setBusy(null)
    setDiscardId(null)
    setLoadFailed(false)
    void listMediaPublications().then(result => {
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
    const result = await queryMediaPublication(id)

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
      notify({ kind: 'error', message: t.mediaQueryFailed, title: t.mediaQueryFailed })
    }
  }

  const publish = async (id: string): Promise<void> => {
    const isLive = beginAsync()
    setBusy(id)

    try {
      const job = await requestResource('media.get_media_job', { path: { job_id: id } })
      await requestResource('media.continue_media_business', {
        path: { job_id: id },
        body: { action: 'publish_post', expected_revision: job.revision }
      })

      if (isLive()) {
        setRefreshKey(value => value + 1)
      }
    } catch (cause) {
      if (isLive()) {
        notify({
          kind: 'error',
          title: t.mediaPublish,
          message: cause instanceof Error ? cause.message : String(cause)
        })
      }
    } finally {
      if (isLive()) {
        setBusy(null)
      }
    }
  }

  const resolve = async (id: string): Promise<void> => {
    const isLive = beginAsync()
    setBusy(id)
    const ok = await discardMediaPublication(id)

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
        message: t.mediaDiscardFailed,
        title: t.mediaDiscardFailed
      })
    }
  }

  const loadMore = async (): Promise<void> => {
    if (nextCursor === null) {
      return
    }

    const isLive = beginAsync()
    setBusy('more')
    const result = await listMediaPublications(nextCursor)

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
        <p className={styles.body}>{t.mediaRecoveryLoadFailed}</p>
        <button className={BTN_SUBTLE} onClick={() => setRefreshKey(current => current + 1)} type="button">
          {t.mediaRefresh}
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
        <h3 className={styles.title}>{t.mediaRecoveryHeading}</h3>
        <button
          className={BTN_SUBTLE}
          disabled={busy !== null}
          onClick={() => setRefreshKey(current => current + 1)}
          type="button"
        >
          {t.mediaRefresh}
        </button>
      </div>
      <p className={styles.body}>{t.mediaRecoveryHint}</p>
      {items.map(item => (
        <div className="space-y-2 border-t border-line-standard pt-3" key={item.publication_id}>
          <p className={styles.title}>{item.title}</p>
          <p className={styles.body}>
            {item.status === 'discarded' && item.can_discard
              ? t.mediaCleanupPending
              : t.mediaRecoveryStatuses[item.media_status]}
          </p>
          {item.error && <p className={styles.body}>{item.error}</p>}
          {item.media_url && (
            <InlineMedia alt={item.title} audioUrl={null} mediaType={item.media_kind} url={item.media_url} />
          )}
          {(item.media_status === 'review_pending' || item.media_status === 'failed') && item.media_job_id ? (
            <MediaReviewButton jobId={item.media_job_id} />
          ) : null}
          {item.can_publish ? (
            <button
              className={BTN_SUBTLE}
              disabled={busy !== null}
              onClick={() => void publish(item.publication_id)}
              type="button"
            >
              {t.mediaPublish}
            </button>
          ) : null}
          <ActionRow
            actions={[
              {
                className: BTN_SUBTLE,
                disabled: busy !== null,
                label: t.mediaQuery,
                onClick: () => void query(item.publication_id)
              },
              {
                className: BTN_SUBTLE,
                disabled: busy !== null || !item.can_discard,
                label: item.status === 'discarded' ? t.mediaRetryCleanup : t.mediaDiscard,
                onClick: () => setDiscardId(item.publication_id)
              }
            ]}
          />
        </div>
      ))}
      <Pager busy={busy !== null} hasMore={nextCursor !== null} mode="more" onLoadMore={() => void loadMore()} />
      <ConfirmDialog
        confirmLabel={t.mediaDiscard}
        description={t.mediaDiscardDescription}
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
        title={t.mediaDiscardTitle}
      />
    </section>
  )
}
