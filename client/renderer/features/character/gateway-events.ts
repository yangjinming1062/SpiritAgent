import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'

import { refreshAvatarSeeds } from './avatar-seeds-store'
import { hydrateFullbodyReference } from './fullbody-reference-store'
import { $companionMood } from './persona-store'
import { $activeAvatarId } from './portrait-store'
import { applyServerCompanionVoiceId } from './prefs'

function authed(): boolean {
  return $auth.get().kind === 'authenticated'
}

/** 角色表现事件：心情、身份版本与首次准备状态；声音目录由应用层另行协调。 */
export function onCharacterEvent(event: GatewayEvent): void {
  switch (event.type) {
    case 'companion.mood': {
      const mood = event.payload.mood.trim()

      if (mood) {
        $companionMood.set(mood)
      }

      break
    }

    case 'companion.visual_identity.updated':
    case 'companion.appearance_preparation.updated': {
      if (authed()) {
        void refreshAvatarSeeds()
          .then(() => {
            const avatarId = $activeAvatarId.get()

            return avatarId == null ? undefined : hydrateFullbodyReference(avatarId)
          })
          .catch(error => log.warn('avatar-seeds', 'Refresh failed', error))
      }

      break
    }

    default:
      break
  }
}

/** 服务端确认的伙伴声音：只写角色偏好，语音目录失效由应用层触发。 */
export function applyServerVoiceUpdate(voiceId: string): void {
  void applyServerCompanionVoiceId(voiceId).catch(error =>
    log.warn('voice-selection', 'Could not apply server voice:', error)
  )
}
