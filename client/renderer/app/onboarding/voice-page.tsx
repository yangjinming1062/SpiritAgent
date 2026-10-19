import { VoiceManager } from '@/app/features/companion/voice-manager'
import { BTN_SUBTLE, HINT_TEXT } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

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
  const t = useStrings().voice

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <VoiceManager
        compact
        disabled={confirming}
        intro={<p className={HINT_TEXT}>{t.onboardingHint}</p>}
        name={name}
        onVoiceSelected={onVoiceSelected}
        preference={preference}
      />
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
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
