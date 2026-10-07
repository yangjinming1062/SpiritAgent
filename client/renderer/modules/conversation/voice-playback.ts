import { map } from 'nanostores'

import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import {
  voicePlaybackKey,
  type VoicePlaybackRecord,
  type VoicePlaybackScope,
  type VoicePlaybackSnapshot
} from '@ipc/contracts'

function createPlaybackStore() {
  const $voicePlaybackRecords = map<Record<string, VoicePlaybackRecord>>({})
  let scope: VoicePlaybackScope | null = null
  let confirmed: VoicePlaybackSnapshot = { records: {}, revision: -1 }
  let loading: Promise<void> = Promise.resolve()
  let reportedFailure = false
  const pending = new Map<string, VoicePlaybackRecord>()

  function applySnapshot(snapshot: VoicePlaybackSnapshot): void {
    if (snapshot.revision < confirmed.revision) {
      return
    }

    confirmed = snapshot
    const records = { ...snapshot.records }

    for (const [key, record] of pending) {
      records[key] = { ...record, listened: record.listened || records[key]?.listened === true }
    }

    const previous = $voicePlaybackRecords.get()
    const previousKeys = Object.keys(previous)

    if (previousKeys.length === 0) {
      $voicePlaybackRecords.set(records)

      return
    }

    // 全量 IPC 快照按键应用，避免每秒进度广播使所有语音条重新渲染。
    for (const key of new Set([...previousKeys, ...Object.keys(records)])) {
      const record = records[key]

      if (previous[key]?.listened !== record?.listened || previous[key]?.positionSeconds !== record?.positionSeconds) {
        $voicePlaybackRecords.setKey(key, record)
      }
    }
  }

  function reportFailure(error: unknown): void {
    log.warn('voice-playback', 'Could not persist playback state', error)

    if (!reportedFailure) {
      reportedFailure = true
      notifyError(error, getStrings().chat.voice.saveFailed)
    }
  }

  function loadVoicePlayback(sessionId: string | null): void {
    const auth = $auth.get()

    const next =
      sessionId && auth.kind === 'authenticated' ? { sessionId, authSessionId: auth.snapshot.sessionId } : null

    if (scope?.sessionId === next?.sessionId && scope?.authSessionId === next?.authSessionId) {
      return
    }

    scope = next
    const owner = scope
    confirmed = { records: {}, revision: -1 }
    pending.clear()
    reportedFailure = false
    $voicePlaybackRecords.set({})
    loading = owner
      ? window.spiritagent.voicePlayback
          .get(owner)
          .then(snapshot => {
            if (owner === scope && snapshot) {
              applySnapshot(snapshot)
            }
          })
          .catch(error => {
            if (owner === scope) {
              reportFailure(error)
            }
          })
      : Promise.resolve()
  }

  function voicePlaybackReady(): Promise<void> {
    return loading
  }

  function captureVoiceProgress(messageId: number, bubbleIndex: number) {
    const owner = scope
    const key = voicePlaybackKey(messageId, bubbleIndex)
    let lastSaved = 0

    return (positionSeconds: number, completed: boolean, flush: boolean): void => {
      if (!owner || owner !== scope) {
        return
      }

      const record = {
        listened: completed || $voicePlaybackRecords.get()[key]?.listened === true,
        positionSeconds: completed ? 0 : Math.max(0, positionSeconds)
      }

      pending.set(key, record)
      $voicePlaybackRecords.setKey(key, record)

      if (!flush && Date.now() - lastSaved < 1000) {
        return
      }

      lastSaved = Date.now()
      void window.spiritagent.voicePlayback
        .update({ ...owner, messageId, bubbleIndex, ...record })
        .then(snapshot => {
          if (owner !== scope) {
            return
          }

          if (pending.get(key) === record) {
            pending.delete(key)
          }

          if (snapshot) {
            reportedFailure = false
            applySnapshot(snapshot.revision >= confirmed.revision ? snapshot : confirmed)
          }
        })
        .catch(error => {
          if (owner === scope) {
            reportFailure(error)
          }
        })
    }
  }

  function removeVoicePlayback(sessionId: string, messageIds?: number[]): void {
    const auth = $auth.get()

    if (auth.kind !== 'authenticated' || messageIds?.length === 0) {
      return
    }

    const owner = { sessionId, authSessionId: auth.snapshot.sessionId }
    const removed = messageIds && new Set(messageIds)

    if (scope?.sessionId === sessionId) {
      for (const key of pending.keys()) {
        if (!removed || removed.has(Number(key.split(':')[0]))) {
          pending.delete(key)
        }
      }
    }

    void window.spiritagent.voicePlayback
      .remove({ ...owner, messageIds })
      .then(snapshot => {
        if (snapshot && scope?.sessionId === sessionId && scope.authSessionId === owner.authSessionId) {
          applySnapshot(snapshot)
        }
      })
      .catch(error => {
        if (scope?.sessionId === sessionId && scope.authSessionId === owner.authSessionId) {
          reportFailure(error)
        }
      })
  }

  const receive = (event: { sessionId: string; authSessionId: string; snapshot: VoicePlaybackSnapshot }): void => {
    if (scope?.sessionId === event.sessionId && scope.authSessionId === event.authSessionId) {
      applySnapshot(event.snapshot)
    }
  }

  return {
    $voicePlaybackRecords,
    loadVoicePlayback,
    voicePlaybackReady,
    captureVoiceProgress,
    removeVoicePlayback,
    receive,
    dispose: () => {
      scope = null
      pending.clear()
    }
  }
}

export type ConversationPlaybackStore = ReturnType<typeof createPlaybackStore>
const playbackStores = new Map<string | null, ConversationPlaybackStore>()

export function getVoicePlaybackStore(sessionId: string | null): ConversationPlaybackStore {
  let store = playbackStores.get(sessionId)

  if (!store) {
    store = createPlaybackStore()
    playbackStores.set(sessionId, store)
  }

  store.loadVoicePlayback(sessionId)

  return store
}

export function releaseVoicePlaybackStore(sessionId: string | null): void {
  playbackStores.get(sessionId)?.dispose()
  playbackStores.delete(sessionId)
}

export function voicePlaybackReady(sessionId: string | null): Promise<void> {
  return getVoicePlaybackStore(sessionId).voicePlaybackReady()
}

export function captureVoiceProgress(messageId: number, bubbleIndex: number, sessionId: string | null) {
  return getVoicePlaybackStore(sessionId).captureVoiceProgress(messageId, bubbleIndex)
}

export function removeVoicePlayback(sessionId: string, messageIds?: number[]): void {
  getVoicePlaybackStore(sessionId).removeVoicePlayback(sessionId, messageIds)
}

export function bindVoicePlaybackUpdates(): () => void {
  return window.spiritagent.voicePlayback.onChanged(event => {
    playbackStores.get(event.sessionId)?.receive(event)
  })
}

registerStorageClearHandler(() => {
  for (const store of playbackStores.values()) {
    store.dispose()
  }

  playbackStores.clear()
})
