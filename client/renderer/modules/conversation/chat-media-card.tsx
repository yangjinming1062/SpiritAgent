import { useEffect, useState } from 'react'

import { InlineMedia, useResolvedMediaSrc } from '@/modules/media'
import { presentationPorts } from '@/shared/presentation-ports'
import { useStrings } from '@/shared/strings'
import type { ChatMediaItem } from '@/shared/types/spiritagent'

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
  return item.type !== 'image' || item.audio_url ? (
    <InlineMedia
      alt=""
      audioUrl={item.audio_url ?? null}
      key={`${item.url}:${item.audio_url}`}
      mediaType={item.type}
      url={item.url}
    />
  ) : (
    <ImageCard item={item} />
  )
}

function ReviewMediaCard({ item, onReviewed }: { item: ChatMediaItem; onReviewed?: () => void }): React.JSX.Element {
  const dict = useStrings().chat.media
  const [status, setStatus] = useState<'pending' | 'accepted' | 'rejected' | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let mounted = true
    void window.spiritagent
      .api<{ status: string; reason: string }>({
        path: `/api/companion/media-reviews/${item.review_id}`,
        method: 'GET'
      })
      .then(result => {
        if (mounted) {
          setStatus(result.status === 'accepted' ? 'accepted' : result.status === 'rejected' ? 'rejected' : 'pending')
          setReason(result.reason)
        }
      })
      .catch(() => {
        if (mounted) {
          setError(dict.reviewLoadError)
        }
      })

    return () => {
      mounted = false
    }
  }, [item.review_id, dict.reviewLoadError])

  if (status === 'accepted') {
    return <DeliveredMediaCard item={item} />
  }

  const decide = async (decision: 'accept' | 'reject'): Promise<void> => {
    setBusy(true)
    setError('')

    try {
      await window.spiritagent.api({
        path: `/api/companion/media-reviews/${item.review_id}/${decision}`,
        method: 'POST',
        body: {}
      })
      setStatus(decision === 'accept' ? 'accepted' : 'rejected')
      onReviewed?.()
    } catch {
      setError(dict.reviewAcceptError)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-amber-400/50 bg-fill-trough p-2">
      <p className="text-xs text-amber-300">
        {status === 'rejected' ? dict.reviewRejected : dict.reviewHint} {reason}
      </p>
      <InlineMedia alt="" audioUrl={item.audio_url ?? null} mediaType={item.type} url={item.url} />
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
  const dict = useStrings()
  const src = useResolvedMediaSrc(item)

  if (!src) {
    return (
      <div className="flex h-24 w-40 items-center justify-center rounded-lg border border-line-standard bg-fill-faint text-xs text-faint">
        {dict.chat.media.imageLoading}
      </div>
    )
  }

  return (
    <button
      className="block cursor-zoom-in overflow-hidden rounded-lg border border-line-standard bg-fill-trough p-0 transition hover:border-line-strong"
      onClick={() => presentationPorts().openMediaViewer(item)}
      type="button"
    >
      <img alt="" className="block max-h-56 max-w-full object-contain" src={src} />
    </button>
  )
}
