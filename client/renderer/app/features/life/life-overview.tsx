import { useStore } from '@nanostores/react'
import type React from 'react'

import { $appPage, applyAppNavigation } from '@/app/navigation/app-navigation'
import { $chatSessionId, ensureCompanionSession } from '@/modules/conversation'
import { $lifeError, $lifeFocusedPursuit, $lifeLoading, $lifeState, refreshLife } from '@/modules/life'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { BTN_GHOST, BTN_SUBTLE, ErrorBanner, LoadingBlock } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import styles from './life.module.css'
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
  const failed = useStore($lifeError)
  const strings = useStrings()
  const t = strings.life
  const beginAsync = useAsyncGuard()

  const openChat = async (): Promise<void> => {
    const current = beginAsync()
    const scope = captureAuthScope()

    try {
      const sessionId = await ensureCompanionSession()

      if (!sessionId && current()) {
        throw new Error(strings.chat.nav.loadFailed)
      }

      if (sessionId && current()) {
        onNavigate?.()
        await applyAppNavigation({ page: 'chat', sessionId })
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
      {!state && loading ? (
        <LoadingBlock label={t.loading} />
      ) : (
        <p className={styles.activity}>{state?.current_activity || t.unavailable}</p>
      )}
      {state?.mood && <p className={styles.mood}>{state.mood}</p>}
      {failed && (
        <ErrorBanner message={t.failed} onRetry={() => void refreshLife()} retryLabel={strings.common.retry} />
      )}
      <div className={styles.focused}>
        <span className={styles.eyebrow}>{t.ongoing}</span>
        {pursuit ? (
          <button
            className={styles.pursuitLink}
            onClick={() => {
              onNavigate?.()
              void applyAppNavigation({ page: 'life', pursuitId: pursuit.id })
            }}
            type="button"
          >
            {pursuit.title}
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
              void applyAppNavigation({ page: 'life' })
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
