import {
  IPC,
  type VoicePlaybackRemoval,
  type VoicePlaybackScope,
  type VoicePlaybackSnapshot,
  type VoicePlaybackUpdate
} from '@ipc/contracts'
import type { IpcMain } from 'electron'

import type { BackendSessionPort } from '../shared/backend-port'
import { broadcastToAllWindows } from '../shared/utils'

import type { VoicePlaybackStore } from './voice-playback-store'

export function registerVoicePlaybackIpc({
  ipcMain,
  ensureBackendSession,
  store
}: {
  ipcMain: IpcMain
  ensureBackendSession: () => BackendSessionPort
  store: VoicePlaybackStore
}): void {
  function access(scope: VoicePlaybackScope) {
    if (!scope || typeof scope.sessionId !== 'string' || typeof scope.authSessionId !== 'string') {
      throw new Error('Invalid voice playback scope')
    }

    const current = ensureBackendSession().getSession()

    if (!current || current.sessionId !== scope.authSessionId) {
      return null
    }

    return {
      accountId: current.accountId,
      sessionId: scope.sessionId,
      isCurrent: () => ensureBackendSession().getSession()?.sessionId === scope.authSessionId
    }
  }

  function publish(scope: VoicePlaybackScope, snapshot: VoicePlaybackSnapshot | null): VoicePlaybackSnapshot | null {
    if (snapshot && access(scope)) {
      broadcastToAllWindows(IPC.event.voicePlaybackChanged, {
        authSessionId: scope.authSessionId,
        sessionId: scope.sessionId,
        snapshot
      })

      return snapshot
    }

    return null
  }

  ipcMain.handle(IPC.invoke.voicePlaybackGet, async (_event, scope: VoicePlaybackScope) => {
    const owner = access(scope)

    return owner ? store.get(owner) : null
  })
  ipcMain.handle(IPC.invoke.voicePlaybackUpdate, async (_event, update: VoicePlaybackUpdate) => {
    const owner = access(update)

    if (
      !Number.isSafeInteger(update.messageId) ||
      update.messageId <= 0 ||
      !Number.isSafeInteger(update.bubbleIndex) ||
      update.bubbleIndex < 0 ||
      typeof update.listened !== 'boolean' ||
      !Number.isFinite(update.positionSeconds) ||
      update.positionSeconds < 0
    ) {
      throw new Error('Invalid voice playback update')
    }

    return owner
      ? publish(
          update,
          await store.update(owner, update.messageId, update.bubbleIndex, {
            listened: update.listened,
            positionSeconds: update.positionSeconds
          })
        )
      : null
  })
  ipcMain.handle(IPC.invoke.voicePlaybackRemove, async (_event, removal: VoicePlaybackRemoval) => {
    const owner = access(removal)

    if (
      removal.messageIds !== undefined &&
      (!Array.isArray(removal.messageIds) || removal.messageIds.some(id => !Number.isSafeInteger(id) || id <= 0))
    ) {
      throw new Error('Invalid voice playback removal')
    }

    return owner ? publish(removal, await store.remove(owner, removal.messageIds)) : null
  })
}
