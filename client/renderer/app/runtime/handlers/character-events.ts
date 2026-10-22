import {
  $activeAvatarId,
  $companionMood,
  applyServerCompanionVoiceId,
  hydrateCharacterCard,
  hydrateFullbodyReference,
  hydrateWardrobe,
  refreshAvatarSeeds
} from '@/modules/character'
import { invalidateVoiceCatalog } from '@/modules/speech'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'

function authed(): boolean {
  return $auth.get().kind === 'authenticated'
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
      void hydrateWardrobe({ force: true })

      break
    }

    default:
      break
  }
}
