import fsp from 'node:fs/promises'
import path from 'node:path'

import { voicePlaybackKey, type VoicePlaybackRecord, type VoicePlaybackSnapshot } from '@ipc/contracts'

import { atomicWriteFile, isAccountId } from '../shared/utils'

import { createAccountQueue } from './account-queue'

interface PlaybackAccess {
  accountId: string
  sessionId: string
  isCurrent: () => boolean
}

function parseSnapshot(value: unknown): VoicePlaybackSnapshot {
  if (!value || typeof value !== 'object' || !('records' in value) || !('revision' in value)) {
    throw new Error('Invalid voice playback snapshot')
  }

  const { records, revision } = value

  if (
    !records ||
    typeof records !== 'object' ||
    Array.isArray(records) ||
    typeof revision !== 'number' ||
    !Number.isSafeInteger(revision) ||
    revision < 0
  ) {
    throw new Error('Invalid voice playback snapshot')
  }

  const entries: Record<string, VoicePlaybackRecord> = {}

  for (const [key, record] of Object.entries(records)) {
    if (
      !/^[1-9]\d*:\d+$/.test(key) ||
      !record ||
      typeof record !== 'object' ||
      typeof record.listened !== 'boolean' ||
      typeof record.positionSeconds !== 'number' ||
      !Number.isFinite(record.positionSeconds) ||
      record.positionSeconds < 0
    ) {
      throw new Error('Invalid voice playback record')
    }

    entries[key] = { listened: record.listened, positionSeconds: record.positionSeconds }
  }

  return { records: entries, revision }
}

export function createVoicePlaybackStore({ spiritagentHome }: { spiritagentHome: string }) {
  const root = path.join(spiritagentHome, 'cache', 'voice-playback')
  const snapshots = new Map<string, VoicePlaybackSnapshot>()
  const queue = createAccountQueue()

  async function transact(
    access: PlaybackAccess,
    change?: (snapshot: VoicePlaybackSnapshot) => VoicePlaybackSnapshot
  ): Promise<VoicePlaybackSnapshot | null> {
    const { accountId, sessionId, isCurrent } = access

    if (!isAccountId(accountId) || !/^\d+$/.test(sessionId)) {
      throw new Error('Invalid voice playback scope')
    }

    const key = `${accountId}:${sessionId}`
    const file = path.join(root, accountId, `${sessionId}.json`)

    return queue.enqueue(accountId, sessionId, async sameAccountGeneration => {
      const current = (): boolean => sameAccountGeneration() && isCurrent()

      if (!current()) {
        return null
      }

      let snapshot = snapshots.get(key)

      if (!snapshot) {
        try {
          snapshot = parseSnapshot(JSON.parse(await fsp.readFile(file, 'utf8')))
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
            throw error
          }

          snapshot = { records: {}, revision: 0 }
        }
      }

      if (!current()) {
        return null
      }

      if (change) {
        snapshot = change(snapshot)

        if (!(await atomicWriteFile(file, JSON.stringify(snapshot), current))) {
          return null
        }
      }

      if (!current()) {
        return null
      }

      snapshots.set(key, snapshot)

      return snapshot
    })
  }

  return {
    get: (access: PlaybackAccess) => transact(access),
    update: (access: PlaybackAccess, messageId: number, bubbleIndex: number, record: VoicePlaybackRecord) =>
      transact(access, snapshot => {
        const key = voicePlaybackKey(messageId, bubbleIndex)

        return {
          records: {
            ...snapshot.records,
            [key]: { ...record, listened: record.listened || snapshot.records[key]?.listened === true }
          },
          revision: snapshot.revision + 1
        }
      }),
    remove: (access: PlaybackAccess, messageIds?: number[]) => {
      const removed = messageIds && new Set(messageIds)

      return transact(access, snapshot => ({
        records: removed
          ? Object.fromEntries(
              Object.entries(snapshot.records).filter(([key]) => !removed.has(Number(key.split(':')[0])))
            )
          : {},
        revision: snapshot.revision + 1
      }))
    },
    flush: queue.flush,
    clear: (accountId: string): Promise<void> => {
      if (!isAccountId(accountId)) {
        return Promise.reject(new Error('Invalid voice playback account'))
      }

      for (const key of snapshots.keys()) {
        if (key.startsWith(`${accountId}:`)) {
          snapshots.delete(key)
        }
      }

      return queue.clear(accountId, path.join(root, accountId))
    }
  }
}

export type VoicePlaybackStore = ReturnType<typeof createVoicePlaybackStore>
