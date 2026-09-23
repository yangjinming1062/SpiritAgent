import { useEffect, useState } from 'react'

import { ChatMediaCard } from '@/modules/conversation'
import { SECTION_TITLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface PendingMediaReview {
  id: number
  media_type: 'image' | 'video'
  media_url: string
  title: string
}

export function MediaReviewQueue(): React.JSX.Element {
  const t = useStrings().settings.persona
  const [rows, setRows] = useState<PendingMediaReview[]>([])
  const [error, setError] = useState(false)
  const [refreshRevision, setRefreshRevision] = useState(0)

  useEffect(() => {
    let mounted = true
    void window.spiritagent
      .api<PendingMediaReview[]>({
        path: '/api/companion/media-reviews',
        method: 'GET'
      })
      .then(result => {
        if (mounted) {
          setRows(result)
          setError(false)
        }
      })
      .catch(() => {
        if (mounted) {
          setError(true)
        }
      })

    return () => {
      mounted = false
    }
  }, [refreshRevision])

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className={SECTION_TITLE}>{t.mediaReviewTitle}</p>
        <button
          className="rounded-md border border-line-standard px-2 py-1 text-xs"
          onClick={() => setRefreshRevision(n => n + 1)}
          type="button"
        >
          {t.mediaReviewRefresh}
        </button>
      </div>
      {error && (
        <p className="text-xs text-danger-fg" role="alert">
          {t.mediaReviewLoadError}
        </p>
      )}
      {!error && rows.length === 0 && <p className="text-xs text-faint">{t.mediaReviewEmpty}</p>}
      {rows.map(row => (
        <div className="space-y-1" key={row.id}>
          {row.title && <p className="text-sm text-fg">{row.title}</p>}
          <ChatMediaCard
            item={{ type: row.media_type, url: row.media_url, review_id: String(row.id) }}
            onReviewed={() => setRows(current => current.filter(item => item.id !== row.id))}
          />
        </div>
      ))}
    </section>
  )
}
