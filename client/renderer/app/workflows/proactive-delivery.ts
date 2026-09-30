import {
  $effectiveTier,
  $spriteState,
  isActionStageVisible,
  observeActionStageVisibility,
  setSpriteState
} from '@/modules/character'
import { setProactiveBubble } from '@/modules/conversation'
import { speak, stopSpeaking } from '@/modules/speech'
import { $surfaceRole } from '@/shared/store/surfaces'

/** 精灵旁的主动气泡、媒体提示与主动台词只在桌面精灵舞台实际可见时呈现（与表达播放同一判断：未隐藏/最小化、未被完整入口收起、未开轻语、未锁屏）；不可见时不弹出也不出声，消息仍记入未读与历史，重新可见后由待读气泡承接。 */
export function isSpriteOverlayVisible(): boolean {
  return $surfaceRole.get() === 'sprite' && isActionStageVisible()
}

export async function speakProactive(text: string): Promise<void> {
  if (!text.trim()) {
    return
  }

  // 静止档位或精灵不可见时不出气泡也不出声。
  const tier = $effectiveTier.get()

  if (tier === 'still' || !isSpriteOverlayVisible()) {
    return
  }

  const bubble = { text: text.trim() }

  if (tier !== 'autonomous') {
    // 普通档位：停留更久，让用户在没有语音的情况下也能读完文字。
    setProactiveBubble(bubble, 8000)

    return
  }

  setProactiveBubble(bubble)
  // 强制切到 speaking ——优先级 60 会被 'working'（pri 70）默默门控，不强制切主动语音就体现不出来。
  setSpriteState('speaking', { force: true })

  // 合成或朗读期间精灵变为不可见时立即停声，尚未开始的播放也随之作废。
  const stopWatching = observeActionStageVisibility(visible => {
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

  // 朗读期间可能有更高优先级状态介入（工具 working、流式 thinking）；只在仍是 speaking 时才复位，否则会踩掉介入状态并让它失去收尾复位。
  if ($spriteState.get() === 'speaking') {
    setSpriteState('idle', { force: true })
  }

  // 让气泡在语音结束后再停留一会儿再消失；精灵已不可见时直接撤下。
  setProactiveBubble(isSpriteOverlayVisible() ? bubble : null, ok ? 4200 : 5000)
}
