import { useEffect, useState } from 'react'

import { InlineMedia, useResolvedMediaSrc } from '@/modules/media'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { presentationPorts } from '@/shared/presentation-ports'
import { useStrings } from '@/shared/strings'
import type { ChatMediaItem } from '@protocol'

import { useConversationView } from './conversation-view'

export function ChatMediaCard({
  item,
  onReviewed
}: {
  item: ChatMediaItem
  onReviewed?: () => void
}): React.JSX.Element {
  if (item.review_id) {
    return <ReviewMediaCard item={item} onReviewed={onReviewed} />
  }

  return <DeliveredMediaCard item={item} />
}

function DeliveredMediaCard({ item }: { item: ChatMediaItem }): React.JSX.Element {
  const { viewId } = useConversationView()

  return item.type !== 'image' || item.audio_url ? (
    <InlineMedia
      alt=""
      audioUrl={item.audio_url ?? null}
      key={`${item.url}:${item.audio_url}`}
      mediaType={item.type}
      onImageClick={item.type === 'image' ? () => presentationPorts().openMediaViewer(item, viewId) : undefined}
      url={item.url}
    />
  ) : (
    <ImageCard item={item} />
  )
}

function ReviewMediaCard({ item, onReviewed }: { item: ChatMediaItem; onReviewed?: () => void }): React.JSX.Element {
  const { viewId } = useConversationView()
  const dict = useStrings().chat.media
  const [status, setStatus] = useState<'pending' | 'accepted' | 'rejected' | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const beginAsync = useAsyncGuard()
  const onReviewedRef = useLatestRef(onReviewed)

  useEffect(() => {
    let mounted = true
    const isLive = beginAsync()
    void window.spiritagent
      .api<{ status: string; reason: string }>({
        path: `/api/companion/media-reviews/${item.review_id}`,
        method: 'GET'
      })
      .then(result => {
        if (mounted && isLive()) {
          setStatus(result.status === 'accepted' ? 'accepted' : result.status === 'rejected' ? 'rejected' : 'pending')
          setReason(result.reason)

          if (result.status !== 'pending') {
            onReviewedRef.current?.()
          }
        }
      })
      .catch(() => {
        if (mounted && isLive()) {
          setError(dict.reviewLoadError)
        }
      })

    return () => {
      mounted = false
    }
  }, [item.review_id, dict.reviewLoadError, beginAsync, onReviewedRef])

  if (status === 'accepted') {
    return <DeliveredMediaCard item={item} />
  }

  const decide = async (decision: 'accept' | 'reject'): Promise<void> => {
    const isLive = beginAsync()
    setBusy(true)
    setError('')

    try {
      const result = await window.spiritagent.api<{ status: string }>({
        path: `/api/companion/media-reviews/${item.review_id}/${decision}`,
        method: 'POST',
        body: {}
      })

      if (!isLive()) {
        return
      }

      setStatus(result.status === 'accepted' ? 'accepted' : 'rejected')
      onReviewedRef.current?.()
    } catch {
      if (!isLive()) {
        return
      }

      try {
        const current = await window.spiritagent.api<{ status: string }>({
          path: `/api/companion/media-reviews/${item.review_id}`,
          method: 'GET'
        })

        if (!isLive()) {
          return
        }

        if (current.status !== 'pending') {
          setStatus(current.status === 'accepted' ? 'accepted' : 'rejected')
          onReviewedRef.current?.()

          return
        }
      } catch {
        // 状态查询失败时保留本次操作错误，用户仍可刷新或重试。
      }

      if (isLive()) {
        setError(dict.reviewAcceptError)
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-amber-400/50 bg-fill-trough p-2">
      <p className="text-xs text-amber-300">
        {status === 'rejected' ? dict.reviewRejected : dict.reviewHint} {reason}
      </p>
      <InlineMedia
        alt=""
        audioUrl={item.audio_url ?? null}
        mediaType={item.type}
        onImageClick={item.type === 'image' ? () => presentationPorts().openMediaViewer(item, viewId) : undefined}
        url={item.url}
      />
      {error && (
        <p className="text-xs text-danger-fg" role="alert">
          {error}
        </p>
      )}
      {status === 'pending' && (
        <div className="flex gap-2">
          <button
            className="rounded-md border border-line-standard px-3 py-1 text-xs"
            disabled={busy}
            onClick={() => void decide('accept')}
            type="button"
          >
            {dict.reviewAccept}
          </button>
          <button
            className="rounded-md border border-line-standard px-3 py-1 text-xs"
            disabled={busy}
            onClick={() => void decide('reject')}
            type="button"
          >
            {dict.reviewReject}
          </button>
        </div>
      )}
    </div>
  )
}

function ImageCard({ item }: { item: ChatMediaItem }): React.JSX.Element {
  const { viewId } = useConversationView()
  const dict = useStrings()
  const media = useResolvedMediaSrc(item)

  if (media.status !== 'ready') {
    return (
      <div className="flex h-24 w-40 items-center justify-center rounded-lg border border-line-standard bg-fill-faint text-xs text-faint">
        {media.status === 'failed' ? dict.chat.media.loadFailed : dict.chat.media.imageLoading}
      </div>
    )
  }

  return (
    <button
      aria-label={dict.ui.lightbox.zoomIn}
      className="block cursor-zoom-in overflow-hidden rounded-lg border border-line-standard bg-fill-trough p-0 transition hover:border-line-strong"
      onClick={() => presentationPorts().openMediaViewer(item, viewId)}
      type="button"
    >
      <img alt="" className="block max-h-56 max-w-full object-contain" src={media.src} />
    </button>
  )
}
