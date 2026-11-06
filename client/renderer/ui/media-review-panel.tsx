import type React from 'react'

import type { ApiSchemas } from '../../../shared/api.generated'
import type { MediaReviewCopy } from '../../../shared/media-review'

import { BTN_SUBTLE } from './palette'

/** 复核候选的纯展示：显示数据、操作状态与事件都由所属功能提供。 */
export function MediaReviewPanel({
  approve,
  busy,
  copy,
  error,
  job,
  mediaId,
  onPreview,
  receive,
  receiveResult,
  regenerate,
  reload
}: {
  approve: (slot: number, candidateId: string) => void
  busy: boolean
  copy: MediaReviewCopy
  error: string
  job: ApiSchemas['MediaJobView'] | null
  mediaId?: string
  onPreview: (candidate: ApiSchemas['MediaCandidateView']) => void
  receive: () => void
  receiveResult?: boolean
  regenerate: (slot: number) => void
  reload: () => void
}): React.JSX.Element {
  const editable = job && ['paused', 'failed'].includes(job.state) && !job.complete

  return (
    <span aria-busy={busy || (!job && !error)} className="flex w-full flex-col gap-2">
      {receiveResult ? (
        <button className={BTN_SUBTLE} disabled={busy} onClick={receive} type="button">
          {busy ? copy.receiving : copy.receive}
        </button>
      ) : null}
      {!job && !error ? <span className="text-xs text-muted">{copy.loading}</span> : null}
      {!receiveResult &&
        job?.slots
          .filter(slot => !mediaId || slot.media_id === mediaId)
          .map(slot => (
            <span className="flex flex-col gap-2" key={slot.slot}>
              {slot.label ? <span className="text-xs font-medium">{slot.label}</span> : null}
              {slot.candidates.map((candidate, index) => (
                <span className="rounded-lg border border-line-standard p-2 text-xs text-muted" key={candidate.id}>
                  <span className="mb-1 block">
                    {copy.candidate} {index + 1}
                  </span>
                  <button className={BTN_SUBTLE} onClick={() => onPreview(candidate)} type="button">
                    {copy.preview}
                  </button>
                  {candidate.review?.human_approval ? (
                    <span className="block">{copy.approved}</span>
                  ) : (
                    candidate.review?.required.map(name => {
                      const decision = candidate.review?.[name]

                      return (
                        <span className="block" key={name}>
                          {copy[name]} · {copy[decision?.verdict ?? 'review']}
                          {decision?.reason ? `：${decision.reason}` : ''}
                        </span>
                      )
                    })
                  )}
                  <button
                    className={BTN_SUBTLE}
                    disabled={
                      busy ||
                      !editable ||
                      !candidate.can_approve ||
                      Boolean(candidate.review?.human_approval) ||
                      slot.delivered ||
                      slot.status === 'succeeded'
                    }
                    onClick={() => approve(slot.slot, candidate.id)}
                    type="button"
                  >
                    {busy ? copy.working : copy.approve}
                  </button>
                </span>
              ))}
              {!slot.candidates.length ? <span className="text-xs text-muted">{copy.noCandidate}</span> : null}
              {slot.can_regenerate ? (
                <button
                  className={BTN_SUBTLE}
                  disabled={busy || !editable}
                  onClick={() => regenerate(slot.slot)}
                  type="button"
                >
                  {busy ? copy.working : copy.regenerate}
                </button>
              ) : null}
              {slot.error ? <span className="text-xs text-muted">{slot.error}</span> : null}
            </span>
          ))}
      {error ? (
        <span className="text-xs" role="alert">
          {error}
          <button className={BTN_SUBTLE} disabled={busy} onClick={reload} type="button">
            {copy.reload}
          </button>
        </span>
      ) : null}
    </span>
  )
}
