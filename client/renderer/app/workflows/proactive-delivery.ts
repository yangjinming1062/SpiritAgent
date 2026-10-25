import {
  $effectiveTier,
  $spriteState,
  isCharacterStageVisible,
  observeCharacterStageVisibility,
  setSpriteState
} from '@/modules/character'
import { setProactiveBubble } from '@/modules/conversation'
import { speak, stopSpeaking } from '@/modules/speech'
import { $surfaceRole } from '@/shared/store/surfaces'

export function isSpriteOverlayVisible(): boolean {
  return $surfaceRole.get() === 'sprite' && isCharacterStageVisible()
}

export async function speakProactive(text: string): Promise<void> {
  const content = text.trim()

  if (!content) {
    return
  }

  const tier = $effectiveTier.get()

  if (tier === 'still' || !isSpriteOverlayVisible()) {
    return
  }

  const bubble = { text: content }

  if (tier !== 'autonomous') {
    // 普通档位：停留更久，让用户在没有语音的情况下也能读完文字。
    setProactiveBubble(bubble, 8000)

    return
  }

  setProactiveBubble(bubble)
  // 主动语音须覆盖 working 的状态优先级。
  setSpriteState('speaking', { force: true })

  // 合成或朗读期间精灵变为不可见时立即停声，尚未开始的播放也随之作废。
  const stopWatching = observeCharacterStageVisibility(visible => {
    if (!visible) {
      stopSpeaking()
    }
  })

  let ok = false

  try {
    ok = await speak(text)
  } finally {
    stopWatching()
  }

  // 不覆盖朗读期间介入的状态。
  if ($spriteState.get() === 'speaking') {
    setSpriteState('idle', { force: true })
  }

  // 让气泡在语音结束后再停留一会儿再消失；精灵已不可见时直接撤下。
  setProactiveBubble(isSpriteOverlayVisible() ? bubble : null, ok ? 4200 : 5000)
}
