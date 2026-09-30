import fsp from 'node:fs/promises'
import path from 'node:path'

import type { SessionHistorySnapshot } from '@ipc/contracts'

import { atomicWriteFile, isAccountId, isFiniteNumber } from '../shared/utils'

import { createAccountQueue } from './account-queue'

// 快照 messages 只保留展示所需的 SessionMessage 形状；主进程不做业务投影。
export interface SessionHistoryDiskCache {
  clear: (accountId: string) => Promise<void>
  get: (accountId: string, sessionId: string) => Promise<null | SessionHistorySnapshot>
  remove: (accountId: string, sessionId: string) => Promise<void>
  save: (accountId: string, sessionId: string, snapshot: SessionHistorySnapshot) => Promise<void>
}

export interface SessionHistoryDiskCacheOptions {
  spiritagentHome: string
}

function lastIdFromMessages(messages: unknown[]): null | number {
  let last: null | number = null

  for (const raw of messages) {
    if (!raw || typeof raw !== 'object') {
      continue
    }

    const id = (raw as { id?: unknown }).id

    if (isFiniteNumber(id)) {
      last = last === null || id > last ? id : last
    }
  }

  return last
}

function sanitizeSnapshot(input: Partial<SessionHistorySnapshot> | null | undefined): null | SessionHistorySnapshot {
  if (!input || typeof input !== 'object' || !Array.isArray(input.messages)) {
    return null
  }

  const currentSeq = isFiniteNumber(input.currentSeq) ? input.currentSeq : 0
  const truncated = input.truncated === true
  const nextCursor = typeof input.nextCursor === 'string' && input.nextCursor ? input.nextCursor : null
  const info = input.info && typeof input.info === 'object' ? (input.info as Record<string, unknown>) : undefined

  const writtenAt = isFiniteNumber(input.writtenAt) ? input.writtenAt : Date.now()
  const lastMessageId = isFiniteNumber(input.lastMessageId) ? input.lastMessageId : lastIdFromMessages(input.messages)

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
  const queue = createAccountQueue()

  function accountDir(accountId: string): string {
    return path.join(cacheRoot, accountId)
  }

  function sessionPath(accountId: string, sessionId: string): string {
    return path.join(accountDir(accountId), `${sessionId}.json`)
  }

  function isSafe(accountId: string, sessionId: string): boolean {
    return isAccountId(accountId) && /^\d+$/.test(sessionId)
  }

  async function get(accountId: string, sessionId: string): Promise<null | SessionHistorySnapshot> {
    if (!isSafe(accountId, sessionId)) {
      return null
    }

    const isCurrent = queue.generation(accountId)

    try {
      await queue.clearing(accountId)
      const raw = await fsp.readFile(sessionPath(accountId, sessionId), 'utf8')

      return isCurrent() ? sanitizeSnapshot(JSON.parse(raw) as Partial<SessionHistorySnapshot>) : null
    } catch {
      return null
    }
  }

  async function save(accountId: string, sessionId: string, snapshot: Partial<SessionHistorySnapshot>): Promise<void> {
    if (!isSafe(accountId, sessionId)) {
      return
    }

    const sanitized = sanitizeSnapshot(snapshot)

    if (!sanitized) {
      return
    }

    const file = sessionPath(accountId, sessionId)

    await queue.enqueue(accountId, sessionId, async isCurrent => {
      if (isCurrent()) {
        await atomicWriteFile(file, JSON.stringify(sanitized), isCurrent)
      }
    })
  }

  async function remove(accountId: string, sessionId: string): Promise<void> {
    if (!isSafe(accountId, sessionId)) {
      return
    }

    // 走同一写入队列：避免在途 save 在 rm 之后落盘，复活已删快照。
    await queue.enqueue(accountId, sessionId, async isCurrent => {
      if (isCurrent()) {
        await fsp.rm(sessionPath(accountId, sessionId), { force: true })
      }
    })
  }

  function clear(accountId: string): Promise<void> {
    return isAccountId(accountId)
      ? queue.clear(accountId, accountDir(accountId))
      : Promise.reject(new Error('Invalid history cache account'))
  }

  return {
    clear,
    get,
    remove,
    save
  }
}
