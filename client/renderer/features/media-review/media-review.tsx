import { useStore } from '@nanostores/react'
import type React from 'react'

import { presentationPorts } from '@/shared/presentation-ports'
import { $locale } from '@/shared/store/locale'
import { MediaReviewPanel } from '@/ui'

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

  const preview = (candidate: ApiSchemas['MediaCandidateView']): void =>
    presentationPorts().openMediaViewer({ type: candidate.media_type, url: candidate.preview_url })

  return (
    <MediaReviewPanel
      approve={review.approve}
      busy={review.busy}
      copy={copy}
      error={review.error}
      job={review.job}
      mediaId={mediaId}
      onPreview={preview}
      receive={review.receive}
      receiveResult={receiveResult}
      regenerate={review.regenerate}
      reload={review.reload}
    />
  )
}
