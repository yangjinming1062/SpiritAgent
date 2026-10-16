import { VoiceManager } from '@/app/features/companion/voice-manager'
import { BTN_SUBTLE, HINT_TEXT } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface VoicePageProps {
  name: string
  preference?: string
  onDescribe: () => void
  onVoiceSelected: (voiceId: string, label: string, serverConfirmed: boolean) => Promise<void>
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
  const t = useStrings().voice

  return (
    <div className="space-y-4">
      <p className={HINT_TEXT}>{t.onboardingHint}</p>
      <VoiceManager disabled={confirming} name={name} onVoiceSelected={onVoiceSelected} preference={preference} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button className={BTN_SUBTLE} disabled={confirming} onClick={onDescribe} type="button">
          {t.describePreference}
        </button>
        <button className={BTN_SUBTLE} disabled={confirming} onClick={onContinueText} type="button">
          {t.continueText}
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
