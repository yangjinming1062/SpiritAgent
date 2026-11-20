import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { MediaPreview, useMediaViewId, VideoSurface } from '@/features/media'
import { requestResource } from '@/shared/lib/resource-request'
import { presentationPorts } from '@/shared/presentation-ports'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, MediaReviewPanel } from '@/ui'

import type { ApiSchemas } from '../../../../shared/api.generated'
import { mediaReviewCopy } from '../../../../shared/media-review'
import { useMediaJobReview } from '../../../../shared/use-media-job-review'

const reviewApi = { invoke: requestResource }

/** 复核入口：任务状态与操作归本功能，面板只接显示数据与回调。 */
interface ReviewProps {
  jobId: string
  onChanged?: () => void | Promise<void>
}

export function MediaReviewButton(props: ReviewProps): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const copy = mediaReviewCopy[useStore($locale)]

  return (
    <>
      <button aria-expanded={open} className={BTN_SUBTLE} onClick={() => setOpen(value => !value)} type="button">
        {copy.showCandidates}
      </button>
      {open ? <ReviewDetails key={props.jobId} {...props} /> : null}
    </>
  )
}

function ReviewDetails({ jobId, onChanged }: ReviewProps): React.JSX.Element {
  const review = useMediaJobReview(reviewApi, jobId, onChanged)
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
            // 原生控件在无全屏权限时仍会留下不可用的全屏按钮，预览统一用自带控制条，全屏走窗口内查看器。
            return (
              <VideoSurface
                className="max-h-72 w-full rounded-lg border border-line-hairline bg-black"
                frameClassName="block w-full overflow-hidden rounded-lg"
                onError={state.onError}
                onFullscreen={() => preview(candidate)}
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
      regenerate={review.regenerate}
      reload={review.reload}
      renderMedia={renderMedia}
    />
  )
}
