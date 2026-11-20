import { useEffect, useRef, useState } from 'react'

import { requestResource } from '@/shared/lib/resource-request'

import type { ApiSchemas } from '../../../../shared/api.generated'
import { manualMediaRequest } from '../../../../shared/media-review'

export interface MediaJobReview {
  job: ApiSchemas['MediaJobView'] | null
  busy: boolean
  error: string
  approve: (slot: number, candidateId: string) => void
  regenerate: (slot: number) => void
  reload: () => void
}

/** 用户在素材详情打开候选后，可沿原任务选用或重新生成。 */
export function useMediaJobReview({
  jobId,
  onChanged
}: {
  jobId: string
  onChanged?: () => void | Promise<void>
}): MediaJobReview {
  const [job, setJob] = useState<ApiSchemas['MediaJobView'] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const epoch = useRef(0)
  const pending = useRef<ApiSchemas['MediaManualRequest'] | null>(null)

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
  }, [jobId])

  /** 只读刷新：始终查询任务，不执行接收结果等业务变更。 */
  const reload = async (): Promise<void> => {
    if (busy) {
      return
    }

    const version = epoch.current
    setBusy(true)
    setError('')

    try {
      const updated = await requestResource('media.get_media_job', { path: { job_id: jobId } })

      if (epoch.current === version) {
        setJob(updated)
      }
    } catch (cause) {
      if (epoch.current === version) {
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    } finally {
      if (epoch.current === version) {
        setBusy(false)
      }
    }
  }

  async function run(slot: number, action: 'approve' | 'regenerate', candidateId?: string): Promise<void> {
    if (busy || !job) {
      return
    }

    const version = epoch.current
    setBusy(true)
    setError('')

    try {
      const body = manualMediaRequest(job, slot, action, candidateId, pending.current)
      pending.current = body
      const updated = await requestResource('media.manually_handle_media', { path: { job_id: jobId }, body })
      pending.current = null

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

  return {
    job,
    busy,
    error,
    approve: (slot, candidateId) => void run(slot, 'approve', candidateId),
    regenerate: slot => void run(slot, 'regenerate'),
    reload: () => void reload()
  }
}
