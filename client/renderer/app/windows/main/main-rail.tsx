import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef, useState } from 'react'

import { AccountMenu } from '@/app/features/accounts/account-menu'
import { $creationBusy } from '@/app/features/creation/creation-activity'
import {
  $appPage,
  openChat,
  openCompanion,
  openCreation,
  openLife,
  openSettings
} from '@/app/navigation/app-navigation'
import { $persona, $portraitUrl } from '@/modules/character'
import { $diaryHasUnread } from '@/modules/diary'
import { $postsHasUnread } from '@/modules/posts'
import { useDismissOnOutside } from '@/shared/hooks/use-dismiss-on-outside'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Home, type IconComponent, MessageSquareText, Palette, Settings, Sparkles, Users } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import type { AppPage } from '@ipc/contracts'

import styles from './main.module.css'

type RailPage = Exclude<AppPage, 'settings'>

const NAV_ENTRIES: ReadonlyArray<{ icon: IconComponent; id: RailPage }> = [
  { icon: MessageSquareText, id: 'chat' },
  { icon: Home, id: 'life' },
  { icon: Palette, id: 'creation' },
  { icon: Sparkles, id: 'companion' }
]

export function MainRail({
  labels,
  chatUnread,
  onOverlayChange
}: {
  labels: Record<AppPage, string>
  chatUnread: boolean
  onOverlayChange: (open: boolean) => void
}): React.JSX.Element {
  const page = useStore($appPage)
  const persona = useStore($persona)
  const portrait = useStore($portraitUrl)
  const postsUnread = useStore($postsHasUnread)
  const diaryUnread = useStore($diaryHasUnread)
  const creationBusy = useStore($creationBusy)
  const strings = useStrings()
  const t = strings.living.rail
  const name = persona?.name || t.companionFallback
  const [accountOpen, setAccountOpen] = useState(false)
  const accountRef = useRef<HTMLDivElement>(null)
  const accountButtonRef = useRef<HTMLButtonElement>(null)

  const unreadByPage: Partial<Record<RailPage, boolean>> = {
    chat: chatUnread,
    life: postsUnread || diaryUnread,
    creation: creationBusy
  }

  const closeAccounts = (): void => {
    setAccountOpen(false)
    onOverlayChange(false)
    accountButtonRef.current?.focus()
  }

  useDismissOnOutside(accountRef, accountOpen, closeAccounts)

  const navigate = (id: RailPage): void => {
    triggerHaptic('tap')
    void (id === 'chat'
      ? openChat()
      : id === 'life'
        ? openLife()
        : id === 'creation'
          ? openCreation()
          : openCompanion())
  }

  return (
    <aside className={styles.rail}>
      <button
        aria-label={`${name} · ${strings.chat.nav.companion}${chatUnread ? ` (${t.unread})` : ''}`}
        className={styles.avatar}
        data-unread={chatUnread}
        onClick={() => navigate('chat')}
        title={name}
        type="button"
      >
        {portrait ? <img alt="" className={styles.avatarImage} src={portrait} /> : <span>{name.slice(0, 1)}</span>}
        {chatUnread && <span aria-hidden="true" className={styles.avatarUnread} />}
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
              onClick={() => navigate(id)}
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
          aria-current={page === 'settings' ? 'page' : undefined}
          className={cn(styles.navItem, page === 'settings' && styles.navItemActive)}
          onClick={() => void openSettings()}
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
