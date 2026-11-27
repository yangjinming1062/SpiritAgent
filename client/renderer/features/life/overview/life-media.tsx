import { useEffect, useRef, useState } from 'react'

import { MediaPreview, useResolvedMediaSrc, VideoSurface } from '@/features/media'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { ImageLightboxButton } from '@/ui'

import { LifeMediaError, LifeMediaPlay, type LifeMediaTile } from '../../../../../shared/life-media'

type TileProps = React.ComponentProps<LifeMediaTile>

function LifePhoto({ item, t }: Pick<TileProps, 'item' | 't'>) {
  return (
    <MediaPreview type="image" url={item.url}>
      {({ failed, src, onError, onRetry }) =>
        failed ? (
          <LifeMediaError label={t.failed} onRetry={onRetry} retry={t.retry} />
        ) : src ? (
          <ImageLightboxButton
            ariaLabel={item.alt ? `${t.openImage} · ${item.alt}` : t.openImage}
            className="life-media-button"
            name={item.alt}
            url={src}
          >
            <img alt={item.alt} className="life-media-surface" onError={onError} src={src} />
          </ImageLightboxButton>
        ) : null
      }
    </MediaPreview>
  )
}

function pauseOtherLifeVideos(playing: HTMLVideoElement): void {
  if (playing.paused) {
    return
  }

  playing.ownerDocument.querySelectorAll<HTMLVideoElement>('.life-media video').forEach(video => {
    if (video !== playing && !video.paused) {
      video.pause()
    }
  })
}

function LifeVideo({ item, t }: Pick<TileProps, 'item' | 't'>) {
  const [requested, setRequested] = useState(false)
  const poster = useResolvedMediaSrc({ type: 'image', url: item.poster_url ?? '' })
  const posterSrc = poster.status === 'ready' ? poster.src : undefined

  if (!requested) {
    return (
      <LifeMediaPlay label={item.alt ? `${t.playVideo} · ${item.alt}` : t.playVideo} onClick={() => setRequested(true)}>
        {posterSrc && <img alt="" className="life-media-surface" src={posterSrc} />}
      </LifeMediaPlay>
    )
  }

  return (
    <MediaPreview type="video" url={item.url}>
      {({ failed, src, onError, onRetry }) =>
        failed ? (
          <LifeMediaError label={t.failed} onRetry={onRetry} retry={t.retry} />
        ) : src ? (
          <VideoSurface
            autoPlay
            className="life-media-surface"
            frameClassName="life-media-surface"
            onError={onError}
            onPlay={pauseOtherLifeVideos}
            poster={posterSrc}
            src={src}
          />
        ) : (
          <span className="life-media-loading" role="status">
            {t.loading}
          </span>
        )
      }
    </MediaPreview>
  )
}

function LifeThumbnail({ item }: Pick<TileProps, 'item'>) {
  const preview = useResolvedMediaSrc({
    type: 'image',
    url: (item.kind === 'video' ? item.poster_url : item.url) ?? ''
  })

  if (preview.status !== 'ready') {
    return null
  }

  return <img alt={item.alt} className="life-media-surface" src={preview.src} />
}

function DeferredLifeMedia({ active, children }: { active: boolean; children: React.ReactNode }) {
  const panelActive = usePanelActivity()
  const [nearViewport, setNearViewport] = useState(false)
  const anchor = useRef<HTMLSpanElement>(null)
  const visible = active && panelActive

  useEffect(() => {
    const target = anchor.current

    if (!visible || nearViewport || !target) {
      return
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setNearViewport(true)
          observer.disconnect()
        }
      },
      { rootMargin: '240px' }
    )

    observer.observe(target)

    return () => observer.disconnect()
  }, [nearViewport, visible])

  if (visible && nearViewport) {
    return children
  }

  return <span aria-hidden="true" className="life-media-surface" ref={anchor} />
}

export const DesktopLifeMediaTile: LifeMediaTile = ({ active, item, t }) => (
  <DeferredLifeMedia active={active}>
    {item.kind === 'video' ? <LifeVideo item={item} t={t} /> : <LifePhoto item={item} t={t} />}
  </DeferredLifeMedia>
)

export const DesktopLifeThumbnail: LifeMediaTile = ({ active, item }) => (
  <DeferredLifeMedia active={active}>
    <LifeThumbnail item={item} />
  </DeferredLifeMedia>
)
