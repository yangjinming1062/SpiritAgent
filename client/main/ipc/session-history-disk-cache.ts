import fsp from 'node:fs/promises'
import path from 'node:path'

// 会话历史本地快照：与后端 SessionResumeResult 的可持久化子集对齐。
// messages 只保留展示所需的 SessionMessage 形状；主进程不做业务投影。
export interface SessionHistorySnapshot {
  currentSeq: number
  info?: Record<string, unknown>
  lastMessageId: null | number
  messages: unknown[]
  nextCursor: null | string
  truncated: boolean
  writtenAt: number
}

export interface SessionHistoryDiskCache {
  clear: () => Promise<void>
  get: (accountId: string, sessionId: string) => Promise<null | SessionHistorySnapshot>
  remove: (accountId: string, sessionId: string) => Promise<void>
  save: (accountId: string, sessionId: string, snapshot: SessionHistorySnapshot) => Promise<void>
}

export interface SessionHistoryDiskCacheOptions {
  spiritagentHome: string
}

function isAccountIdSafe(accountId: string): boolean {
  return /^[a-f0-9]{64}$/.test(accountId)
}

function isSessionIdSafe(sessionId: string): boolean {
  return /^\d+$/.test(sessionId)
}

function lastIdFromMessages(messages: unknown[]): null | number {
  let last: null | number = null

  for (const raw of messages) {
    if (!raw || typeof raw !== 'object') {
      continue
    }

    const id = (raw as { id?: unknown }).id

    if (typeof id === 'number' && Number.isFinite(id)) {
      last = last === null || id > last ? id : last
    }
  }

  return last
}

function sanitizeSnapshot(input: Partial<SessionHistorySnapshot> | null | undefined): null | SessionHistorySnapshot {
  if (!input || typeof input !== 'object' || !Array.isArray(input.messages)) {
    return null
  }

  const currentSeq = typeof input.currentSeq === 'number' && Number.isFinite(input.currentSeq) ? input.currentSeq : 0
  const truncated = input.truncated === true
  const nextCursor = typeof input.nextCursor === 'string' && input.nextCursor ? input.nextCursor : null
  const info = input.info && typeof input.info === 'object' ? (input.info as Record<string, unknown>) : undefined

  const writtenAt =
    typeof input.writtenAt === 'number' && Number.isFinite(input.writtenAt) ? input.writtenAt : Date.now()

  const lastMessageId =
    typeof input.lastMessageId === 'number' && Number.isFinite(input.lastMessageId)
      ? input.lastMessageId
      : lastIdFromMessages(input.messages)

  return {
    currentSeq,
    info,
    lastMessageId,
    messages: input.messages,
    nextCursor,
    truncated,
    writtenAt
  }
}

export function createSessionHistoryDiskCache({
  spiritagentHome
}: SessionHistoryDiskCacheOptions): SessionHistoryDiskCache {
  const cacheRoot = path.resolve(spiritagentHome, 'cache', 'sessions')
  const writeQueues = new Map<string, Promise<void>>()
  let epoch = 0

  function accountDir(accountId: string): string {
    return path.join(cacheRoot, accountId)
  }

  function sessionPath(accountId: string, sessionId: string): string {
    return path.join(accountDir(accountId), `${sessionId}.json`)
  }

  async function ensureDir(dir: string): Promise<void> {
    await fsp.mkdir(dir, { recursive: true })
  }

  async function get(accountId: string, sessionId: string): Promise<null | SessionHistorySnapshot> {
    if (!isAccountIdSafe(accountId) || !isSessionIdSafe(sessionId)) {
      return null
    }

    try {
      const raw = await fsp.readFile(sessionPath(accountId, sessionId), 'utf8')

      return sanitizeSnapshot(JSON.parse(raw) as Partial<SessionHistorySnapshot>)
    } catch {
      return null
    }
  }

  async function save(accountId: string, sessionId: string, snapshot: Partial<SessionHistorySnapshot>): Promise<void> {
    if (!isAccountIdSafe(accountId) || !isSessionIdSafe(sessionId)) {
      return
    }

    const sanitized = sanitizeSnapshot(snapshot)

    if (!sanitized) {
      return
    }

    const file = sessionPath(accountId, sessionId)
    const key = `${accountId}:${sessionId}`
    const saveEpoch = epoch
    const prev = writeQueues.get(key) ?? Promise.resolve()

    const next = prev
      .then(async () => {
        if (saveEpoch !== epoch) {
          return
        }

        await ensureDir(accountDir(accountId))

        if (saveEpoch !== epoch) {
          return
        }

        const tmp = `${file}.${process.pid}.${Date.now()}.tmp`

        try {
          await fsp.writeFile(tmp, JSON.stringify(sanitized), 'utf8')

          if (saveEpoch === epoch) {
            await fsp.rename(tmp, file)
          } else {
            await fsp.unlink(tmp).catch(() => {})
          }
        } catch {
          await fsp.unlink(tmp).catch(() => {})
        }
      })
      .catch(() => {})

    writeQueues.set(key, next)
    await next

    if (writeQueues.get(key) === next) {
      writeQueues.delete(key)
    }
  }

  async function remove(accountId: string, sessionId: string): Promise<void> {
    if (!isAccountIdSafe(accountId) || !isSessionIdSafe(sessionId)) {
      return
    }

    // 走同一写入队列：避免在途 save 在 rm 之后落盘，复活已删快照。
    const key = `${accountId}:${sessionId}`
    const removeEpoch = epoch
    const prev = writeQueues.get(key) ?? Promise.resolve()

    const next = prev
      .then(() => (removeEpoch === epoch ? fsp.rm(sessionPath(accountId, sessionId), { force: true }) : undefined))
      .catch(() => {})

    writeQueues.set(key, next)
    await next

    if (writeQueues.get(key) === next) {
      writeQueues.delete(key)
    }
  }

  async function clear(): Promise<void> {
    epoch += 1
    await Promise.allSettled([...writeQueues.values()])
    writeQueues.clear()
    await fsp.rm(cacheRoot, { recursive: true, force: true }).catch(() => {})
  }

  return {
    clear,
    get,
    remove,
    save
  }
}
