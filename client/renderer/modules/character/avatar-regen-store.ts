// 异步头像（半身像）重新生成的迟到事件缓冲——结果以 WS 事件形式到达本模块。

// 由 `avatar.regenerated` 事件承载的头像（半身像）重新生成载荷。
interface AvatarRegeneratedPayload {
  job_id?: string
  asset_url?: string | null
  id?: number
  error?: string
}

// 设置在 60 秒以上的慢速供应商图生上限之上，避免合法请求被误判超时。
const REGEN_TIMEOUT_MS = 120_000
const TOMBSTONE_TTL_MS = 10 * 60_000

const pending = new Map<string, (payload: AvatarRegeneratedPayload) => void>()
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

export function awaitAvatarRegeneration(jobId: string): Promise<AvatarRegeneratedPayload> {
  return new Promise<AvatarRegeneratedPayload>((resolve, reject) => {
    const arrived = late.get(jobId)

    if (arrived) {
      late.delete(jobId)
      resolve(arrived)

      return
    }

    const timer = setTimeout(() => {
      if (pending.get(jobId) === settle) {
        pending.delete(jobId)
        late.delete(jobId)
        timedOut.set(jobId, Date.now())
        reject(new Error(`avatar regeneration timed out for job ${jobId}`))
      }
    }, REGEN_TIMEOUT_MS)

    const settle = (payload: AvatarRegeneratedPayload): void => {
      clearTimeout(timer)
      resolve(payload)
    }

    pending.set(jobId, settle)
  })
}

export function resolveAvatarRegeneration(payload: AvatarRegeneratedPayload): void {
  const jobId = payload.job_id

  if (!jobId) {
    return
  }

  pruneTombstones()

  if (timedOut.has(jobId)) {
    return
  }

  const cb = pending.get(jobId)

  if (cb) {
    pending.delete(jobId)
    cb(payload)

    return
  }

  late.set(jobId, payload)
}
