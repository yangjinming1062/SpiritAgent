import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useMemo, useRef } from 'react'

import { createRunRailStores } from '@/app/features/execution/run-rail-store'
import { ChatMediaCard, useConversationView } from '@/modules/conversation'
import { createFocusCycleHandler } from '@/shared/lib/focus-cycle'
import { X } from '@/shared/lib/icons'
import { useStrings } from '@/shared/strings'

import styles from './run-rail.module.css'

export function RunRail({
  closeLabel,
  id,
  onClose,
  title
}: {
  closeLabel: string
  id: string
  onClose: () => void
  title: string
}): React.JSX.Element {
  const { runtime } = useConversationView()
  const { $runRound, $artifacts } = useMemo(() => createRunRailStores(runtime), [runtime])
  const round = useStore($runRound)
  const artifacts = useStore($artifacts)
  const t = useStrings().conversation.runRail
  const closeRef = useRef<HTMLButtonElement>(null)
  const cycleFocus = createFocusCycleHandler('button, a[href], input, audio[controls], video[controls], [tabindex="0"]')

  useEffect(() => closeRef.current?.focus(), [])

  return (
    <aside
      aria-labelledby={`${id}-title`}
      aria-modal="true"
      className={styles.runRail}
      id={id}
      onKeyDown={event => {
        if (event.key === 'Escape') {
          event.preventDefault()
          event.stopPropagation()
          onClose()

          return
        }

        cycleFocus(event)
      }}
      role="dialog"
    >
      <header className={styles.runRailHeader}>
        <div className="flex items-center gap-2">
          <span className={styles.blueDot} />
          <div>
            <h3 className={styles.runRailTitle} id={`${id}-title`}>
              {title}
            </h3>
            <span className={styles.runRailSubtitle}>{round ? t.subtitle(round.steps.length) : t.idleSubtitle}</span>
          </div>
        </div>
        <button
          aria-label={closeLabel}
          className={styles.runRailCollapse}
          onClick={onClose}
          ref={closeRef}
          title={closeLabel}
          type="button"
        >
          <X className="size-3.5" />
        </button>
      </header>

      <div className={styles.runRailBody}>
        <Section
          subtitle={round?.steps.length ? t.toolsSubtitleSteps(round.steps.length) : t.toolsSubtitleIdle}
          title={t.toolsTitle}
        >
          {round && round.steps.length > 0 ? (
            <ol className={styles.steps}>
              {round.steps.map((step, idx) => (
                <li className={`${styles.step} ${step.active ? styles.stepActive : ''}`} key={`${step.name}-${idx}`}>
                  <span className={styles.stepIndex}>{idx + 1}</span>
                  <span className={styles.stepName}>{step.name}</span>
                  {step.active ? <span aria-hidden="true" className={styles.stepPulse} /> : null}
                </li>
              ))}
            </ol>
          ) : (
            <p className={styles.sectionEmpty}>{round?.active ? t.preparing : t.toolsEmpty}</p>
          )}
        </Section>

        <Section
          subtitle={artifacts.length ? t.artifactsSubtitleCount(artifacts.length) : t.artifactsSubtitleEmpty}
          title={t.artifactsTitle}
        >
          {artifacts.length > 0 ? (
            <div className={styles.artifacts}>
              {artifacts.map(artifact => (
                <ChatMediaCard item={artifact.media} key={artifact.id} />
              ))}
            </div>
          ) : (
            <p className={styles.sectionEmpty}>{t.artifactsEmpty}</p>
          )}
        </Section>
      </div>
    </aside>
  )
}

interface SectionProps {
  children: ReactNode
  subtitle?: string
  title: string
}

function Section({ children, subtitle, title }: SectionProps): React.JSX.Element {
  return (
    <section className={styles.section}>
      <header className={styles.sectionHeader}>
        <h4 className={styles.sectionTitle}>{title}</h4>
        {subtitle ? <span className={styles.sectionSubtitle}>{subtitle}</span> : null}
      </header>
      <div className={styles.sectionBody}>{children}</div>
    </section>
  )
}
