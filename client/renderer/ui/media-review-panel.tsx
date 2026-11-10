import type React from 'react'
import type { ReactNode } from 'react'

import type { ApiSchemas } from '../../../shared/api.generated'
import type { MediaReviewCopy } from '../../../shared/media-review'

import { BTN_SUBTLE } from './palette'
import { Pill, type PillTone } from './primitives'

const VERDICT_TONE: Record<ApiSchemas['ReviewDecision']['verdict'], PillTone> = {
  pass: 'success',
  review: 'warning',
  fail: 'danger'
}

/** 复核候选的纯展示：媒体、检查结论与操作状态由所属功能提供，面板只排布。 */
export function MediaReviewPanel({
  approve,
  busy,
  copy,
  error,
  job,
  mediaId,
  receive,
  receiveResult,
  regenerate,
  reload,
  renderMedia
}: {
  approve: (slot: number, candidateId: string) => void
  busy: boolean
  copy: MediaReviewCopy
  error: string
  job: ApiSchemas['MediaJobView'] | null
  mediaId?: string
  receive: () => void
  receiveResult?: boolean
  regenerate: (slot: number) => void
  reload: () => void
  renderMedia?: (candidate: ApiSchemas['MediaCandidateView']) => ReactNode
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
              {slot.candidates.map((candidate, index) => {
                const review = candidate.review

                const notes = (review?.required ?? []).flatMap(name => {
                  const reason = review?.[name]?.reason.trim()

                  return reason ? [{ name, reason }] : []
                })

                return (
                  <span
                    className="flex flex-col gap-2 rounded-lg border border-line-standard p-2.5 text-xs text-muted"
                    key={candidate.id}
                  >
                    <span className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[11px] font-medium text-strong">
                        {copy.candidate} {index + 1}
                      </span>
                      {review?.human_approval ? (
                        <span className="text-[10px] text-success">{copy.approved}</span>
                      ) : null}
                    </span>
                    {renderMedia?.(candidate)}
                    {review?.required.length ? (
                      <span className="flex flex-wrap gap-1.5">
                        {review.required.map(name => {
                          const decision = review[name]

                          return (
                            <Pill key={name} tone={decision ? VERDICT_TONE[decision.verdict] : 'muted'}>
                              {copy[name]} · {copy[decision?.verdict ?? 'review']}
                            </Pill>
                          )
                        })}
                      </span>
                    ) : null}
                    {notes.length > 0 ? (
                      <details className="text-[11px] leading-relaxed">
                        <summary className="w-fit cursor-pointer select-none text-faint hover:text-muted">
                          {copy.notes}
                        </summary>
                        <ul className="mt-1.5 space-y-1">
                          {notes.map(note => (
                            <li key={note.name}>
                              <span className="text-faint">{copy[note.name]}：</span>
                              {note.reason}
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                    <span className="flex flex-wrap gap-2">
                      <button
                        className={BTN_SUBTLE}
                        disabled={
                          busy ||
                          !editable ||
                          !candidate.can_approve ||
                          Boolean(review?.human_approval) ||
                          slot.delivered ||
                          slot.status === 'succeeded'
                        }
                        onClick={() => approve(slot.slot, candidate.id)}
                        type="button"
                      >
                        {busy ? copy.working : copy.approve}
                      </button>
                    </span>
                  </span>
                )
              })}
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
