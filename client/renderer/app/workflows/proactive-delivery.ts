import { $effectiveTier, $spriteState, setSpriteState } from '@/modules/character'
import { setProactiveBubble } from '@/modules/conversation'
import { speak } from '@/modules/speech'
import { $chatVisible } from '@/shared/store/chat-visibility'

export async function speakProactive(text: string): Promise<void> {
  if (!text.trim()) {
    return
  }

  // 静止档位不出气泡也不出声。
  const tier = $effectiveTier.get()

  if (tier === 'still') {
    return
  }

  const bubble = { text: text.trim() }
  const overlayVisible = !$chatVisible.get()

  if (overlayVisible) {
    setProactiveBubble(bubble)
  }

  if (tier === 'autonomous') {
    // 强制切到 speaking 状态——优先级 60 会被 'working'（pri 70）默默门控，
    // 不强制切的话主动语音就不会体现出来。
    setSpriteState('speaking', { force: true })
    const ok = await speak(text)

    // 朗读期间可能有更高优先级状态介入（工具 working、流式 thinking）；
    // 只在仍是 speaking 时才复位，否则会踩掉介入状态并让它失去收尾复位。
    if ($spriteState.get() === 'speaking') {
      setSpriteState('idle', { force: true })
    }

    // 让气泡在语音结束后再停留一会儿再消失。
    const linger = ok ? 4200 : 5000
    setProactiveBubble(overlayVisible ? bubble : null, linger)
  } else if (overlayVisible) {
    // 普通档位：停留更久，让用户在没有语音的情况下也能读完文字。
    setProactiveBubble(bubble, 8000)
  }
}
