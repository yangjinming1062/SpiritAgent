import { useStrings } from '@/shared/strings'
import { SettingsSectionIntro } from '@/ui'

import { VoiceManager } from './voice-manager'

export function VoicePage(): React.JSX.Element {
  const t = useStrings().voice

  return <VoiceManager intro={<SettingsSectionIntro hint={t.intro} title={t.title} />} />
}
