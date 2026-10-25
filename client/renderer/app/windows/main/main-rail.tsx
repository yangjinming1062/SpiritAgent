import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { AccountMenu } from '@/app/features/accounts/account-menu'
import { $appPage, applyAppNavigation, setAppPage } from '@/app/navigation/app-navigation'
import { $persona, $portraitUrl } from '@/modules/character'
import { $companionSessionId, ensureCompanionSession } from '@/modules/conversation'
import { $diaryHasUnread } from '@/modules/diary'
import { $postsHasUnread } from '@/modules/posts'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useDismissOnOutside } from '@/shared/hooks/use-dismiss-on-outside'
import { triggerHaptic } from '@/shared/lib/haptics'
import { BookOpen, Home, type IconComponent, MessageSquareText, Settings, Sparkles, Users } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { AppPage } from '@ipc/contracts'

import styles from './main.module.css'

const NAV_ENTRIES: ReadonlyArray<{ icon: IconComponent; id: Exclude<AppPage, 'settings'> }> = [
  { icon: MessageSquareText, id: 'chat' },
  { icon: Home, id: 'life' },
  { icon: Sparkles, id: 'posts' },
  { icon: BookOpen, id: 'diary' }
]

export function MainRail({
  labels,
  companionUnread,
  chatUnread,
  onOverlayChange
}: {
  labels: Record<AppPage, string>
  companionUnread: boolean
  chatUnread: boolean
  onOverlayChange: (open: boolean) => void
}): React.JSX.Element {
  const page = useStore($appPage)
  const persona = useStore($persona)
  const portrait = useStore($portraitUrl)
  const postsUnread = useStore($postsHasUnread)
  const diaryUnread = useStore($diaryHasUnread)
  const strings = useStrings()
  const t = strings.living.rail
  const name = persona?.name || t.companionFallback
  const [accountOpen, setAccountOpen] = useState(false)
  const accountRef = useRef<HTMLDivElement>(null)
  const accountButtonRef = useRef<HTMLButtonElement>(null)
  const beginAsync = useAsyncGuard()
  const unreadByPage: Partial<Record<AppPage, boolean>> = { chat: chatUnread, posts: postsUnread, diary: diaryUnread }

  const closeAccounts = (): void => {
    setAccountOpen(false)
    onOverlayChange(false)
    accountButtonRef.current?.focus()
  }

  useDismissOnOutside(accountRef, accountOpen, closeAccounts)

  const openCompanion = async (): Promise<void> => {
    const isLive = beginAsync()
    triggerHaptic('tap')

    try {
      const sessionId = $companionSessionId.get() ?? (await ensureCompanionSession())

      if (sessionId && isLive()) {
        await applyAppNavigation({ page: 'chat', sessionId })
      }
    } catch (error) {
      if (isLive()) {
        notifyError(error, labels.chat)
      }
    }
  }

  return (
    <aside className={styles.rail}>
      <button
        aria-label={`${name} · ${strings.chat.nav.companion}${companionUnread ? ` (${t.unread})` : ''}`}
        className={styles.avatar}
        data-unread={companionUnread}
        onClick={() => void openCompanion()}
        title={name}
        type="button"
      >
        {portrait ? <img alt="" className={styles.avatarImage} src={portrait} /> : <span>{name.slice(0, 1)}</span>}
        {companionUnread && <span aria-hidden="true" className={styles.avatarUnread} />}
      </button>
      <nav aria-label={t.mainNavAria} className={styles.nav}>
        {NAV_ENTRIES.map(({ id, icon: Icon }) => {
          const unread = unreadByPage[id] ?? false

          return (
            <button
              aria-current={page === id ? 'page' : undefined}
              aria-label={unread ? `${labels[id]} (${t.unread})` : labels[id]}
              className={cn(styles.navItem, page === id && styles.navItemActive)}
              key={id}
              onClick={() => (id === 'chat' ? void openCompanion() : setAppPage(id))}
              type="button"
            >
              <span className={styles.navIcon}>
                <Icon aria-hidden="true" size={21} />
                {unread && <span aria-hidden="true" className={styles.unreadDot} />}
              </span>
              <span>{labels[id]}</span>
            </button>
          )
        })}
      </nav>
      <div className={styles.railFooter}>
        <button
          aria-current={page === 'companion' ? 'page' : undefined}
          className={cn(styles.navItem, page === 'companion' && styles.navItemActive)}
          onClick={() => setAppPage('companion')}
          type="button"
        >
          <Users aria-hidden="true" size={21} />
          <span>{labels.companion}</span>
        </button>
        <button
          aria-current={page === 'settings' ? 'page' : undefined}
          className={cn(styles.navItem, page === 'settings' && styles.navItemActive)}
          onClick={() => setAppPage('settings')}
          type="button"
        >
          <Settings aria-hidden="true" size={21} />
          <span>{labels.settings}</span>
        </button>
        <div className={styles.accountAnchor} ref={accountRef}>
          <button
            aria-expanded={accountOpen}
            aria-haspopup="menu"
            className={styles.navItem}
            onClick={() => {
              const next = !accountOpen
              setAccountOpen(next)
              onOverlayChange(next)
            }}
            ref={accountButtonRef}
            type="button"
          >
            <Users aria-hidden="true" size={21} />
            <span>{strings.accounts.title}</span>
          </button>
          {accountOpen && <AccountMenu onClose={closeAccounts} placement="above" />}
        </div>
      </div>
    </aside>
  )
}
