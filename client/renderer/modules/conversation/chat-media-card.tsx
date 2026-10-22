import { InlineMedia, useResolvedMediaSrc } from '@/modules/media'
import { presentationPorts } from '@/shared/presentation-ports'
import { useStrings } from '@/shared/strings'
import type { ChatMediaItem } from '@protocol'

import { useConversationView } from './conversation-view'

export function ChatMediaCard({ item }: { item: ChatMediaItem }): React.JSX.Element {
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
