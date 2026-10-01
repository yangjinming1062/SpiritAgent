import { useRef } from 'react'

import { useStrings } from '@/shared/strings'

import { useResolvedMediaSrc } from './media-src'

export function InlineMedia({
  alt,
  audioUrl,
  mediaType,
  onImageClick,
  url
}: {
  alt: string
  audioUrl: string | null
  mediaType: '' | 'image' | 'video' | 'audio'
  onImageClick?: () => void
  url: string
}): React.JSX.Element | null {
  const dict = useStrings()
  const media = useResolvedMediaSrc({ type: mediaType || 'image', url })
  const voice = useResolvedMediaSrc({ type: 'audio', url: audioUrl || '' })
  const voiceSrc = voice.status === 'ready' ? voice.src : null
  const audioRef = useRef<HTMLAudioElement>(null)

  if (media.status === 'loading') {
    return null
  }

  if (media.status === 'failed') {
    return (
      <div className="flex h-24 w-40 items-center justify-center rounded-lg border border-line-standard bg-fill-faint text-xs text-faint">
        {dict.chat.media.loadFailed}
      </div>
    )
  }

  const src = media.src

  const voiceFailed =
    voice.status === 'failed' ? <p className="text-xs text-faint">{dict.chat.media.loadFailed}</p> : null

  if (mediaType === 'video') {
    return (
      <div className="w-full" onClick={event => event.stopPropagation()}>
        <video
          className="max-h-[70vh] max-w-full rounded-lg"
          controls
          muted={Boolean(voiceSrc)}
          onEnded={() => {
            if (audioRef.current) {
              audioRef.current.pause()
              audioRef.current.currentTime = 0
            }
          }}
          onPause={() => audioRef.current?.pause()}
          onPlay={event => {
            if (audioRef.current) {
              audioRef.current.currentTime = event.currentTarget.currentTime
              void audioRef.current.play()
            }
          }}
          onSeeked={event => {
            if (audioRef.current) {
              audioRef.current.currentTime = event.currentTarget.currentTime
            }
          }}
          src={src}
        />
        {voiceSrc ? <audio className="w-full" controls ref={audioRef} src={voiceSrc} /> : voiceFailed}
      </div>
    )
  }

  if (mediaType === 'audio') {
    return (
      <div className="w-full" onClick={event => event.stopPropagation()}>
        <audio className="w-full" controls src={src} />
      </div>
    )
  }

  const image = <img alt={alt} className="max-h-[70vh] max-w-full rounded-lg" src={src} />

  return (
    <div className="w-full">
      {onImageClick ? (
        <button
          aria-label={dict.ui.lightbox.zoomIn}
          className="block max-w-full cursor-zoom-in rounded-lg p-0"
          onClick={onImageClick}
          type="button"
        >
          {image}
        </button>
      ) : (
        image
      )}
      {voiceSrc ? <audio className="w-full" controls src={voiceSrc} /> : voiceFailed}
    </div>
  )
}
