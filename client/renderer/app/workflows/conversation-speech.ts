import { voicePlaybackKey } from '@ipc/contracts'

import { $autoplayVoice } from '@/modules/character'
import {
  $chatMessageBodies,
  $chatMessageList,
  $chatSessionId,
  $voicePlaybackRecords,
  bindVoicePlaybackUpdates,
  captureVoiceProgress,
  loadVoicePlayback,
  setConversationVoiceSink,
  setVoiceBarControl,
  setVoiceBarFailed,
  setVoiceBarLoading,
  setVoiceBarPaused,
  setVoiceBarPlaying,
  updateVoiceBubble,
  voicePlaybackReady
} from '@/modules/conversation'
import {
  bindVoiceBarListeners,
  bindVoiceBarProjection,
  cancelVoiceBar,
  enqueueVoiceBars,
  refreshVoiceAutoplay,
  setVoiceRecording,
  setVoiceSurfaceMounted,
  toggleVoiceBar
} from '@/modules/speech'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import type { CompanionBubble } from '@/shared/types/spiritagent'

let dispose: (() => void) | undefined

function voiceReference(id: string) {
  const item = $chatMessageList.get().find(message => message.id === id)
  const body = $chatMessageBodies.get()[id]

  return item?.backendMessageId && body?.replyType === 'voice' && body.replyIndex !== undefined
    ? {
        messageId: item.backendMessageId,
        bubbleIndex: body.replyIndex,
        key: voicePlaybackKey(item.backendMessageId, body.replyIndex)
      }
    : null
}

export function bindConversationSpeech(): void {
  dispose?.()
  bindVoiceBarProjection({
    getAudio: id => $chatMessageBodies.get()[id]?.replyAudio,
    getRecord: id => {
      const reference = voiceReference(id)

      return reference ? $voicePlaybackRecords.get()[reference.key] : undefined
    },
    getKey: id => {
      const reference = voiceReference(id)
      const auth = $auth.get()
      const sessionId = $chatSessionId.get()

      return reference && sessionId && auth.kind === 'authenticated'
        ? [auth.snapshot.sessionId, sessionId, reference.key].join(':')
        : null
    },
    getFollowing: id => {
      const list = $chatMessageList.get()
      const index = list.findIndex(item => item.id === id)

      return index < 0
        ? []
        : list
            .slice(index + 1)
            .filter(item => $chatMessageBodies.get()[item.id]?.replyType === 'voice')
            .map(item => item.id)
    },
    ready: voicePlaybackReady,
    captureProgress: id => {
      const reference = voiceReference(id)

      if (!reference) {
        throw new Error('Voice message is not saved')
      }

      return captureVoiceProgress(reference.messageId, reference.bubbleIndex)
    },
    isAutoplay: () => $autoplayVoice.get(),
    retry: async id => {
      const reference = voiceReference(id)
      const body = $chatMessageBodies.get()[id]

      if (!reference) {
        throw new Error('Voice message is not saved')
      }

      const sessionId = $chatSessionId.get()
      const auth = $auth.get()

      const result = await window.spiritagent.api<CompanionBubble>({
        path: '/api/sessions/messages/' + reference.messageId + '/voice/' + reference.bubbleIndex,
        method: 'POST'
      })

      const currentAuth = $auth.get()

      if (
        auth.kind === 'authenticated' &&
        currentAuth.kind === 'authenticated' &&
        currentAuth.snapshot.sessionId === auth.snapshot.sessionId &&
        $chatSessionId.get() === sessionId &&
        $chatMessageBodies.get()[id] === body
      ) {
        updateVoiceBubble(reference.messageId, reference.bubbleIndex, result)
      }
    },
    setPlaying: setVoiceBarPlaying,
    setLoading: setVoiceBarLoading,
    setPaused: setVoiceBarPaused,
    setFailed: setVoiceBarFailed
  })
  setConversationVoiceSink({
    cancel: cancelVoiceBar,
    enqueue: enqueueVoiceBars,
    setVisible: setVoiceSurfaceMounted,
    setRecording: setVoiceRecording
  })
  setVoiceBarControl({ toggle: toggleVoiceBar })

  const syncScope = (): void => {
    loadVoicePlayback($chatSessionId.get())
  }

  const listeners = [
    bindVoicePlaybackUpdates(),
    bindVoiceBarListeners(),
    $chatSessionId.listen(syncScope),
    $auth.listen(syncScope),
    $autoplayVoice.listen(refreshVoiceAutoplay),
    $gatewayState.listen(state => {
      if (state === 'closed' || state === 'error') {
        cancelVoiceBar()
      }
    })
  ]

  syncScope()
  dispose = () => listeners.forEach(stop => stop())
}
