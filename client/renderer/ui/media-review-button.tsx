import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { requestResource } from '@/shared/lib/resource-request'
import { presentationPorts } from '@/shared/presentation-ports'
import { $locale } from '@/shared/store/locale'

import type { ApiSchemas } from '../../../shared/api.generated'
import { manualMediaRequest, mediaReviewCopy } from '../../../shared/media-review'

import { BTN_SUBTLE } from './palette'

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
  const [job, setJob] = useState<ApiSchemas['MediaJobView'] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const epoch = useRef(0)
  const pending = useRef<ApiSchemas['MediaManualRequest'] | null>(null)
  const copy = mediaReviewCopy[useStore($locale)]

  useEffect(() => {
    const version = ++epoch.current
    setJob(null)
    setBusy(false)
    setError('')
    pending.current = null
    void requestResource('media.get_media_job', { path: { job_id: jobId } })
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
  }, [jobId, mediaId])

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
        updated = await requestResource('media.receive_media_result', { path: { job_id: jobId } })
      } else if (job && slot !== undefined && action) {
        const body = manualMediaRequest(job, slot, action, candidateId, pending.current)
        pending.current = body
        updated = await requestResource('media.manually_handle_media', { path: { job_id: jobId }, body })
        pending.current = null
      } else {
        updated = await requestResource('media.get_media_job', { path: { job_id: jobId } })
      }

      if (epoch.current !== version) {
        return
      }

      setJob(updated)
      await onChanged?.()
    } catch (cause) {
      if (epoch.current === version) {
        setError(cause instanceof Error ? cause.message : String(cause))
        const refreshed = await requestResource('media.get_media_job', { path: { job_id: jobId } }).catch(() => null)

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
    <span aria-busy={busy || (!job && !error)} className="flex w-full flex-col gap-2">
      {receiveResult ? (
        <button className={BTN_SUBTLE} disabled={busy} onClick={() => void run()} type="button">
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
                  <button
                    className={BTN_SUBTLE}
                    onClick={() =>
                      presentationPorts().openMediaViewer({ type: candidate.media_type, url: candidate.preview_url })
                    }
                    type="button"
                  >
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
                    onClick={() => void run(slot.slot, 'approve', candidate.id)}
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
                  onClick={() => void run(slot.slot, 'regenerate')}
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
          <button className={BTN_SUBTLE} disabled={busy} onClick={() => void run()} type="button">
            {copy.reload}
          </button>
        </span>
      ) : null}
    </span>
  )
}
