import { useStore } from '@nanostores/react'
import type React from 'react'

import { $activeAvatarId } from '@/modules/character'
import { Disclosure, SettingsSectionIntro } from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'

import { CharacterCardSection } from './character-card-section'
import { PersonaSection } from './persona-editor'

export function PersonaPage(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.persona

  const avatarId = useStore($activeAvatarId)
  const english = useStore($locale) === 'en'

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={t.intro} title={t.title} />
      <PersonaSection />
      {avatarId != null && (
        <Disclosure
          hint={
            english
              ? 'Automatically organized from the confirmed images. Adjust only when needed.'
              : '根据已确认的形象自动整理，需要时再调整。'
          }
          title={english ? 'Appearance details' : '外形详细资料'}
        >
          <CharacterCardSection avatarId={avatarId} key={`card-${avatarId}`} />
        </Disclosure>
      )}
    </div>
  )
}
