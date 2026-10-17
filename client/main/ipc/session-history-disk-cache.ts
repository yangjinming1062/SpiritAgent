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

function parseSnapshot(value: unknown): null | SessionHistorySnapshot {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }

  const input = value as Partial<SessionHistorySnapshot>

  if (
    !Array.isArray(input.messages) ||
    !isFiniteNumber(input.writtenAt) ||
    (input.info !== undefined && (!input.info || typeof input.info !== 'object' || Array.isArray(input.info)))
  ) {
    return null
  }

  return {
    info: input.info,
    messages: input.messages,
    writtenAt: input.writtenAt
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

      return isCurrent() ? parseSnapshot(JSON.parse(raw)) : null
    } catch {
      return null
    }
  }

  async function save(accountId: string, sessionId: string, snapshot: SessionHistorySnapshot): Promise<void> {
    if (!isSafe(accountId, sessionId)) {
      return
    }

    const parsed = parseSnapshot(snapshot)

    if (!parsed) {
      return
    }

    const file = sessionPath(accountId, sessionId)

    await queue.enqueue(accountId, sessionId, async isCurrent => {
      if (isCurrent()) {
        await atomicWriteFile(file, JSON.stringify(parsed), isCurrent)
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
