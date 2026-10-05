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

// 与同步生图请求共用 25 小时等待预算，覆盖本地任务最长 24 小时和生成前后处理。
const REGEN_TIMEOUT_MS = 25 * 60 * 60_000
const TOMBSTONE_TTL_MS = 10 * 60_000

interface PendingRegeneration {
  cancel: (error: Error) => void
  settle: (payload: AvatarRegeneratedPayload) => void
}

const pending = new Map<string, PendingRegeneration>()
const late = new Map<string, AvatarRegeneratedPayload>()
const timedOut = new Map<string, number>()

function pruneTombstones(): void {
  const cutoff = Date.now() - TOMBSTONE_TTL_MS

  for (const [jobId, t] of timedOut) {
    if (t < cutoff) {
      timedOut.delete(jobId)
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

    const timer = setTimeout(() => {
      if (pending.get(jobId)?.settle === settle) {
        cancel(new Error(`avatar regeneration timed out for job ${jobId}`))
      }
    }, REGEN_TIMEOUT_MS)

    const cancel = (error: Error): void => {
      clearTimeout(timer)
      pending.delete(jobId)
      late.delete(jobId)
      timedOut.set(jobId, Date.now())
      reject(error)
    }

    const settle = (payload: AvatarRegeneratedPayload): void => {
      clearTimeout(timer)
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

  if (timedOut.has(jobId)) {
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
