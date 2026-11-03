import type React from 'react'
import { useState } from 'react'

import { SettingsSectionIntro } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { AppearancePreview } from './appearance-preview'
import { PersonaSection } from './persona-editor'

export function PersonaPage(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.persona
  const [editing, setEditing] = useState(false)

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={t.intro} title={t.title} />
      <PersonaSection
        editorOpen={editing}
        onEditorClosed={() => setEditing(false)}
        onEditRequested={() => setEditing(true)}
      />
      <AppearancePreview />
    </div>
  )
}
