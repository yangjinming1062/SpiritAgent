import { bindConversationActivity } from '@/app/workflows/conversation-activity'
import { bindConversationSpeech } from '@/app/workflows/conversation-speech'
import {
  $activeAvatarId,
  $companionVoiceId,
  $portraitUrl,
  $responsePreference,
  $screenLocked,
  setSpriteState
} from '@/modules/character'
import { setConversationVoiceSink } from '@/modules/conversation'
import { openMediaViewer } from '@/modules/media'
import { speakScripted } from '@/modules/speech'
import { bindPresentationPorts } from '@/shared/presentation-ports'
import { $surfaceRole } from '@/shared/store/surfaces'

// 端口与工作流装配：每个 renderer 入口在渲染前显式调用一次。角色与语音实现经呈现端口注入，会话语音投影由会话×语音工作流接手。
export function bindPresentation(): void {
  bindPresentationPorts({
    $activeAvatarId,
    $companionVoiceId,
    $portraitUrl,
    $screenLocked,
    getResponsePreference: () => $responsePreference.get(),
    openMediaViewer,
    setSpriteState,
    speakScripted
  })

  if ($surfaceRole.get() === 'desktop-companion') {
    // 共享模块的账户清理仍会回收默认 runtime；舞台不装配会话语音和活动投影。
    setConversationVoiceSink({
      cancel: () => undefined,
      enqueue: () => undefined,
      setVisible: () => undefined,
      setRecording: () => undefined
    })
  } else {
    bindConversationSpeech()
    bindConversationActivity()
  }
}
