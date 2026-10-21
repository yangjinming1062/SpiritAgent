import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import type { DesktopVideoAction, DesktopVideoSet } from '@/modules/desktop-videos'
import { useResolvedMediaSrc } from '@/modules/media'
import { PortraitLightbox } from '@/shared'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { ZoomIn } from '@/shared/lib/icons'
import { BTN_SUBTLE, CreationFeedback } from '@/shared/panel'
import { useStrings } from '@/shared/strings'
import { desktopMediaLayout } from '@client-shared/desktop-media-geometry'

import styles from './desktop-life-preview.module.css'
import { resolutionLabel } from './desktop-life-resolution'
import { useDesktopLifeStrings } from './desktop-life-strings'

export function DesktopLifeCover({ url }: { url: string | null }): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const media = useResolvedMediaSrc({ type: 'image', url: url ?? '' })
  const [failedUrl, setFailedUrl] = useState<string | null>(null)

  return (
    <div className={styles.cover}>
      {media.status === 'ready' && failedUrl !== url ? (
        <img alt="" className="h-full w-full object-contain" onError={() => setFailedUrl(url)} src={media.src} />
      ) : (
        <span className={styles.coverPlaceholder}>
          {url && media.status === 'loading' ? t.previewLoading : t.noPreview}
        </span>
      )}
    </div>
  )
}

export function DesktopLifePreview({
  action,
  set,
  draft = false,
  onImageReady
}: {
  action: DesktopVideoAction
  set: DesktopVideoSet
  draft?: boolean
  onImageReady?: () => void
}): React.JSX.Element {
  const [attempt, setAttempt] = useState(0)

  const videoUrl = draft ? action.draft?.candidate_video_url : action.current?.video_url

  const imageUrl = draft
    ? action.draft?.candidate_poster_url || action.draft?.image_url || action.current?.poster_url
    : action.current?.poster_url

  const mediaUrl = videoUrl || imageUrl || ''
  // 图片确认跟随完整 URL；视频保留资产身份，避免仅因签名更新重建播放器。
  const mediaKey = draft && !videoUrl ? mediaUrl : mediaUrl.split(/[?#]/)[0]

  return (
    <ResolvedDesktopLifePreview
      action={action}
      draft={draft}
      key={`${draft ? action.draft?.id : action.id}:${mediaKey}:${attempt}`}
      onImageReady={onImageReady}
      onRetry={() => setAttempt(value => value + 1)}
      set={set}
    />
  )
}

function ResolvedDesktopLifePreview({
  action,
  set,
  draft,
  onImageReady,
  onRetry
}: {
  action: DesktopVideoAction
  set: DesktopVideoSet
  draft: boolean
  onImageReady?: () => void
  onRetry: () => void
}): React.JSX.Element {
  const active = usePanelActivity()
  const t = useDesktopLifeStrings()
  const viewOriginal = useStrings().living.scene.viewOriginal
  const videoUrl = draft ? action.draft?.candidate_video_url : action.current?.video_url
  const draftImageUrl = action.draft?.candidate_poster_url || action.draft?.image_url
  const currentFallback = draft && !videoUrl && !draftImageUrl && Boolean(action.current?.poster_url)
  const imageUrl = draft ? draftImageUrl || action.current?.poster_url : action.current?.poster_url
  const url = videoUrl || imageUrl || ''
  const media = useResolvedMediaSrc({ type: videoUrl ? 'video' : 'image', url })
  const posterMedia = useResolvedMediaSrc({ type: 'image', url: imageUrl || '' })
  const [failed, setFailed] = useState(false)
  const [zoomed, setZoomed] = useState(false)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const video = useRef<HTMLVideoElement>(null)
  const frameWidth = draft ? (action.draft?.target_width ?? set.target_width) : set.target_width
  const frameHeight = draft ? (action.draft?.target_height ?? set.target_height) : set.target_height
  const actualWidth = draft && !currentFallback ? action.draft?.frame_width : action.current?.width
  const actualHeight = draft && !currentFallback ? action.draft?.frame_height : action.current?.height
  const loadFailed = Boolean(url) && (failed || media.status === 'failed')
  const hasMedia = Boolean(url) && media.status === 'ready' && !failed

  const layout = desktopMediaLayout(
    size.width || actualWidth || 0,
    size.height || actualHeight || 0,
    frameWidth,
    frameHeight
  )

  const mediaStyle = {
    left: `${(layout.left / frameWidth) * 100}%`,
    top: `${(layout.top / frameHeight) * 100}%`,
    width: `${(layout.width / frameWidth) * 100}%`,
    height: `${(layout.height / frameHeight) * 100}%`,
    objectFit: layout.objectFit
  } as const

  const updateVideoSize = (event: React.SyntheticEvent<HTMLVideoElement>): void => {
    setSize({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight })
  }

  useEffect(() => {
    if (!active) {
      video.current?.pause()
    }
  }, [active])

  const canEnlarge = Boolean(
    active &&
    draft &&
    action.stage === 'image_review' &&
    !action.draft?.image_confirmed &&
    imageUrl === action.draft?.image_url &&
    !videoUrl &&
    hasMedia &&
    size.width > 0 &&
    !loadFailed
  )

  useEffect(() => {
    if (!canEnlarge) {
      setZoomed(false)
    }
  }, [canEnlarge])

  return (
    <div className={styles.preview}>
      <div className={styles.stage} data-has-media={hasMedia}>
        <div
          className={styles.frame}
          style={{
            aspectRatio: frameWidth / frameHeight,
            maxWidth: `calc(var(--desktop-life-preview-height, 60vh) * ${frameWidth / frameHeight})`
          }}
        >
          {posterMedia.status === 'ready' && (
            <div className={styles.backdrop} style={{ backgroundImage: `url("${posterMedia.src}")` }} />
          )}
          {hasMedia &&
            (videoUrl ? (
              <video
                aria-label={action.name}
                className={styles.media}
                controls
                loop={action.kind === 'loop'}
                muted
                onError={() => setFailed(true)}
                onLoadedMetadata={updateVideoSize}
                onResize={updateVideoSize}
                playsInline
                poster={posterMedia.status === 'ready' ? posterMedia.src : undefined}
                preload="metadata"
                ref={video}
                src={media.src}
                style={mediaStyle}
              />
            ) : (
              <img
                alt={action.name}
                className={styles.media}
                onError={() => setFailed(true)}
                onLoad={event => {
                  setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })

                  if (draft && !currentFallback && imageUrl === action.draft?.image_url) {
                    onImageReady?.()
                  }
                }}
                src={media.src}
                style={mediaStyle}
              />
            ))}
          {canEnlarge && (
            <button
              aria-haspopup="dialog"
              aria-label={viewOriginal}
              className={styles.enlarge}
              onClick={() => setZoomed(true)}
              type="button"
            >
              <span className={styles.enlargeHint}>
                <ZoomIn aria-hidden="true" size={16} />
                {viewOriginal}
              </span>
            </button>
          )}
        </div>
        {currentFallback && hasMedia && <span className={styles.currentProduct}>{t.currentProduct}</span>}
        {!hasMedia && !loadFailed && (
          <span className={styles.coverPlaceholder}>{url ? t.previewLoading : t.noPreview}</span>
        )}
      </div>
      {loadFailed && (
        <CreationFeedback
          actions={
            <button className={BTN_SUBTLE} onClick={onRetry} type="button">
              {t.retryPreview}
            </button>
          }
          state="error"
          title={t.previewFailed}
        />
      )}
      {actualWidth && actualHeight ? (
        <p className={styles.metadata}>
          {t.actualResolution}: {resolutionLabel(actualWidth, actualHeight)}
        </p>
      ) : null}
      {zoomed && media.status === 'ready' && (
        <PortraitLightbox name={action.name} onClose={() => setZoomed(false)} url={media.src} />
      )}
    </div>
  )
}
