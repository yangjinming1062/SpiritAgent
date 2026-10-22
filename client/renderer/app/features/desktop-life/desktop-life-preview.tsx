import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import type { DesktopVideoAction, DesktopVideoSet } from '@/modules/desktop-videos'
import { MediaPreview, useResolvedMediaSrc } from '@/modules/media'
import { ImageLightboxButton } from '@/shared/components/image-lightbox-button'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { ZoomIn } from '@/shared/lib/icons'
import { BTN_SUBTLE, CreationFeedback } from '@/shared/panel'
import { useStrings } from '@/shared/strings'
import { desktopMediaLayout } from '@client-shared/desktop-media-geometry'

import styles from './desktop-life-preview.module.css'
import { resolutionLabel } from './desktop-life-resolution'

export function DesktopLifeCover({ url }: { url: string | null }): React.JSX.Element {
  const strings = useStrings()
  const t = strings.desktopLife

  return (
    <div className={styles.cover}>
      <MediaPreview type="image" url={url ?? ''}>
        {({ failed, onError, src, status }) =>
          src && !failed ? (
            <img alt="" className="h-full w-full object-contain" onError={onError} src={src} />
          ) : (
            <span className={styles.coverPlaceholder}>
              {url && status === 'loading' ? t.previewLoading : t.noPreview}
            </span>
          )
        }
      </MediaPreview>
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
      key={`${draft ? action.draft?.id : action.id}:${mediaKey}`}
      onImageReady={onImageReady}
      set={set}
    />
  )
}

function ResolvedDesktopLifePreview({
  action,
  set,
  draft,
  onImageReady
}: {
  action: DesktopVideoAction
  set: DesktopVideoSet
  draft: boolean
  onImageReady?: () => void
}): React.JSX.Element {
  const active = usePanelActivity()
  const strings = useStrings()
  const t = strings.desktopLife
  const viewOriginal = useStrings().living.scene.viewOriginal
  const videoUrl = draft ? action.draft?.candidate_video_url : action.current?.video_url
  const draftImageUrl = action.draft?.candidate_poster_url || action.draft?.image_url
  const currentFallback = draft && !videoUrl && !draftImageUrl && Boolean(action.current?.poster_url)
  const imageUrl = draft ? draftImageUrl || action.current?.poster_url : action.current?.poster_url
  const url = videoUrl || imageUrl || ''
  const posterMedia = useResolvedMediaSrc({ type: 'image', url: imageUrl || '' })
  const [size, setSize] = useState({ width: 0, height: 0 })
  const video = useRef<HTMLVideoElement>(null)
  const frameWidth = draft ? (action.draft?.target_width ?? set.target_width) : set.target_width
  const frameHeight = draft ? (action.draft?.target_height ?? set.target_height) : set.target_height
  const actualWidth = draft && !currentFallback ? action.draft?.frame_width : action.current?.width
  const actualHeight = draft && !currentFallback ? action.draft?.frame_height : action.current?.height

  const updateVideoSize = (event: React.SyntheticEvent<HTMLVideoElement>): void => {
    setSize({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight })
  }

  useEffect(() => {
    if (!active) {
      video.current?.pause()
    }
  }, [active])

  return (
    <div className={styles.preview}>
      <MediaPreview type={videoUrl ? 'video' : 'image'} url={url}>
        {({ failed, onError, onRetry, src, status }) => {
          const loadFailed = Boolean(url) && failed
          const hasMedia = Boolean(url) && status === 'ready' && !failed

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

          return (
            <>
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
                        onError={onError}
                        onLoadedMetadata={updateVideoSize}
                        onResize={updateVideoSize}
                        playsInline
                        poster={posterMedia.status === 'ready' ? posterMedia.src : undefined}
                        preload="metadata"
                        ref={video}
                        src={src}
                        style={mediaStyle}
                      />
                    ) : (
                      <img
                        alt={action.name}
                        className={styles.media}
                        onError={onError}
                        onLoad={event => {
                          setSize({
                            width: event.currentTarget.naturalWidth,
                            height: event.currentTarget.naturalHeight
                          })

                          if (draft && !currentFallback && imageUrl === action.draft?.image_url) {
                            onImageReady?.()
                          }
                        }}
                        src={src}
                        style={mediaStyle}
                      />
                    ))}
                  {canEnlarge && status === 'ready' && (
                    <ImageLightboxButton
                      ariaLabel={viewOriginal}
                      className={styles.enlarge}
                      name={action.name}
                      url={src ?? ''}
                    >
                      <span className={styles.enlargeHint}>
                        <ZoomIn aria-hidden="true" size={16} />
                        {viewOriginal}
                      </span>
                    </ImageLightboxButton>
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
            </>
          )
        }}
      </MediaPreview>
    </div>
  )
}
