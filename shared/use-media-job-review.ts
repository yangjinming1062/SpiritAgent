import { useCallback, useEffect, useRef, useState } from 'react'

import type { ApiArguments, ApiResponse } from './api'
import type { ApiSchemas } from './api.generated'
import { manualMediaRequest } from './media-review'

type Operation = 'media.get_media_job' | 'media.manually_handle_media'
type ManualRequest = ApiSchemas['MediaManualRequest']
interface ReviewApi {
  invoke<K extends Operation>(operation: K, ...args: ApiArguments<K>): Promise<ApiResponse<K>>
}

export function useMediaJobReview(api: ReviewApi, jobId: string, onChanged?: () => void | Promise<void>) {
  const [job, setJob] = useState<ApiSchemas['MediaJobView'] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const owner = useRef<{ busy: boolean; pending: ManualRequest | null; parentRefreshPending: boolean } | null>(null)
  const run = useCallback(
    async (body?: ManualRequest, changed?: () => void | Promise<void>) => {
      const current = owner.current
      if (!current || current.busy) {
        return
      }
      current.busy = true
      setBusy(true)
      setError('')
      const path = { job_id: jobId }
      const read = () => api.invoke('media.get_media_job', { path })
      const refreshParent = async () => {
        if (current.parentRefreshPending) {
          await changed?.()
          current.parentRefreshPending = false
        }
      }
      try {
        let updated: ApiSchemas['MediaJobView'] | null
        if (body) {
          current.pending = body
          try {
            updated = await api.invoke('media.manually_handle_media', { path, body })
            current.pending = null
            current.parentRefreshPending = true
          } catch (cause) {
            if (owner.current !== current) {
              return
            }
            setError(cause instanceof Error ? cause.message : String(cause))
            updated = await read().catch(() => null)
          }
        } else {
          await refreshParent()
          if (owner.current !== current) {
            return
          }
          updated = await read()
        }
        if (!updated || owner.current !== current) {
          return
        }
        setJob(updated)
        // 回包丢失后，任务修订推进也会改变所属列表；原修订仍允许使用同一请求身份重试。
        if (current.pending && updated.revision > current.pending.expected_revision) {
          current.pending = null
          current.parentRefreshPending = true
        }
        await refreshParent()
      } catch (cause) {
        if (owner.current === current) {
          setError(cause instanceof Error ? cause.message : String(cause))
        }
      } finally {
        current.busy = false
        if (owner.current === current) {
          setBusy(false)
        }
      }
    },
    [api, jobId]
  )

  useEffect(() => {
    owner.current = { busy: false, pending: null, parentRefreshPending: false }
    setJob(null)
    void run()
    return () => {
      owner.current = null
    }
  }, [run])

  const change = (slot: number, action: ManualRequest['action'], candidateId?: string) => {
    if (job?.id === jobId && owner.current && !owner.current.busy) {
      void run(manualMediaRequest(job, slot, action, candidateId, owner.current.pending), onChanged)
    }
  }
  return {
    job,
    busy,
    error,
    approve: (slot: number, candidateId: string) => change(slot, 'approve', candidateId),
    regenerate: (slot: number) => change(slot, 'regenerate'),
    reload: () => void run(undefined, onChanged)
  }
}
