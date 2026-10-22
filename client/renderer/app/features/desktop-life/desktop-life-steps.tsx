import { Check } from '@/shared/lib/icons'
import { useStrings } from '@/shared/strings'

import styles from './desktop-life.module.css'

export function DesktopLifeSteps({ step }: { step: 0 | 1 | 2 }): React.JSX.Element {
  const strings = useStrings()
  const t = strings.desktopLife
  const labels = [t.stepIdea, t.stepProduction, t.stepReady]

  return (
    <ol aria-label={t.createSet} className={styles.steps}>
      {labels.map((label, index) => (
        <li
          aria-current={step === index ? 'step' : undefined}
          className={styles.step}
          data-complete={index < step}
          key={label}
        >
          <span aria-hidden="true" className={styles.stepNumber}>
            {index < step ? <Check size={14} /> : index + 1}
          </span>
          <span>{label}</span>
        </li>
      ))}
    </ol>
  )
}
