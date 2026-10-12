import { $autoplayVoice, autoplayVoicePref } from '@/modules/character'
import {
  $conversationViews,
  activeVoiceMessageId,
  bindVoicePlaybackUpdates,
  captureVoiceProgress,
  getVoicePlaybackStore,
  isConversationActive,
  runtimeForMessage,
  setConversationVoiceSink,
  setVoiceBarControl,
  setVoiceBarFailed,
  setVoiceBarLoading,
  setVoiceBarPaused,
  setVoiceBarPlaying,
  voicePlaybackReady
} from '@/modules/conversation'
import {
  bindVoiceBarListeners,
  bindVoiceBarProjection,
  cancelVoiceBar,
  enqueueVoiceBars,
  refreshVoiceAutoplay,
  restartVoiceBar,
  setVoiceRecording,
  setVoiceSurfaceMounted,
  toggleVoiceBar
} from '@/modules/speech'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { voicePlaybackKey } from '@ipc/contracts'
import type { CompanionBubble } from '@protocol'

let dispose: (() => void) | undefined

function voiceReference(id: string) {
  const runtime = runtimeForMessage(id)
  const item = runtime?.$chatMessageList.get().find(message => message.id === id)
  const body = runtime?.$chatMessageBodies.get()[id]
  const sessionId = runtime?.$chatSessionId.get()

  return runtime && sessionId && item?.backendMessageId && body?.replyType === 'voice' && body.replyIndex !== undefined
    ? {
        runtime,
        sessionId,
        body,
        messageId: item.backendMessageId,
        bubbleIndex: body.replyIndex,
        key: voicePlaybackKey(item.backendMessageId, body.replyIndex)
      }
    : null
}

function isVoiceViewVisible(messageId?: string): boolean | undefined {
  const views = $conversationViews.get()

  if (views.length === 0) {
    return undefined
  }

  const sessionId = messageId ? runtimeForMessage(messageId)?.$chatSessionId.get() : null

  return views.some(view => view.visible && (!messageId || view.runtime.$chatSessionId.get() === sessionId))
}

export function bindConversationSpeech(): void {
  dispose?.()
  bindVoiceBarProjection({
    isActive: id => {
      if ($conversationViews.get().length === 0) {
        return undefined
      }

      const sessionId = id ? runtimeForMessage(id)?.$chatSessionId.get() : null

      return id ? isConversationActive(sessionId ?? null) : $conversationViews.get().some(view => view.eligible)
    },
    isVisible: isVoiceViewVisible,
    getAudio: id => runtimeForMessage(id)?.$chatMessageBodies.get()[id]?.replyAudio,
    getRecord: id => {
      const reference = voiceReference(id)

      return reference
        ? getVoicePlaybackStore(reference.sessionId).$voicePlaybackRecords.get()[reference.key]
        : undefined
    },
    getKey: id => {
      const reference = voiceReference(id)
      const auth = $auth.get()

      return reference && reference.runtime.isCurrent() && auth.kind === 'authenticated'
        ? [auth.snapshot.sessionId, reference.sessionId, reference.key].join(':')
        : null
    },
    getFollowing: id => {
      const runtime = runtimeForMessage(id)
      const list = runtime?.$chatMessageList.get() ?? []
      const index = list.findIndex(item => item.id === id)

      return index < 0
        ? []
        : list
            .slice(index + 1)
            .filter(item => runtime?.$chatMessageBodies.get()[item.id]?.replyType === 'voice')
            .map(item => item.id)
    },
    ready: id => voicePlaybackReady(voiceReference(id)?.sessionId ?? null),
    captureProgress: id => {
      const reference = voiceReference(id)

      if (!reference) {
        throw new Error('Voice message is not saved')
      }

      return captureVoiceProgress(reference.messageId, reference.bubbleIndex, reference.sessionId)
    },
    isAutoplay: () => $autoplayVoice.get(),
    retry: async id => {
      const reference = voiceReference(id)

      if (!reference) {
        throw new Error('Voice message is not saved')
      }

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
        reference.runtime.isCurrent() &&
        reference.runtime.$chatMessageBodies.get()[id] === reference.body
      ) {
        reference.runtime.updateVoiceBubble(reference.messageId, reference.bubbleIndex, result)
      }
    },
    setPlaying: setVoiceBarPlaying,
    setLoading: setVoiceBarLoading,
    setPaused: setVoiceBarPaused,
    setFailed: setVoiceBarFailed
  })
  setConversationVoiceSink({
    cancel: (sessionId, reason) => {
      if (reason === 'selection' && $conversationViews.get().length > 0) {
        return
      }

      const current = activeVoiceMessageId()

      if (!sessionId || !current || voiceReference(current)?.sessionId === sessionId) {
        cancelVoiceBar()
      }
    },
    enqueue: enqueueVoiceBars,
    setVisible: setVoiceSurfaceMounted,
    setRecording: setVoiceRecording
  })
  setVoiceBarControl({
    $autoplay: $autoplayVoice,
    restart: restartVoiceBar,
    setAutoplay: autoplayVoicePref.set,
    toggle: toggleVoiceBar
  })

  let disposed = false
  let viewUpdateQueued = false

  const listeners = [
    bindVoicePlaybackUpdates(),
    bindVoiceBarListeners(),
    $autoplayVoice.listen(refreshVoiceAutoplay),
    $conversationViews.listen(() => {
      if (viewUpdateQueued) {
        return
      }

      viewUpdateQueued = true
      // 视图更新先注销再登记；等同一轮登记完成后判断，也覆盖主对话与轻语之间的交接。
      queueMicrotask(() => {
        viewUpdateQueued = false

        if (disposed) {
          return
        }

        const current = activeVoiceMessageId()

        if (current && !isVoiceViewVisible(current)) {
          cancelVoiceBar()
        }
      })
    }),
    $gatewayState.listen(state => {
      if (state === 'closed' || state === 'error') {
        cancelVoiceBar()
      }
    })
  ]

  dispose = () => {
    disposed = true
    listeners.forEach(stop => stop())
  }
}
