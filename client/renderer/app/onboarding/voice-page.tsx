import { VoiceManager } from '@/app/features/companion/voice-manager'
import { BTN_SUBTLE, HINT_TEXT } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import styles from './onboarding.module.css'

interface VoicePageProps {
  name: string
  preference?: string
  onDescribe: () => void
  onVoiceSelected: (
    voiceId: string,
    label: string,
    serverConfirmed: boolean,
    isSelectionCurrent?: () => boolean
  ) => Promise<boolean | void>
  onContinueText: () => void
  confirming: boolean
  error: string | null
}

export function VoicePage({
  name,
  preference,
  onDescribe,
  onVoiceSelected,
  onContinueText,
  confirming,
  error
}: VoicePageProps): React.JSX.Element {
  const strings = useStrings()
  const t = strings.onboarding
  const voice = strings.voice

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <h2 className={`shrink-0 ${styles.questionTitle}`}>{t.voiceTitle}</h2>
      <VoiceManager
        compact
        disabled={confirming}
        intro={<p className={HINT_TEXT}>{voice.onboardingHint}</p>}
        name={name}
        onVoiceSelected={onVoiceSelected}
        preference={preference}
      />
      <div className={styles.voiceNavigation}>
        <button className={BTN_SUBTLE} disabled={confirming} onClick={onDescribe} type="button">
          {voice.describePreference}
        </button>
        <button className={BTN_SUBTLE} disabled={confirming} onClick={onContinueText} type="button">
          {voice.continueText}
        </button>
      </div>
      {error && (
        <p className="text-xs text-danger-fg" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
