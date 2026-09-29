import {
  $actionCatalog,
  $actionCatalogStatus,
  $activeAvatarId,
  $companionMood,
  $videoPacks,
  acceptPlayCommand,
  actionCatalogChanged,
  type ActionPlayCommand,
  hydrateActionCatalog,
  hydrateCharacterCard,
  hydrateFullbodyReference,
  hydrateVideoPack,
  hydrateWardrobe,
  isActionStageVisible,
  observeActionStageVisibility,
  refreshAvatarSeeds,
  resolveAvatarRegeneration
} from '@/modules/character'
import {
  $videoGenError,
  $videoGenScope,
  $videoGenStage,
  $videoGenState,
  type VideoGenStage,
  videoPackEventReceived
} from '@/modules/character/rendering/video'
import { type GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { getStrings } from '@/shared/strings'

import { decodePayload } from '../gateway-event-util'

// 角色 / 形象事件处理器：心情、衣柜、头像重生与视频动作播放。
// 全部只更新 character 域的状态，不接触会话。

function authed(): boolean {
  return $auth.get().kind === 'authenticated'
}

async function acceptRequestedAction(command: ActionPlayCommand, accountId: string, sessionId: string): Promise<void> {
  let cancelled = false

  const identityMatches = (): boolean => {
    const identity = $auth.get()

    return (
      identity.kind === 'authenticated' &&
      identity.snapshot.accountId === accountId &&
      identity.snapshot.sessionId === sessionId
    )
  }

  const clipMatches = (): boolean => {
    const catalog = $actionCatalog.get()
    const clip = catalog?.clipsById.get(command.action_id)

    return (
      !!clip &&
      catalog?.packId === command.pack_id &&
      (command.asset_revision_id === null || clip.asset_revision === command.asset_revision_id)
    )
  }

  // 隐藏后再显示、换号后再切回、A→B→A 换装都不能复活等待中的旧请求。
  const stops = [
    observeActionStageVisibility(visible => {
      cancelled ||= !visible
    }),
    $auth.listen(() => {
      cancelled ||= !identityMatches()
    }),
    $actionCatalog.listen(catalog => {
      cancelled ||= catalog === null || catalog.packId !== command.pack_id
    }),
    $actionCatalogStatus.listen(status => {
      cancelled ||= status === 'unavailable'
    })
  ]

  try {
    if (!clipMatches() || $actionCatalogStatus.get() !== 'ready') {
      await hydrateActionCatalog(true)
    }

    const canAccept = (): boolean =>
      !cancelled &&
      identityMatches() &&
      clipMatches() &&
      $actionCatalogStatus.get() === 'ready' &&
      isActionStageVisible()

    if (!canAccept()) {
      return
    }

    if (!(await window.spiritagent.surface.claimPlay({ playId: command.play_id, expiresAt: command.expires_at }))) {
      return
    }

    const catalog = $actionCatalog.get()

    if (canAccept() && catalog) {
      acceptPlayCommand(command, catalog.clipsById.get(command.action_id) ?? null, catalog.packId)
    }
  } finally {
    stops.forEach(stop => stop())
  }
}

export function handleCharacterEvent(event: GatewayEvent): void {
  switch (event.type) {
    case 'companion.mood': {
      const mood = decodePayload<{ mood?: unknown }>(event.payload).mood

      if (typeof mood === 'string' && mood.trim()) {
        $companionMood.set(mood.trim())
      }

      break
    }

    case 'companion.character_card.updated': {
      if (authed()) {
        void hydrateCharacterCard().catch(error => log.warn('character-card', 'Refresh failed', error))
        void refreshAvatarSeeds()
          .then(() => {
            const avatarId = $activeAvatarId.get()

            return avatarId == null ? undefined : hydrateFullbodyReference(avatarId)
          })
          .catch(error => log.warn('avatar-seeds', 'Refresh failed', error))
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
      // 动作生成进度：驱动生成态文案；具体阶段文本由衣柜页消费包列表渲染。
      if (authed()) {
        videoPackEventReceived()
        void hydrateVideoPack(true)
      }

      break
    }

    case 'companion.action.play_requested': {
      // 播放指令：经统一调度器裁决（安全控制/拖拽优先，表达仅在基础状态为 idle 时生效——
      // 由 VideoStage 的 resolvePresentation 完成）；此处只校验目录与包归属后受理。
      // 可见舞台由主进程最终认领；代理窗的完整对话不遮挡侧边伙伴。
      const identity = $auth.get()

      if (identity.kind !== 'authenticated' || !isActionStageVisible()) {
        break
      }

      const p = decodePayload<ActionPlayCommand>(event.payload)

      if (!p?.play_id || !p.pack_id || !p.action_id) {
        break
      }

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

      void acceptRequestedAction(command, identity.snapshot.accountId, identity.snapshot.sessionId).catch(error =>
        log.warn('action-playback', 'Could not claim playback', error)
      )

      break
    }

    case 'companion.video.ready':
    case 'companion.video.activated': {
      // 视频包就绪 / 激活：生成态收敛并重新水合激活包（写持久化状态前先走 authedApi）。
      if (!authed()) {
        break
      }

      const p = decodePayload<{ packId?: number; outfitId?: number | null }>(event.payload)

      videoPackEventReceived()
      $videoGenState.set('idle')
      $videoGenStage.set(null)
      $videoGenError.set(null)
      $videoGenScope.set({ outfitId: p?.outfitId ?? null, packId: p?.packId ?? null })
      void hydrateVideoPack(true)

      break
    }

    case 'companion.video.progress': {
      // 按参考生成的阶段推进：只更新生成态文案，不触碰已激活包的显示。
      if (!authed()) {
        break
      }

      const p = decodePayload<{ stage?: string; packId?: number; outfitId?: number | null }>(event.payload)

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

      const progressPack = p?.packId == null ? null : ($videoPacks.get().find(pack => pack.id === p.packId) ?? null)

      const previousScope = $videoGenScope.get()

      const previousOutfitId =
        p?.packId != null && previousScope?.packId === p.packId ? (previousScope?.outfitId ?? null) : null

      const outfitId = p?.outfitId ?? progressPack?.outfit_id ?? previousOutfitId

      videoPackEventReceived()
      $videoGenState.set('generating')
      $videoGenStage.set(stage)
      $videoGenError.set(null)
      $videoGenScope.set({ outfitId, packId: p?.packId ?? null })

      break
    }

    case 'companion.video.failed': {
      const p = decodePayload<{ reason?: string; packId?: number; outfitId?: number | null }>(event.payload)
      log.warn('events', 'video pack failed:', p?.reason)

      if (authed()) {
        videoPackEventReceived()
        $videoGenState.set('failed')
        $videoGenStage.set(null)
        $videoGenScope.set({ outfitId: p?.outfitId ?? null, packId: p?.packId ?? null })
        $videoGenError.set({
          message: p?.reason || getStrings().living.appearance.videoGenRequestFailed,
          outfitId: p?.outfitId ?? null,
          packId: p?.packId ?? null
        })
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
