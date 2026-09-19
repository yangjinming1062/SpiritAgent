import {
  $chatMessageBodies,
  $chatMessageList,
  $chatSessionId,
  $voiceBarLoadingId,
  $voiceBarPlayingId,
  setConversationVoiceSink,
  setVoiceBarControl,
  setVoiceBarFailed,
  setVoiceBarLoading,
  setVoiceBarPlaying,
  updateVoiceBubble
} from '@/modules/conversation'
import { bindVoiceBarListeners, bindVoiceBarProjection, cancelVoiceBar, toggleVoiceBar } from '@/modules/speech'
import type { CompanionBubble } from '@/shared/types/spiritagent'

export function bindConversationSpeech(): void {
  bindVoiceBarProjection({
    getAudio: id => $chatMessageBodies.get()[id]?.replyAudio,
    retry: async id => {
      const item = $chatMessageList.get().find(message => message.id === id)
      const body = $chatMessageBodies.get()[id]

      if (!item?.backendMessageId || body?.replyIndex === undefined || body.replyType !== 'voice') {
        throw new Error('Voice message is not saved')
      }

      const sessionId = $chatSessionId.get()

      const result = await window.spiritagent.api<CompanionBubble>({
        path: `/api/sessions/messages/${item.backendMessageId}/voice/${body.replyIndex}`,
        method: 'POST'
      })

      if ($chatSessionId.get() === sessionId && $chatMessageBodies.get()[id] === body) {
        updateVoiceBubble(item.backendMessageId, body.replyIndex, result)
      }
    },
    setPlaying: setVoiceBarPlaying,
    setLoading: setVoiceBarLoading,
    setFailed: setVoiceBarFailed,
    getPlaying: () => $voiceBarPlayingId.get(),
    getLoading: () => $voiceBarLoadingId.get()
  })
  setConversationVoiceSink({ cancel: cancelVoiceBar })
  setVoiceBarControl({
    toggle: id => {
      void toggleVoiceBar(id)
    }
  })
  bindVoiceBarListeners()
}
