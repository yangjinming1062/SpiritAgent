import {
  $companionMood,
  $effectiveTier,
  $screenLocked,
  emitVfx,
  hydrateWardrobe,
  playSpriteActionSequence,
  resolveAvatarRegeneration,
  setSpriteState,
  type SpriteEmotion
} from '@/modules/character'
import {
  hydrateMesh2D,
  hydratePuppet,
  resetMesh2D,
  resetPuppet,
  setMesh2DStatus,
  switchRenderMode
} from '@/modules/character/rendering/2d'
import {
  $clipMap,
  $modelGenError,
  $modelGenProgress,
  $modelGenState,
  clearModelRetry,
  setModelFailed,
  setModelInfo
} from '@/modules/character/rendering/3d'
import { type GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $chatVisible } from '@/shared/store/chat-visibility'

import { decodePayload, type EventRouteContext } from '../gateway-event-util'

// 角色 / 形象事件处理器：心情、自主具身表达、模型与 2D 拆分、衣柜与头像重生。
// 全部只更新 character 域（及其渲染域 2d/3d）的状态，不接触会话。

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
      // 只要 $modelInfo.asset_url 变化，3D 引擎就会重新加载（见 companion-3d.tsx）。
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

      // 终态已定后,迟到的 progress 不能再把它打回 'generating' —— 否则覆盖层会重现。
      if ($modelGenState.get() === 'succeeded') {
        break
      }

      $modelGenState.set(p?.stage === 'done' ? 'succeeded' : 'generating')
      $modelGenProgress.set({ stage: p?.stage ?? '', progress: p?.progress ?? 0 })

      break
    }

    case 'model.failed': {
      const p = decodePayload<{ reason?: string; retry_download?: boolean; model_id?: number }>(event.payload)
      setModelFailed(p?.reason ?? '3D 模型生成失败', {
        retryDownload: p?.retry_download === true,
        modelId: p?.model_id ?? null
      })

      break
    }

    case 'companion.2d.ready': {
      // 2d 拆分完成——重新水合 2d 行并串一次 puppet 分流判定（manifest 恒为 kind=psd 描述符）。
      //
      // 二次 auth 防御：与 model.ready 同理，hydrateMesh2D 走 authedApi + 写持久化 atom，
      // 登出 race 里到达会把旧 session 的 manifest 写进 localStorage。这里早返回避免污染。
      if (!authed()) {
        break
      }

      const p = decodePayload<{
        model_id?: number
        manifest_url?: string | null
        layers?: { name: string; url: string }[]
      }>(event.payload)

      if (p?.manifest_url) {
        log.info('events', '2d ready:', p.model_id)
      }

      void hydrateMesh2D().then(() => hydratePuppet())

      break
    }

    case 'companion.2d.failed': {
      // 切分失败：渲染层由 SpriteStage 兜底（程序化蛋 / 已就绪的 3D 模型）。
      const p = decodePayload<{ reason?: string }>(event.payload)
      setMesh2DStatus('failed', p?.reason ?? '2D 切分失败')
      log.warn('events', '2d failed:', p?.reason)

      break
    }

    case 'companion.outfit.updated': {
      // 衣柜状态变化（切分就绪/穿着翻转/删除）——重拉列表；列表端点是真相源，事件只当刷新触发。
      // 仅穿着翻转时重水合 2d（幂等，与 2d.ready 双触发无妨）；入柜不换装与删除不动当前穿着。
      const p = decodePayload<{ worn?: boolean }>(event.payload)

      void hydrateWardrobe()

      if (p?.worn) {
        void hydrateMesh2D().then(() => hydratePuppet())
      }

      break
    }

    case 'companion.outfit.failed': {
      const p = decodePayload<{ reason?: string }>(event.payload)
      void hydrateWardrobe()
      log.warn('events', 'outfit failed:', p?.reason)

      break
    }

    case 'companion.render_mode.changed': {
      const p = decodePayload<{ new_mode?: '2d' | '3d' }>(event.payload)

      if (p?.new_mode === '2d' || p?.new_mode === '3d') {
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

      // 与 model.ready / companion.2d.ready 同理：下方 resetMesh2D/resetPuppet 走
      // 定义了 Persisted atom 的 clear handler，登出 race 里触发会把刚清空的 localStorage
      // 又把 in-memory atom 写回 fallback（语义无害但与 clearCompanionStorage 重叠），
      // 之后的 hydrateMesh2D 在已登出窗口写持久化。这里同样加显式 auth 二次防御。
      if (!authed()) {
        break
      }

      // DESIGN §1.2 不变量：头像重生不使 2D/3D 模型失效——模型只随物种变更或用户
      // 显式请求重生。这里只做幂等的本地状态刷新（hydrate 重新拉取既有资产行）。
      resetMesh2D()
      resetPuppet()
      void hydrateMesh2D().then(() => hydratePuppet())

      break
    }

    default:
      break
  }
}
