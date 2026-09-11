import { openWhisper } from '@/app/windows/sprite/whisper/whisper-store'
import { bindConversationSpeech } from '@/app/workflows/conversation-speech'
import { speakProactive } from '@/app/workflows/proactive-delivery'
import { bindWhisperOpener } from '@/app/workflows/session-delivery'
import {
  $activeAvatarId,
  $companionVoiceId,
  $portraitUrl,
  $responseMode,
  $screenLocked,
  $spriteState,
  bindProactiveLineSpeaker,
  setSpriteState
} from '@/modules/character'
import { openMediaViewer } from '@/modules/media'
import {
  $voicePreparing,
  playDataUrl,
  requestSynth,
  speakChatMessage,
  speakScripted,
  stopAudio,
  stopSpeaking
} from '@/modules/speech'
import { bindPresentationPorts } from '@/shared/presentation-ports'

// 端口与工作流装配：每个 renderer 入口在渲染前显式调用一次。
// 角色与语音实现经呈现端口注入，会话语音投影由会话×语音工作流接手。
export function bindPresentation(): void {
  bindPresentationPorts({
    $activeAvatarId,
    $companionVoiceId,
    $portraitUrl,
    $responseMode,
    $screenLocked,
    $spriteState,
    $voicePreparing,
    openMediaViewer,
    playDataUrl,
    requestSynth,
    setSpriteState,
    speakChatMessage,
    speakScripted,
    stopAudio,
    stopSpeaking
  })

  bindConversationSpeech()
  bindWhisperOpener(openWhisper)

  // 仪式行走等角色行为的主动性台词经工作流送达（角色模块不依赖应用层）。
  bindProactiveLineSpeaker(speakProactive)
}
