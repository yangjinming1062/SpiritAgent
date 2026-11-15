import { useStore } from '@nanostores/react'
import type React from 'react'

import { $appPage, openCompanionMainChat, openLife } from '@/app/navigation/app-navigation'
import { $chatSessionId } from '@/features/conversation'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import { BTN_GHOST, BTN_SUBTLE } from '@/ui'

import styles from '../life.module.css'
import { $lifeFocusedPursuit, $lifeLoading, $lifeState } from '../store'

import { LifeNarrative } from './life-narrative'
import { useLife } from './use-life'

export function LifeOverview({
  visible = true,
  onNavigate,
  showLifeLink = true
}: {
  visible?: boolean
  onNavigate?: () => void
  showLifeLink?: boolean
}): React.JSX.Element {
  useLife(visible)
  const state = useStore($lifeState)
  const pursuit = useStore($lifeFocusedPursuit)
  const loading = useStore($lifeLoading)
  const strings = useStrings()
  const t = strings.life
  const beginAsync = useAsyncGuard()

  const openChat = async (): Promise<void> => {
    const current = beginAsync()
    const scope = captureAuthScope()

    try {
      const sessionId = await openCompanionMainChat()

      if (current()) {
        onNavigate?.()
        requestAnimationFrame(() => {
          if (scope?.() && $appPage.get() === 'chat' && $chatSessionId.get() === sessionId) {
            document.querySelector<HTMLTextAreaElement>('[data-conversation-input="true"]')?.focus()
          }
        })
      }
    } catch (error) {
      if (current()) {
        notifyError(error, t.chatAction)
      }
    }
  }

  return (
    <section aria-label={t.now} className={styles.overview}>
      <span className={styles.eyebrow}>{t.now}</span>
      <LifeNarrative />
      <div className={styles.focused}>
        <span className={styles.eyebrow}>{t.ongoing}</span>
        {pursuit ? (
          <button
            className={styles.pursuitLink}
            onClick={() => {
              onNavigate?.()
              void openLife('now', { pursuitId: pursuit.id })
            }}
            type="button"
          >
            {pursuit.narrative || t.pursuitFallback}
          </button>
        ) : (
          <p className={styles.quiet}>
            {loading ? t.loading : !state || state.active_pursuit_id ? t.unavailable : t.noPursuit}
          </p>
        )}
      </div>
      <div className={styles.actions}>
        {showLifeLink && (
          <button
            className={BTN_SUBTLE}
            onClick={() => {
              onNavigate?.()
              void openLife()
            }}
            type="button"
          >
            {t.overviewAction}
          </button>
        )}
        <button className={BTN_GHOST} onClick={() => void openChat()} type="button">
          {t.chatAction}
        </button>
      </div>
    </section>
  )
}
