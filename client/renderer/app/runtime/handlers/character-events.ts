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
  type SpriteEmotion,
  switchRenderMode
} from '@/modules/character'
import {
  $clipMap,
  $modelGenError,
  $modelGenProgress,
  $modelGenState,
  clearModelRetry,
  setModelFailed,
  setModelInfo
} from '@/modules/character/rendering/model'
import { type GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $chatVisible } from '@/shared/store/chat-visibility'

import { decodePayload, type EventRouteContext } from '../gateway-event-util'

// 角色 / 形象事件处理器：心情、自主具身表达、模型与外观、衣柜与头像重生。
// 全部只更新 character 域（及其模型渲染域）的状态，不接触会话。

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

    case 'model.ready': {
      // 后端在 /api/companion/model 生成结束后推送此事件。
      // 只要 $modelInfo.asset_url 变化，模型引擎就会重新加载（见 ModelStage.tsx）。
      // error 字段用于展示生成失败；目前 UI 只是记录日志，恢复流程在后续切片。
      //
      // 二次 auth 防御：顶层 guard 只挡 'pending'，'unauthenticated' 的事件正常落地
      // 是为了 message.complete 不卡 thinking。但 model.ready 写持久化 atom（isPersistable
      // 通过 → localStorage），登出 race 里到达会污染下一位用户的冷启动读数。这里显式再挡一次。
      if (!authed()) {
        break
      }

      const p = decodePayload<{
        model_id?: number
        asset_url?: string
        species?: string
        rig_type?: string
        style?: string
        content_hash?: string
        error?: string
        clip_map?: Readonly<Record<string, string>>
      }>(event.payload)

      if (p?.error) {
        log.warn('events', 'model.ready error:', p.error)
        setModelFailed(p.error)

        break
      }

      $modelGenState.set('succeeded')
      $modelGenProgress.set(null)
      $modelGenError.set(null)
      clearModelRetry()
      setModelInfo({
        id: p?.model_id ?? null,
        asset_url: p?.asset_url ?? null,
        species: p?.species ?? null,
        rig_type: p?.rig_type ?? 'biped',
        style: p?.style ?? 'realistic',
        content_hash: p?.content_hash ?? null,
        status: 'succeeded',
        has_rig: true
      })
      // 运行时新生成的模型必须在此接住映射，否则角色会一直静止到下次水合。
      $clipMap.set(p?.clip_map ?? {})

      break
    }

    case 'model.gen.progress': {
      const p = decodePayload<{ stage?: string; progress?: number }>(event.payload)

      // uploading 是后端在提交新任务前串行发出的首事件，允许其他窗口发起的重建
      // 开启新一轮。终态之后的其余迟到进度仍丢弃，避免重新出现生成覆盖层。
      const genState = $modelGenState.get()

      if (genState === 'succeeded' || genState === 'failed') {
        if (p?.stage !== 'uploading') {
          break
        }

        $modelGenError.set(null)
        clearModelRetry()
      }

      $modelGenState.set(p?.stage === 'done' ? 'succeeded' : 'generating')
      $modelGenProgress.set({ stage: p?.stage ?? '', progress: p?.progress ?? 0 })

      break
    }

    case 'model.failed': {
      const p = decodePayload<{ reason?: string; retry_download?: boolean; model_id?: number }>(event.payload)
      setModelFailed(p?.reason ?? '模型生成失败', {
        retryDownload: p?.retry_download === true,
        modelId: p?.model_id ?? null
      })

      break
    }

    case 'companion.outfit.updated': {
      // 衣柜状态变化（重绘草稿/确认转正/穿着翻转/删除）——重拉列表；列表端点是真相源，事件只当刷新触发。
      void hydrateWardrobe()

      break
    }

    case 'companion.video.ready':
    case 'companion.video.activated': {
      // 视频包就绪 / 激活：重新水合激活包（写持久化状态前先走 authedApi）。
      if (!authed()) {
        break
      }

      void hydrateVideoPack()

      break
    }

    case 'companion.video.failed': {
      const p = decodePayload<{ reason?: string }>(event.payload)
      log.warn('events', 'video pack failed:', p?.reason)

      break
    }

    case 'companion.render_mode.changed': {
      const p = decodePayload<{ new_mode?: 'model' | 'video' }>(event.payload)

      if (p?.new_mode === 'model' || p?.new_mode === 'video') {
        void switchRenderMode(p.new_mode)
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
