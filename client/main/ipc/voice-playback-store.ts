import fsp from 'node:fs/promises'
import path from 'node:path'

import { voicePlaybackKey, type VoicePlaybackRecord, type VoicePlaybackSnapshot } from '@ipc/contracts'

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
  const queues = new Map<string, Promise<unknown>>()
  const snapshots = new Map<string, VoicePlaybackSnapshot>()
  const epochs = new Map<string, number>()
  const clearing = new Map<string, Promise<void>>()

  async function transact(
    access: PlaybackAccess,
    change?: (snapshot: VoicePlaybackSnapshot) => VoicePlaybackSnapshot
  ): Promise<VoicePlaybackSnapshot | null> {
    const { accountId, sessionId, isCurrent } = access

    if (!/^[a-f0-9]{64}$/.test(accountId) || !/^\d+$/.test(sessionId)) {
      throw new Error('Invalid voice playback scope')
    }

    const key = `${accountId}:${sessionId}`
    const generation = epochs.get(accountId)
    const current = (): boolean => generation === epochs.get(accountId) && isCurrent()
    const previous = Promise.all([queues.get(key), clearing.get(accountId)])
    const file = path.join(root, accountId, `${sessionId}.json`)

    const task = previous.then(async () => {
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
        await fsp.mkdir(path.dirname(file), { recursive: true })
        const temporary = `${file}.${process.pid}.tmp`

        try {
          await fsp.writeFile(temporary, JSON.stringify(snapshot), 'utf8')

          if (!current()) {
            return null
          }

          await fsp.rename(temporary, file)
        } finally {
          await fsp.rm(temporary, { force: true })
        }
      }

      if (!current()) {
        return null
      }

      snapshots.set(key, snapshot)

      return snapshot
    })

    const tail = task.catch(() => {})
    queues.set(key, tail)

    try {
      return await task
    } finally {
      if (queues.get(key) === tail) {
        queues.delete(key)
      }
    }
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
    flush: async (): Promise<void> => {
      await Promise.all([...queues.values(), ...clearing.values()])
    },
    clear: (accountId: string): Promise<void> => {
      if (!/^[a-f0-9]{64}$/.test(accountId)) {
        return Promise.reject(new Error('Invalid voice playback account'))
      }

      epochs.set(accountId, (epochs.get(accountId) ?? 0) + 1)

      for (const key of snapshots.keys()) {
        if (key.startsWith(`${accountId}:`)) {
          snapshots.delete(key)
        }
      }

      const pending = [...queues].filter(([key]) => key.startsWith(`${accountId}:`)).map(([, task]) => task)

      const task = Promise.allSettled([...pending, clearing.get(accountId)]).then(() =>
        fsp.rm(path.join(root, accountId), { force: true, recursive: true })
      )

      clearing.set(accountId, task)

      return task
    }
  }
}

export type VoicePlaybackStore = ReturnType<typeof createVoicePlaybackStore>
