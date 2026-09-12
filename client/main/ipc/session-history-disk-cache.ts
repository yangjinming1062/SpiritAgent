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
  get: (userId: number, sessionId: string) => Promise<null | SessionHistorySnapshot>
  remove: (userId: number, sessionId: string) => Promise<void>
  save: (userId: number, sessionId: string, snapshot: SessionHistorySnapshot) => Promise<void>
}

export interface SessionHistoryDiskCacheOptions {
  spiritagentHome?: null | string
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
  if (!spiritagentHome) {
    throw new Error('createSessionHistoryDiskCache: spiritagentHome is required')
  }

  const cacheRoot = path.resolve(spiritagentHome, 'cache', 'sessions')
  const writeQueues = new Map<string, Promise<void>>()

  function userDir(userId: number): string {
    return path.join(cacheRoot, String(userId))
  }

  function sessionPath(userId: number, sessionId: string): string {
    return path.join(userDir(userId), `${sessionId}.json`)
  }

  async function ensureDir(dir: string): Promise<void> {
    await fsp.mkdir(dir, { recursive: true })
  }

  async function get(userId: number, sessionId: string): Promise<null | SessionHistorySnapshot> {
    if (!Number.isInteger(userId) || userId <= 0 || !isSessionIdSafe(sessionId)) {
      return null
    }

    try {
      const raw = await fsp.readFile(sessionPath(userId, sessionId), 'utf8')

      return sanitizeSnapshot(JSON.parse(raw) as Partial<SessionHistorySnapshot>)
    } catch {
      return null
    }
  }

  async function save(userId: number, sessionId: string, snapshot: Partial<SessionHistorySnapshot>): Promise<void> {
    if (!Number.isInteger(userId) || userId <= 0 || !isSessionIdSafe(sessionId)) {
      return
    }

    const sanitized = sanitizeSnapshot(snapshot)

    if (!sanitized) {
      return
    }

    const file = sessionPath(userId, sessionId)
    const key = `${userId}:${sessionId}`
    const prev = writeQueues.get(key) ?? Promise.resolve()

    const next = prev
      .then(async () => {
        await ensureDir(userDir(userId))
        const tmp = `${file}.${process.pid}.${Date.now()}.tmp`

        try {
          await fsp.writeFile(tmp, JSON.stringify(sanitized), 'utf8')
          await fsp.rename(tmp, file)
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

  async function remove(userId: number, sessionId: string): Promise<void> {
    if (!Number.isInteger(userId) || userId <= 0 || !isSessionIdSafe(sessionId)) {
      return
    }

    // 走同一写入队列：避免在途 save 在 rm 之后落盘，复活已删快照。
    const key = `${userId}:${sessionId}`
    const prev = writeQueues.get(key) ?? Promise.resolve()
    const next = prev.then(() => fsp.rm(sessionPath(userId, sessionId), { force: true })).catch(() => {})

    writeQueues.set(key, next)
    await next

    if (writeQueues.get(key) === next) {
      writeQueues.delete(key)
    }
  }

  async function clear(): Promise<void> {
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
