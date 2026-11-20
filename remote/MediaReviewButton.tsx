import type { ReactElement } from 'react'
import { useState } from 'react'

import { mediaReviewCopy } from '../shared/media-review'
import { useMediaJobReview } from '../shared/use-media-job-review'

import type { RemoteApi } from './api'
import { Media } from './ui'

interface ReviewProps {
  api: RemoteApi
  jobId: string
  onChanged?: () => void | Promise<void>
}

export function MediaReviewButton(props: ReviewProps): ReactElement {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button aria-expanded={open} onClick={() => setOpen(value => !value)} type="button">
        {mediaReviewCopy.zh.showCandidates}
      </button>
      {open ? <ReviewDetails key={props.jobId} {...props} /> : null}
    </>
  )
}

function ReviewDetails({ api, jobId, onChanged }: ReviewProps): ReactElement {
  const { job, busy, error, approve, regenerate, reload } = useMediaJobReview(api, jobId, onChanged)
  const copy = mediaReviewCopy.zh
  const editable = job && ['paused', 'failed'].includes(job.state) && !job.complete
  return (
    <span aria-busy={busy || (!job && !error)} className="media-review-panel">
      {!job && !error ? <span>{copy.loading}</span> : null}
      {job?.slots.map(slot => (
        <span className="media-review-slot" key={slot.slot}>
          {slot.label ? <strong>{slot.label}</strong> : null}
          {slot.candidates.map((candidate, index) => (
            <span className="media-review-candidate" key={candidate.id}>
              <span>
                {copy.candidate} {index + 1}
              </span>
              <Media type={candidate.media_type} url={candidate.preview_url} />
              {candidate.review?.human_approval ? (
                <span>{copy.approved}</span>
              ) : (
                candidate.review?.required.map(name => {
                  const decision = candidate.review?.[name]
                  return (
                    <span className="media-review-decision" key={name}>
                      {copy[name]} · {copy[decision?.verdict ?? 'review']}
                      {decision?.reason ? `：${decision.reason}` : ''}
                    </span>
                  )
                })
              )}
              <button
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
          {!slot.candidates.length ? <span>{copy.noCandidate}</span> : null}
          {slot.can_regenerate ? (
            <button disabled={busy || !editable} onClick={() => regenerate(slot.slot)} type="button">
              {busy ? copy.working : copy.regenerate}
            </button>
          ) : null}
          {slot.error ? <span>{slot.error}</span> : null}
        </span>
      ))}
      {error ? (
        <span role="alert">
          {error}
          <button disabled={busy} onClick={reload} type="button">
            {copy.reload}
          </button>
        </span>
      ) : null}
    </span>
  )
}
