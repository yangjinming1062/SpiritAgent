import { $effectiveTier, setSpriteState } from '@/modules/character'
import { setProactiveBubble } from '@/modules/conversation'
import { speak } from '@/modules/speech'
import { $chatVisible } from '@/shared/store/chat-visibility'

export async function speakProactive(
  text: string,
  opts?: { userInitiated?: boolean; affect?: string; sessionId?: string }
): Promise<void> {
  if (!text.trim()) {
    return
  }

  // 静止档位会压掉主动外联，但用户主动触发的反应（戳 / 拖拽）始终出声。
  // Affect 永远不受门控——调用方自己设情绪状态。
  const tier = $effectiveTier.get()

  if (!opts?.userInitiated && tier === 'still') {
    return
  }

  const bubble = { text: text.trim(), sessionId: opts?.sessionId }
  const overlayVisible = !$chatVisible.get()

  if (overlayVisible) {
    setProactiveBubble(bubble)
  }

  if (tier === 'autonomous' || opts?.userInitiated) {
    // 强制切到 speaking 状态——优先级 60 会被 'working'（pri 70）默默门控，
    // 不强制切的话主动/触发的语音就不会体现出来。
    setSpriteState('speaking', { force: true })
    const ok = await speak(text)
    setSpriteState('idle', { force: true })
    // 让气泡在语音结束后再停留一会儿再消失。
    const linger = ok ? 4200 : 5000
    setProactiveBubble(overlayVisible ? bubble : null, linger)
  } else if (overlayVisible) {
    // 普通档位：停留更久，让用户在没有语音的情况下也能读完文字。
    setProactiveBubble(bubble, 8000)
  }
}
