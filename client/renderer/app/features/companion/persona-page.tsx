import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { $activeAvatarId } from '@/modules/character'
import { SettingsSectionIntro } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { CharacterCardSection } from './character-card-section'
import { PersonaSection } from './persona-editor'

export function PersonaPage(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.persona

  const avatarId = useStore($activeAvatarId)
  const [activeEditor, setActiveEditor] = useState<'persona' | 'card' | null>(null)

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={t.intro} title={t.title} />
      <PersonaSection
        editorOpen={activeEditor === 'persona'}
        onEditorClosed={() => setActiveEditor(null)}
        onEditRequested={() => setActiveEditor('persona')}
      />
      {avatarId != null && (
        <CharacterCardSection
          avatarId={avatarId}
          editorOpen={activeEditor === 'card'}
          key={`card-${avatarId}`}
          onEditorClosed={() => setActiveEditor(null)}
          onEditRequested={() => setActiveEditor('card')}
        />
      )}
    </div>
  )
}
