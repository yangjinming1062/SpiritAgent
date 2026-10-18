import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import type { DesktopVideoAction, DesktopVideoSet } from '@/modules/desktop-videos'
import { useResolvedMediaSrc } from '@/modules/media'
import { PortraitLightbox } from '@/shared'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { ZoomIn } from '@/shared/lib/icons'
import { BTN_SUBTLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'
import { desktopMediaLayout } from '@client-shared/desktop-media-geometry'

import styles from './desktop-life-preview.module.css'
import { resolutionLabel } from './desktop-life-resolution'
import { useDesktopLifeStrings } from './desktop-life-strings'

export interface DesktopLifePreviewStatus {
  kind: 'working' | 'paused' | 'error' | 'unknown' | 'empty'
  title: string
  description?: string
  detail?: string
  actions?: React.ReactNode
}

export function DesktopLifeCover({ url, target }: { url: string | null; target?: DesktopVideoSet }): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const media = useResolvedMediaSrc({ type: 'image', url: url ?? '' })
  const [size, setSize] = useState({ url, width: 0, height: 0 })
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const frameWidth = target?.target_width ?? 16
  const frameHeight = target?.target_height ?? 9

  const layout = desktopMediaLayout(
    size.url === url ? size.width : 0,
    size.url === url ? size.height : 0,
    frameWidth,
    frameHeight
  )

  return (
    <div className={styles.cover} style={{ aspectRatio: frameWidth / frameHeight }}>
      {media.status === 'ready' && failedUrl !== url ? (
        <>
          <div className={styles.backdrop} style={{ backgroundImage: `url("${media.src}")` }} />
          <img
            alt=""
            className={styles.media}
            onError={() => setFailedUrl(url)}
            onLoad={event =>
              setSize({ url, width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })
            }
            src={media.src}
            style={{
              left: `${(layout.left / frameWidth) * 100}%`,
              top: `${(layout.top / frameHeight) * 100}%`,
              width: `${(layout.width / frameWidth) * 100}%`,
              height: `${(layout.height / frameHeight) * 100}%`,
              objectFit: layout.objectFit
            }}
          />
        </>
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
  onImageReady,
  status
}: {
  action: DesktopVideoAction
  set: DesktopVideoSet
  draft?: boolean
  onImageReady?: () => void
  status?: DesktopLifePreviewStatus
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
      status={status}
    />
  )
}

function ResolvedDesktopLifePreview({
  action,
  set,
  draft,
  onImageReady,
  onRetry,
  status
}: {
  action: DesktopVideoAction
  set: DesktopVideoSet
  draft: boolean
  onImageReady?: () => void
  onRetry: () => void
  status?: DesktopLifePreviewStatus
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
    if (!active || status) {
      video.current?.pause()
    }
  }, [active, status])

  const displayedStatus: DesktopLifePreviewStatus | undefined =
    status ||
    (loadFailed
      ? {
          kind: 'error',
          title: t.previewFailed,
          actions: (
            <button className={`${BTN_SUBTLE} ${styles.button}`} onClick={onRetry} type="button">
              {t.retryPreview}
            </button>
          )
        }
      : !hasMedia
        ? { kind: url ? 'working' : 'empty', title: url ? t.previewLoading : t.noPreview }
        : undefined)

  const canEnlarge = Boolean(
    active &&
    draft &&
    action.stage === 'image_review' &&
    !action.draft?.image_confirmed &&
    imageUrl === action.draft?.image_url &&
    !videoUrl &&
    hasMedia &&
    size.width > 0 &&
    !displayedStatus
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
                controls={!status}
                loop={action.kind === 'loop'}
                muted
                onError={() => setFailed(true)}
                onLoadedMetadata={updateVideoSize}
                onResize={updateVideoSize}
                playsInline
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
        {displayedStatus && (
          <div className={styles.statusLayer}>
            <div
              aria-live={displayedStatus.kind === 'error' ? 'assertive' : 'polite'}
              className={styles.statusCard}
              data-kind={displayedStatus.kind}
              data-over-media={hasMedia}
              role="status"
            >
              {displayedStatus.kind === 'working' && <span aria-hidden="true" className={styles.spinner} />}
              <strong className={styles.statusTitle}>{displayedStatus.title}</strong>
              {displayedStatus.description && <p className={styles.statusDescription}>{displayedStatus.description}</p>}
              {displayedStatus.detail && (
                <details className={styles.details}>
                  <summary>{t.reviewDetails}</summary>
                  <p>{displayedStatus.detail}</p>
                </details>
              )}
              {displayedStatus.actions && <div className={styles.actions}>{displayedStatus.actions}</div>}
            </div>
          </div>
        )}
      </div>
      {status && loadFailed && (
        <div className={styles.loadFailure} role="status">
          <p>{t.previewFailed}</p>
          <button className={`${BTN_SUBTLE} ${styles.button}`} onClick={onRetry} type="button">
            {t.retryPreview}
          </button>
        </div>
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
