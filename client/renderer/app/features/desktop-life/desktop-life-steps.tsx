import { Check } from '@/shared/lib/icons'

import { useDesktopLifeStrings } from './desktop-life-strings'
import styles from './desktop-life.module.css'

export function DesktopLifeSteps({
  step,
  skipImage = false
}: {
  step: number
  skipImage?: boolean
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const labels = [t.stepIdea, ...(skipImage ? [] : [t.stepImage]), t.stepVideo, t.stepUse]
  const currentStep = skipImage && step >= 2 ? step - 1 : step

  return (
    <ol aria-label={t.createSet} className={styles.steps}>
      {labels.map((label, index) => (
        <li
          aria-current={currentStep === index ? 'step' : undefined}
          className={styles.step}
          data-complete={index < currentStep}
          key={label}
        >
          <span aria-hidden="true" className={styles.stepNumber}>
            {index < currentStep ? <Check size={14} /> : index + 1}
          </span>
          <span>{label}</span>
        </li>
      ))}
    </ol>
  )
}
