import {
  $actionCatalog,
  $actionCatalogStatus,
  $activeAvatarId,
  $companionMood,
  acceptPlayCommand,
  actionCatalogChanged,
  type ActionClipEntry,
  applyServerCompanionVoiceId,
  hydrateActionCatalog,
  hydrateCharacterCard,
  hydrateFullbodyReference,
  hydrateVideoPack,
  hydrateWardrobe,
  isActionStageVisible,
  observeActionStageVisibility,
  refreshAvatarSeeds,
  reportReceipt,
  videoGenFailed,
  videoGenProgress,
  videoGenReady,
  videoPackEventReceived
} from '@/modules/character'
import { invalidateVoiceCatalog } from '@/modules/speech'
import { type GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'

// 角色/形象事件处理：心情、衣柜、头像重生与视频动作播放；只更新 character 域状态，不接触会话。

function authed(): boolean {
  return $auth.get().kind === 'authenticated'
}

async function acceptRequestedAction(
  command: Extract<GatewayEvent, { type: 'companion.action.play_requested' }>['payload'],
  accountId: string,
  sessionId: string
): Promise<void> {
  let cancelled = false

  const identityMatches = (): boolean => {
    const identity = $auth.get()

    return (
      identity.kind === 'authenticated' &&
      identity.snapshot.accountId === accountId &&
      identity.snapshot.sessionId === sessionId
    )
  }

  // 外观代次随每次激活递增：指令较新说明本舞台目录落后；较旧或同代次包不同说明外观已被替换。
  const appearance = (): 'behind' | 'current' | 'replaced' => {
    const catalog = $actionCatalog.get()

    if (!catalog || command.appearance_epoch > catalog.appearanceEpoch) {
      return 'behind'
    }

    return command.appearance_epoch === catalog.appearanceEpoch && command.pack_id === catalog.packId
      ? 'current'
      : 'replaced'
  }

  const matchedClip = (): ActionClipEntry | null => {
    const clip = $actionCatalog.get()?.clipsById.get(command.action_id)

    return clip && (command.asset_revision_id === null || clip.asset_revision === command.asset_revision_id)
      ? clip
      : null
  }

  // 隐藏后再显示、换号后再切回都不能复活等待中的旧请求；A→B→A 换装由外观代次拦截。
  const stops = [
    observeActionStageVisibility(visible => {
      cancelled ||= !visible
    }),
    $auth.listen(() => {
      cancelled ||= !identityMatches()
    }),
    $actionCatalogStatus.listen(status => {
      cancelled ||= status === 'unavailable'
    })
  ]

  const stageReady = (): boolean =>
    !cancelled && identityMatches() && $actionCatalogStatus.get() === 'ready' && isActionStageVisible()

  try {
    const local = $actionCatalog.get()

    // 同包旧代次说明该包已重新激活且在所有舞台失效，直接认领并回执；其他不能直接播放的情况先强制刷新（覆盖恢复保留备份中的代次，新包代次可能低于本地旧包）。
    const reactivated = local?.packId === command.pack_id && command.appearance_epoch < local.appearanceEpoch

    if (!reactivated && (appearance() !== 'current' || !matchedClip() || $actionCatalogStatus.get() !== 'ready')) {
      await hydrateActionCatalog(true)
    }

    const state = appearance()

    // 刷新后仍落后时不认领：其他舞台可能已加载新代次，请求由有效期收尾；舞台不可用或当前外观缺少对应素材时同样不认领。
    if (state === 'behind' || !stageReady() || (state === 'current' && !matchedClip())) {
      return
    }

    if (!(await shellApi().surface.claimPlay({ playId: command.play_id, expiresAt: command.expires_at }))) {
      return
    }

    // 换号后无法以原账号回执，已认领的请求随有效期收尾。
    if (!identityMatches()) {
      return
    }

    // 已认领的请求不再交给其他舞台：不能播放时回执 rejected，避免账本停留在 queued。
    const catalog = $actionCatalog.get()

    if (catalog === null) {
      void reportReceipt(command, 'rejected', 'appearance changed')
    } else if (!stageReady()) {
      void reportReceipt(command, 'rejected', 'stage unavailable')
    } else {
      acceptPlayCommand(command, matchedClip(), catalog)
    }
  } finally {
    stops.forEach(stop => stop())
  }
}

export function handleCharacterEvent(event: GatewayEvent): void {
  switch (event.type) {
    case 'companion.voice.updated': {
      void applyServerCompanionVoiceId(event.payload.voice_id).catch(error =>
        log.warn('voice-selection', 'Could not apply server voice:', error)
      )
      invalidateVoiceCatalog()

      break
    }

    case 'companion.mood': {
      const mood = event.payload.mood.trim()

      if (mood) {
        $companionMood.set(mood)
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
        videoPackEventReceived(event.payload)
        void hydrateVideoPack(true)
      }

      break
    }

    case 'companion.action.play_requested': {
      // 播放指令：经统一调度器裁决（安全控制/拖拽优先，表达仅在 idle 时生效——由 MediaStage 的 resolvePresentation 完成）；此处只校验目录、包与外观代次后受理。可见舞台由主进程最终认领。
      const identity = $auth.get()

      if (identity.kind !== 'authenticated' || !isActionStageVisible()) {
        break
      }

      const command = event.payload

      if (!command.play_id || !command.pack_id || !command.action_id) {
        break
      }

      void acceptRequestedAction(command, identity.snapshot.accountId, identity.snapshot.sessionId).catch(error =>
        log.warn('action-playback', 'Could not claim playback', error)
      )

      break
    }

    case 'companion.video.ready':
    case 'companion.video.activated': {
      // 视频包就绪 / 激活后重新读取当前账户的激活包。
      if (authed()) {
        videoGenReady(event.payload, event.type === 'companion.video.ready')
        void hydrateVideoPack(true)
      }

      break
    }

    case 'companion.video.progress': {
      // 按参考生成的阶段推进：只更新生成态文案，不触碰已激活包的显示。
      if (authed()) {
        videoGenProgress(event.payload.stage, event.payload, event.payload.video_queue)
      }

      break
    }

    case 'companion.video.failed': {
      const payload = event.payload

      log.warn('events', 'video pack failed:', payload.reason)

      if (authed()) {
        videoGenFailed(payload.reason, payload)
        void hydrateVideoPack(true)
      }

      break
    }

    default:
      break
  }
}
