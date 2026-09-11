import { useRef } from 'react'

import { useResolvedMediaSrc } from './chat-media-src'

export function InlineMedia({
  alt,
  audioUrl,
  mediaType,
  url
}: {
  alt: string
  audioUrl: string | null
  mediaType: '' | 'image' | 'video' | 'audio'
  url: string
}): React.JSX.Element | null {
  const src = useResolvedMediaSrc({ type: mediaType || 'image', url })
  const voiceSrc = useResolvedMediaSrc({ type: 'audio', url: audioUrl || '' })
  const audioRef = useRef<HTMLAudioElement>(null)

  if (!src) {
    return null
  }

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
        {voiceSrc ? <audio className="w-full" controls ref={audioRef} src={voiceSrc} /> : null}
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

  return (
    <div className="w-full">
      <img alt={alt} className="max-h-[70vh] max-w-full rounded-lg" src={src} />
      {voiceSrc ? <audio className="w-full" controls src={voiceSrc} /> : null}
    </div>
  )
}
