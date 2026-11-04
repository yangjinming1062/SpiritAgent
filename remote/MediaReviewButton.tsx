import type { ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import { manualMediaRequest, mediaReviewCopy } from '../shared/media-review'

import type { RemoteApi } from './api'
import { Media } from './ui'

export function MediaReviewButton({
  api,
  jobId,
  mediaId,
  receiveResult,
  onChanged
}: {
  api: RemoteApi
  jobId: string
  mediaId?: string
  receiveResult?: boolean
  onChanged?: () => void | Promise<void>
}): ReactElement {
  const [job, setJob] = useState<ApiSchemas['MediaJobView'] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const epoch = useRef(0)
  const pending = useRef<ApiSchemas['MediaManualRequest'] | null>(null)
  const copy = mediaReviewCopy.zh
  useEffect(() => {
    const version = ++epoch.current
    setJob(null)
    setBusy(false)
    setError('')
    pending.current = null
    void api
      .invoke('media.get_media_job', { path: { job_id: jobId } })
      .then(value => {
        if (epoch.current === version) {
          setJob(value)
        }
      })
      .catch(cause => {
        if (epoch.current === version) {
          setError(cause instanceof Error ? cause.message : String(cause))
        }
      })
    return () => {
      epoch.current = version + 1
    }
  }, [api, jobId, mediaId])
  const run = async (slot?: number, action?: 'approve' | 'regenerate', candidateId?: string): Promise<void> => {
    if (busy) {
      return
    }
    const version = epoch.current
    setBusy(true)
    setError('')
    try {
      let updated: ApiSchemas['MediaJobView']
      if (receiveResult) {
        updated = await api.invoke('media.receive_media_result', { path: { job_id: jobId } })
      } else if (job && slot !== undefined && action) {
        const body = manualMediaRequest(job, slot, action, candidateId, pending.current)
        pending.current = body
        updated = await api.invoke('media.manually_handle_media', { path: { job_id: jobId }, body })
        pending.current = null
      } else {
        updated = await api.invoke('media.get_media_job', { path: { job_id: jobId } })
      }
      if (epoch.current !== version) {
        return
      }
      setJob(updated)
      await onChanged?.()
    } catch (cause) {
      if (epoch.current === version) {
        setError(cause instanceof Error ? cause.message : String(cause))
        const refreshed = await api.invoke('media.get_media_job', { path: { job_id: jobId } }).catch(() => null)
        if (epoch.current === version && refreshed) {
          setJob(refreshed)
        }
      }
    } finally {
      if (epoch.current === version) {
        setBusy(false)
      }
    }
  }
  const editable = job && ['paused', 'failed'].includes(job.state) && !job.complete
  return (
    <span aria-busy={busy || (!job && !error)} className="media-review-panel">
      {receiveResult ? (
        <button disabled={busy} onClick={() => void run()} type="button">
          {busy ? copy.receiving : copy.receive}
        </button>
      ) : null}
      {!job && !error ? <span>{copy.loading}</span> : null}
      {!receiveResult &&
        job?.slots
          .filter(slot => !mediaId || slot.media_id === mediaId)
          .map(slot => (
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
                    onClick={() => void run(slot.slot, 'approve', candidate.id)}
                    type="button"
                  >
                    {busy ? copy.working : copy.approve}
                  </button>
                </span>
              ))}
              {!slot.candidates.length ? <span>{copy.noCandidate}</span> : null}
              {slot.can_regenerate ? (
                <button disabled={busy || !editable} onClick={() => void run(slot.slot, 'regenerate')} type="button">
                  {busy ? copy.working : copy.regenerate}
                </button>
              ) : null}
              {slot.error ? <span>{slot.error}</span> : null}
            </span>
          ))}
      {error ? (
        <span role="alert">
          {error}
          <button disabled={busy} onClick={() => void run()} type="button">
            {copy.reload}
          </button>
        </span>
      ) : null}
    </span>
  )
}
