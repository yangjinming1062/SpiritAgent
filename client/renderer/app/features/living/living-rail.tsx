import { useStore } from '@nanostores/react'
import type React from 'react'

import { $activeAvatarId, $companionMood, $persona, $portraitUrl, $spriteState } from '@/modules/character'
import { $diaryHasUnread } from '@/modules/memory'
import { $postsHasUnread } from '@/modules/posts'
import { triggerHaptic } from '@/shared/lib/haptics'
import {
  CalendarPlus,
  Globe,
  Home,
  type IconComponent,
  MessageSquareText,
  Monitor,
  Settings,
  Shirt,
  Sparkles
} from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

import { useDesktopLifeStrings } from './desktop-life-strings'
import { $livingView, type LivingView, setLivingView } from './living-store'
import styles from './living.module.css'

interface NavEntry {
  icon: IconComponent
  id: LivingView
  label: string
}

export function LivingRail(): React.JSX.Element {
  const t = useStrings().living.rail
  const desktopLife = useDesktopLifeStrings()
  const companionMood = useStore($companionMood)
  const persona = useStore($persona)
  const portrait = useStore($portraitUrl)
  const activeAvatarId = useStore($activeAvatarId)
  const view = useStore($livingView)
  const spriteState = useStore($spriteState)
  const postsHasUnread = useStore($postsHasUnread)
  const diaryHasUnread = useStore($diaryHasUnread)
  const displayName = persona?.name || t.companionFallback
  const moodText = companionMood?.trim()

  const navEntries: NavEntry[] = [
    { icon: MessageSquareText, id: 'chat', label: t.chat },
    { icon: Sparkles, id: 'posts', label: t.posts },
    { icon: CalendarPlus, id: 'diary', label: t.diary },
    { icon: Shirt, id: 'appearance', label: t.wardrobe },
    { icon: Monitor, id: 'desktopLife', label: desktopLife.title },
    { icon: Globe, id: 'remote', label: t.remote },
    { icon: Home, id: 'scene', label: t.scene }
  ]

  return (
    <aside className={styles.rail}>
      <div className={styles.identity}>
        <button
          aria-label={t.avatarMood(displayName)}
          className={styles.avatar}
          data-state={spriteState}
          onClick={() => triggerHaptic('tap')}
          type="button"
        >
          {portrait ? (
            <img alt={displayName} className={styles.avatarImage} src={portrait} />
          ) : activeAvatarId == null ? (
            <span className={styles.avatarFallback}>{displayName.slice(0, 1)}</span>
          ) : null}
        </button>
        <div className={styles.identityText}>
          <p className={styles.displayName}>{displayName}</p>
          {moodText ? (
            <p className={styles.statusMoodText} title={moodText}>
              {moodText}
            </p>
          ) : null}
        </div>
      </div>

      <nav className={styles.nav}>
        {navEntries.map(entry => {
          const Icon = entry.icon
          const isActive = view === entry.id
          const hasUnread = (entry.id === 'posts' && postsHasUnread) || (entry.id === 'diary' && diaryHasUnread)

          return (
            <button
              aria-label={hasUnread ? `${entry.label} (${t.unread})` : entry.label}
              className={cn(styles.navItem, isActive && styles.navItemActive)}
              key={entry.id}
              onClick={() => setLivingView(entry.id)}
              type="button"
            >
              <span className={styles.navItemIconWrap}>
                <Icon className={styles.navItemIcon} />
                {hasUnread && <span aria-hidden="true" className={styles.navUnreadDot} />}
              </span>
              <span className={styles.navItemLabel}>{entry.label}</span>
            </button>
          )
        })}
      </nav>

      <div className={styles.railFooter}>
        <button
          className={cn(styles.navItem, view === 'settings' && styles.navItemActive)}
          onClick={() => setLivingView('settings')}
          type="button"
        >
          <Settings className={styles.navItemIcon} />
          <span className={styles.navItemLabel}>{t.settings}</span>
        </button>
      </div>
    </aside>
  )
}
