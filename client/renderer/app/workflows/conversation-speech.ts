import {
  $chatMessageBodies,
  $voiceBarPlayingId,
  setConversationVoiceSink,
  setVoiceBarControl,
  setVoiceBarLoading,
  setVoiceBarPlaying
} from '@/modules/conversation'
import {
  bindVoiceBarListeners,
  bindVoiceBarProjection,
  cancelVoiceBar,
  estimateVoiceDuration,
  getCachedVoiceDuration,
  isLivingVoiceBarActive,
  resolveVoiceBarDuration,
  synthesizeVoiceBar,
  toggleVoiceBar
} from '@/modules/speech'

// 会话 × 语音工作流：把语音条引擎投影到当前窗口的消息体上，
// 并向会话模块提供收音/合成/中止的窄接缝。两个模块互相不知道对方。
// 需要在端口绑定完成后由 app/bootstrap 调用一次（每个 renderer 各自装配）。

export function bindConversationSpeech(): void {
  bindVoiceBarProjection({
    getVoiceItem: messageId => {
      const body = $chatMessageBodies.get()[messageId]

      return body ? { speechStyle: body.speechStyle, text: body.text } : undefined
    },
    patchVoice: (messageId, patch) => {
      const body = $chatMessageBodies.get()[messageId]

      if (body) {
        $chatMessageBodies.setKey(messageId, { ...body, ...patch })
      }
    },
    setPlaying: setVoiceBarPlaying,
    setLoading: setVoiceBarLoading,
    getPlaying: () => $voiceBarPlayingId.get(),
    failPendingWithoutDuration: () => {
      for (const [messageId, body] of Object.entries($chatMessageBodies.get())) {
        if (body?.voiceStatus === 'pending' && body.voiceDuration == null) {
          $chatMessageBodies.setKey(messageId, { ...body, voiceStatus: undefined })
        }
      }
    },
    hasPendingVoice: exceptId =>
      Object.entries($chatMessageBodies.get()).some(
        ([messageId, body]) => messageId !== exceptId && body?.voiceStatus === 'pending'
      ),
    clearProjectedDurations: () => {
      for (const [messageId, body] of Object.entries($chatMessageBodies.get())) {
        if (body?.voiceDuration != null) {
          $chatMessageBodies.setKey(messageId, { ...body, voiceDuration: undefined, voiceStatus: undefined })
        }
      }
    }
  })

  setConversationVoiceSink({
    isActive: isLivingVoiceBarActive,
    cachedDuration: getCachedVoiceDuration,
    synthesize: (messageId, text) => {
      void synthesizeVoiceBar(messageId, text, { autoPlay: true })
    },
    cancel: cancelVoiceBar,
    estimateDuration: estimateVoiceDuration
  })

  setVoiceBarControl({
    toggle: messageId => {
      void toggleVoiceBar(messageId)
    },
    ensureDuration: (messageId, text) => {
      void resolveVoiceBarDuration(messageId, text)
    }
  })

  bindVoiceBarListeners()
}
