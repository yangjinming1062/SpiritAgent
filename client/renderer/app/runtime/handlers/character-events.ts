import {
  $actionCatalog,
  $companionMood,
  $screenLocked,
  acceptPlayCommand,
  actionCatalogChanged,
  type ActionPlayCommand,
  hydrateCharacterCard,
  hydrateVideoPack,
  hydrateWardrobe,
  resolveAvatarRegeneration
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

// 角色 / 形象事件处理器：心情、衣柜、头像重生与视频动作播放。
// 全部只更新 character 域的状态，不接触会话。

function authed(): boolean {
  return $auth.get().kind === 'authenticated'
}

export function handleCharacterEvent(event: GatewayEvent, ctx: EventRouteContext): void {
  switch (event.type) {
    case 'companion.mood': {
      const mood = decodePayload<{ mood?: string }>(event.payload)?.mood?.trim()

      if (mood) {
        $companionMood.set(mood)
      }

      break
    }

    case 'companion.character_card.updated': {
      if (authed()) {
        void hydrateCharacterCard().catch(error => log.warn('character-card', 'Refresh failed', error))
      }

      break
    }

    case 'companion.outfit.updated': {
      // 衣柜状态变化（重绘草稿/确认转正/穿着翻转/删除）——重拉列表；列表端点是真相源，事件只当刷新触发。
      void hydrateWardrobe()

      break
    }

    case 'companion.action.catalog_changed': {
      // 目录变更（新动作入库/素材版本推进）：重新水合当前包目录；同包刷新不打断在播实例。
      if (authed()) {
        actionCatalogChanged()
        void hydrateVideoPack(true)
      }

      break
    }

    case 'companion.action.job_updated': {
      // 动作生成进度：驱动生成态文案；具体阶段文本由外观页消费包列表渲染。
      if (authed()) {
        videoPackEventReceived()
        void hydrateVideoPack(true)
      }

      break
    }

    case 'companion.action.play_requested': {
      // 播放指令：经统一调度器裁决（安全控制/拖拽优先，表达仅在基础状态为 idle 时生效——
      // 由 VideoStage 的 resolvePresentation 完成）；此处只校验目录与包归属后受理。
      // 仅当前可见的精灵舞台执行：隐藏工作台代理窗口、锁屏或聊天覆盖时不播放、不回执“已展示”。
      if (!authed() || ctx.isProxy || $screenLocked.get() || $chatVisible.get()) {
        break
      }

      const p = decodePayload<ActionPlayCommand>(event.payload)

      if (!p?.play_id || !p.pack_id || !p.action_id) {
        break
      }

      const catalog = $actionCatalog.get()

      if (!catalog) {
        break
      }

      const clip = catalog.clipsById.get(p.action_id)

      if (clip) {
        const command: ActionPlayCommand = {
          play_id: p.play_id,
          target_device: p.target_device ?? '',
          target_surface: p.target_surface ?? '',
          pack_id: p.pack_id,
          appearance_epoch: p.appearance_epoch ?? 0,
          action_id: p.action_id,
          asset_revision_id: p.asset_revision_id ?? null,
          repeat_count: p.repeat_count ?? 1,
          expires_at: p.expires_at ?? null,
          source: p.source ?? 'chat_expression'
        }

        acceptPlayCommand(command, clip, catalog.packId)
      }

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
