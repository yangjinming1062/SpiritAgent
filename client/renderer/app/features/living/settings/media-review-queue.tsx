import { type VideoActionKey, videoActionNames } from '@/modules/character'
import { ChatMediaCard } from '@/modules/conversation'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { SECTION_TITLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface PendingMediaReview {
  id: number
  media_type: 'image' | 'video'
  media_url: string
  system_slot: '' | VideoActionKey
  title: string
}

export function MediaReviewQueue(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.settings.persona
  const slotNames = videoActionNames(dict.living.appearance)

  const { data, error, reload, setData } = useAsyncLoader(() =>
    window.spiritagent.api<PendingMediaReview[]>({
      path: '/api/companion/media-reviews',
      method: 'GET'
    })
  )

  const rows = data ?? []
  const failed = error !== null

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className={SECTION_TITLE}>{t.mediaReviewTitle}</p>
        <button className="rounded-md border border-line-standard px-2 py-1 text-xs" onClick={reload} type="button">
          {t.mediaReviewRefresh}
        </button>
      </div>
      {failed && (
        <p className="text-xs text-danger-fg" role="alert">
          {t.mediaReviewLoadError}
        </p>
      )}
      {!failed && rows.length === 0 && <p className="text-xs text-faint">{t.mediaReviewEmpty}</p>}
      {rows.map(row => {
        const title = row.system_slot ? slotNames[row.system_slot] : row.title

        return (
          <div className="space-y-1" key={row.id}>
            {title && <p className="text-sm text-fg">{title}</p>}
            <ChatMediaCard
              item={{ type: row.media_type, url: row.media_url, review_id: String(row.id) }}
              onReviewed={() => setData(current => current?.filter(item => item.id !== row.id) ?? null)}
            />
          </div>
        )
      })}
    </section>
  )
}
