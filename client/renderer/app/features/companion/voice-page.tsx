import { useStore } from '@nanostores/react'

import { $persona } from '@/modules/character'
import { SettingsSectionIntro } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { VoiceManager } from './voice-manager'

export function VoicePage(): React.JSX.Element {
  const persona = useStore($persona)
  const t = useStrings().voice

  return (
    <div className="space-y-4">
      <SettingsSectionIntro hint={t.intro} title={t.title} />
      <VoiceManager name={persona?.name ?? ''} />
    </div>
  )
}
