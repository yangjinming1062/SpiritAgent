import { useResolvedMediaSrc } from '@/modules/media'
import { InlineMedia } from '@/modules/media'
import { presentationPorts } from '@/shared/presentation-ports'
import { useStrings } from '@/shared/strings'
import type { ChatMediaItem } from '@/shared/types/spiritagent'

export function ChatMediaCard({ item }: { item: ChatMediaItem }): React.JSX.Element {
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
