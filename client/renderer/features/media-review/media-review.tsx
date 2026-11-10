import { useStore } from '@nanostores/react'
import type React from 'react'

import { MediaPreview, useMediaViewId } from '@/features/media'
import { presentationPorts } from '@/shared/presentation-ports'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, MediaReviewPanel } from '@/ui'

import type { ApiSchemas } from '../../../../shared/api.generated'
import { mediaReviewCopy } from '../../../../shared/media-review'

import { useMediaJobReview } from './use-media-job-review'

/** 复核入口：任务状态与操作归本功能，面板只接显示数据与回调。 */
export function MediaReviewButton({
  jobId,
  mediaId,
  receiveResult,
  onChanged
}: {
  jobId: string
  mediaId?: string
  receiveResult?: boolean
  onChanged?: () => void | Promise<void>
}): React.JSX.Element {
  const review = useMediaJobReview({ jobId, mediaId, receiveResult, onChanged })
  const copy = mediaReviewCopy[useStore($locale)]
  const dict = useStrings()
  const viewId = useMediaViewId()

  const preview = (candidate: ApiSchemas['MediaCandidateView']): void =>
    presentationPorts().openMediaViewer({ type: candidate.media_type, url: candidate.preview_url }, viewId)

  const renderMedia = (candidate: ApiSchemas['MediaCandidateView']): React.ReactNode => (
    <MediaPreview type={candidate.media_type} url={candidate.preview_url}>
      {state => {
        if (state.status === 'ready' && state.src) {
          if (candidate.media_type === 'video') {
            return (
              <video
                className="max-h-72 w-full rounded-lg border border-line-hairline bg-black"
                controls
                onError={state.onError}
                src={state.src}
              />
            )
          }

          return (
            <button
              aria-label={dict.ui.lightbox.zoomIn}
              className="w-fit cursor-zoom-in rounded-lg"
              onClick={() => preview(candidate)}
              type="button"
            >
              <img
                alt=""
                className="max-h-72 max-w-full rounded-lg border border-line-hairline object-contain"
                onError={state.onError}
                src={state.src}
              />
            </button>
          )
        }

        return (
          <span className="flex h-24 flex-col items-center justify-center gap-1 rounded-lg border border-line-hairline bg-fill-faint text-[11px] text-faint">
            {state.status === 'failed' ? (
              <>
                <span>{copy.mediaFailed}</span>
                <button className={BTN_SUBTLE} onClick={state.onRetry} type="button">
                  {copy.retryMedia}
                </button>
              </>
            ) : (
              copy.mediaLoading
            )}
          </span>
        )
      }}
    </MediaPreview>
  )

  return (
    <MediaReviewPanel
      approve={review.approve}
      busy={review.busy}
      copy={copy}
      error={review.error}
      job={review.job}
      mediaId={mediaId}
      receive={review.receive}
      receiveResult={receiveResult}
      regenerate={review.regenerate}
      reload={review.reload}
      renderMedia={renderMedia}
    />
  )
}
