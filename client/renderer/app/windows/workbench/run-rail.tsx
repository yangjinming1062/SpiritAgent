// 工作台运行轨迹：本轮工具调用与本会话工件

import { useStore } from '@nanostores/react'
import type React from 'react'

import { $artifacts, $isRailOpen, $runRound, setRailOpen, toggleRail } from '@/app/windows/workbench/run-rail-store'
import { ChatMediaCard } from '@/modules/conversation'
import { X } from '@/shared/lib/icons'
import { useStrings } from '@/shared/strings'

import styles from './workbench.module.css'

export function RunRail(): React.JSX.Element {
  const open = useStore($isRailOpen)
  const round = useStore($runRound)
  const artifacts = useStore($artifacts)
  const t = useStrings().workbench.runRail

  // 用户手动折叠右栏后保留 0 宽，仅一个唤起条；展开恢复 320。
  if (!open) {
    return (
      <aside className={styles.runRailCollapsed}>
        <button
          aria-label={t.expandAria}
          className={styles.runRailExpand}
          onClick={() => setRailOpen(true)}
          type="button"
        >
          <span className={styles.runRailExpandLabel}>{t.label}</span>
        </button>
      </aside>
    )
  }

  return (
    <aside className={styles.runRail}>
      <header className={styles.runRailHeader}>
        <div className="flex items-center gap-2">
          <span className={styles.blueDot} />
          <div>
            <h3 className={styles.runRailTitle}>{t.label}</h3>
            <span className={styles.runRailSubtitle}>{round ? t.subtitle(round.steps.length) : t.idleSubtitle}</span>
          </div>
        </div>
        <button
          aria-label={t.collapseAria}
          className={styles.runRailCollapse}
          onClick={() => toggleRail()}
          title={t.collapseTitle}
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
                <ChatMediaCard
                  item={{ type: artifact.kind, url: artifact.url, audio_url: artifact.audioUrl }}
                  key={artifact.id}
                />
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
  children: React.ReactNode
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
