// 异步头像（半身像）重新生成的迟到事件缓冲——结果以 WS 事件形式到达本模块。

type Resolver<T> = (payload: T) => void

interface PendingMap<T> {
  pending: Map<string, Resolver<T>>
  late: Map<string, T>
  timedOut: Map<string, number>
}

// 设置在 60 秒以上的慢速供应商图生上限之上，避免合法请求被误判超时。
const REGEN_TIMEOUT_MS = 120_000
const TOMBSTONE_TTL_MS = 10 * 60_000

function pruneTombstones(map: Map<string, number>): void {
  const cutoff = Date.now() - TOMBSTONE_TTL_MS

  for (const [jobId, t] of map) {
    if (t < cutoff) {
      map.delete(jobId)
    }
  }
}

function makeAwaiter<T>(
  store: PendingMap<T>,
  makeTombstoneError: (jobId: string) => Error
): (jobId: string) => Promise<T> {
  return (jobId: string): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      const late = store.late.get(jobId)

      if (late) {
        store.late.delete(jobId)
        resolve(late)

        return
      }

      const timer = setTimeout(() => {
        if (store.pending.get(jobId) === settle) {
          store.pending.delete(jobId)
          store.late.delete(jobId)
          store.timedOut.set(jobId, Date.now())
          reject(makeTombstoneError(jobId))
        }
      }, REGEN_TIMEOUT_MS)

      const settle: Resolver<T> = payload => {
        clearTimeout(timer)
        resolve(payload)
      }

      store.pending.set(jobId, settle)
    })
}

function makeResolver<T>(store: PendingMap<T>): (payload: T & { job_id?: string }) => void {
  return payload => {
    const jobId = payload.job_id

    if (!jobId) {
      return
    }

    pruneTombstones(store.timedOut)

    if (store.timedOut.has(jobId)) {
      return
    }

    const cb = store.pending.get(jobId)

    if (cb) {
      store.pending.delete(jobId)
      cb(payload)

      return
    }

    store.late.set(jobId, payload)
  }
}

// 由 `avatar.regenerated` 事件承载的头像（半身像）重新生成载荷。
interface AvatarRegeneratedPayload {
  job_id?: string
  asset_url?: string | null
  id?: number
  error?: string
}

const avatarStore: PendingMap<AvatarRegeneratedPayload> = {
  pending: new Map(),
  late: new Map(),
  timedOut: new Map()
}

export const awaitAvatarRegeneration = makeAwaiter<AvatarRegeneratedPayload>(
  avatarStore,
  jobId => new Error(`avatar regeneration timed out for job ${jobId}`)
)
export const resolveAvatarRegeneration = makeResolver<AvatarRegeneratedPayload>(avatarStore)
