// 异步头像（半身像）重新生成的迟到事件缓冲——结果以 WS 事件形式到达本模块。

import { registerStorageClearHandler } from '@/shared/lib/storage'
import { $gatewayState } from '@/shared/store/gateway'

// 由 `avatar.regenerated` 事件承载的头像（半身像）重新生成载荷。
interface AvatarRegeneratedPayload {
  job_id?: string
  asset_url?: string | null
  id?: number
  error?: string
}

const TOMBSTONE_TTL_MS = 10 * 60_000

interface PendingRegeneration {
  cancel: (error: Error) => void
  settle: (payload: AvatarRegeneratedPayload) => void
}

const pending = new Map<string, PendingRegeneration>()
const late = new Map<string, AvatarRegeneratedPayload>()
const cancelled = new Map<string, number>()

function pruneTombstones(): void {
  const cutoff = Date.now() - TOMBSTONE_TTL_MS

  for (const [jobId, t] of cancelled) {
    if (t < cutoff) {
      cancelled.delete(jobId)
    }
  }
}

function cancelPendingRegenerations(message: string): void {
  for (const wait of pending.values()) {
    wait.cancel(new Error(message))
  }

  late.clear()
}

registerStorageClearHandler(() => cancelPendingRegenerations('Avatar regeneration account changed'))

$gatewayState.listen(state => {
  if (state === 'closed' || state === 'error') {
    cancelPendingRegenerations('Avatar regeneration connection closed; reload the saved avatar before retrying')
  }
})

export function awaitAvatarRegeneration(jobId: string): Promise<AvatarRegeneratedPayload> {
  return new Promise<AvatarRegeneratedPayload>((resolve, reject) => {
    if ($gatewayState.get() !== 'open') {
      reject(new Error('Avatar regeneration connection is not open'))

      return
    }

    if (pending.has(jobId)) {
      reject(new Error(`Avatar regeneration already has a waiter for job ${jobId}`))

      return
    }

    const arrived = late.get(jobId)

    if (arrived) {
      late.delete(jobId)
      resolve(arrived)

      return
    }

    const cancel = (error: Error): void => {
      pending.delete(jobId)
      late.delete(jobId)
      cancelled.set(jobId, Date.now())
      reject(error)
    }

    const settle = (payload: AvatarRegeneratedPayload): void => {
      resolve(payload)
    }

    pending.set(jobId, { cancel, settle })
  })
}

export function resolveAvatarRegeneration(payload: AvatarRegeneratedPayload): void {
  const jobId = payload.job_id

  if (!jobId || $gatewayState.get() !== 'open') {
    return
  }

  pruneTombstones()

  if (cancelled.has(jobId)) {
    return
  }

  const cb = pending.get(jobId)

  if (cb) {
    pending.delete(jobId)
    cb.settle(payload)

    return
  }

  late.set(jobId, payload)
}
