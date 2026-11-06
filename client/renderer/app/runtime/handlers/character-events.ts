import {
  $activeAvatarId,
  $companionMood,
  applyServerCompanionVoiceId,
  hydrateFullbodyReference,
  refreshAvatarSeeds
} from '@/features/character'
import { invalidateVoiceCatalog } from '@/features/speech'
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
