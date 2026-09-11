import { bindChatVoiceBarListeners } from '@/chat'
import { bindPresentationPorts } from '@/shared/presentation-ports'

import { $screenLocked } from './activity'
import { playDataUrl, stopAudio } from './audio-track'
import { $spriteState, setSpriteState } from './companion-store'
import { openMediaViewer } from './media-viewer-overlay'
import { $activeAvatarId, $portraitUrl } from './portrait-store'
import { $companionVoiceId, $responseMode } from './prefs'
import { requestSynth, speakChatMessage, stopSpeaking } from './tts'
import { $voicePreparing } from './voice-state'

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
  stopAudio,
  stopSpeaking
})

bindChatVoiceBarListeners()
