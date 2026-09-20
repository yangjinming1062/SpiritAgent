import {
  $companionMood,
  $effectiveTier,
  $screenLocked,
  emitVfx,
  hydrateVideoPack,
  hydrateWardrobe,
  playSpriteActionSequence,
  resolveAvatarRegeneration,
  setSpriteState,
  type SpriteEmotion
} from '@/modules/character'
import {
  $videoGenError,
  $videoGenStage,
  $videoGenState,
  type VideoGenStage,
  videoPackEventReceived
} from '@/modules/character/rendering/video'
import { type GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $chatVisible } from '@/shared/store/chat-visibility'

import { decodePayload, type EventRouteContext } from '../gateway-event-util'

// 角色 / 形象事件处理器：心情、自主具身表达、衣柜、头像重生与视频包生成。
// 全部只更新 character 域的状态，不接触会话。

const SWEAT_EMOTIONS: ReadonlySet<string> = new Set(['scared', 'embarrassed', 'concerned', 'apologetic'])

// 高唤醒负面情绪冒冷汗（DESIGN §6.3 粒子清单 💦 的情绪侧触发点）
function maybeEmotionVfx(emotion?: string): void {
  if (emotion && SWEAT_EMOTIONS.has(emotion)) {
    emitVfx('sweat', { nx: 0.5, ny: 0.2, count: 2 })
  }
}

function authed(): boolean {
  return $auth.get().kind === 'authenticated'
}

export function handleCharacterEvent(event: GatewayEvent, ctx: EventRouteContext): void {
  switch (event.type) {
    case 'companion.affect': {
      // 云端独立具身表达只服务自主档下当前可见的桌面精灵，不进入聊天回合。
      const payload = decodePayload<{ actions?: string[]; emotion?: string }>(event.payload)

      const emotion = payload?.emotion
      const actions = Array.isArray(payload?.actions) ? payload.actions.filter(a => typeof a === 'string') : []
      const autonomous = $effectiveTier.get() === 'autonomous'

      const canAnimate = autonomous && !$chatVisible.get() && !$screenLocked.get() && !ctx.isProxy

      if (canAnimate && ((emotion && emotion !== 'neutral') || actions.length > 0)) {
        maybeEmotionVfx(emotion)
        setSpriteState('emotional', {
          action: actions[0],
          emotion: (emotion && emotion !== 'neutral' ? emotion : 'neutral') as SpriteEmotion
        })
        playSpriteActionSequence(actions)
      }

      break
    }

    case 'companion.mood': {
      const mood = decodePayload<{ mood?: string }>(event.payload)?.mood?.trim()

      if (mood) {
        $companionMood.set(mood)
      }

      break
    }

    case 'companion.outfit.updated': {
      // 衣柜状态变化（重绘草稿/确认转正/穿着翻转/删除）——重拉列表；列表端点是真相源，事件只当刷新触发。
      void hydrateWardrobe()

      break
    }

    case 'companion.video.ready':
    case 'companion.video.activated': {
      // 视频包就绪 / 激活：生成态收敛并重新水合激活包（写持久化状态前先走 authedApi）。
      if (!authed()) {
        break
      }

      videoPackEventReceived()
      $videoGenState.set('idle')
      $videoGenStage.set(null)
      $videoGenError.set(null)
      void hydrateVideoPack(true)

      break
    }

    case 'companion.video.progress': {
      // 按参考生成的阶段推进：只更新生成态文案，不触碰已激活包的显示。
      if (!authed()) {
        break
      }

      const p = decodePayload<{ stage?: string }>(event.payload)

      const stages: readonly VideoGenStage[] = [
        'script',
        'pose',
        'submit',
        'generate',
        'download',
        'process',
        'publish'
      ]

      const stage = stages.find(s => s === p?.stage) ?? null

      videoPackEventReceived()
      $videoGenState.set('generating')
      $videoGenStage.set(stage)
      $videoGenError.set(null)

      break
    }

    case 'companion.video.failed': {
      const p = decodePayload<{ reason?: string }>(event.payload)
      log.warn('events', 'video pack failed:', p?.reason)

      if (authed()) {
        videoPackEventReceived()
        $videoGenState.set('failed')
        $videoGenStage.set(null)
        $videoGenError.set(p?.reason || '视频形象生成失败，请稍后重试')
        void hydrateVideoPack(true)
      }

      break
    }

    case 'avatar.regenerated': {
      // 后台重新生成的结果——通过 job_id 解析等待者，
      // 让肖像能直接替换而不阻塞处理器。
      const p = decodePayload<{
        job_id?: string
        asset_url?: string | null
        id?: number
        error?: string
      }>(event.payload)

      if (p?.job_id) {
        resolveAvatarRegeneration(p)
      }

      break
    }

    default:
      break
  }
}
