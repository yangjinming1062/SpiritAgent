import { useStore } from '@nanostores/react'
import type React from 'react'

import { $activeAvatarId, $companionMood, $persona, $portraitUrl, $spriteEmotion, $spriteState } from '@/companion'
import { triggerHaptic } from '@/shared/lib/haptics'
import {
  CalendarPlus,
  Globe,
  Home,
  type IconComponent,
  MessageSquareText,
  Palette,
  Settings,
  Shirt,
  Sparkles
} from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { $livingView, type LivingView, setLivingView } from './living-store'
import styles from './living.module.css'

interface NavEntry {
  icon: IconComponent
  id: LivingView
  label: string
}

const NAV_ENTRIES: NavEntry[] = [
  { icon: MessageSquareText, id: 'chat', label: '对话' },
  { icon: Sparkles, id: 'moments', label: '片刻' },
  { icon: CalendarPlus, id: 'diary', label: '日记' },
  { icon: Shirt, id: 'wardrobe', label: '衣橱' },
  { icon: Palette, id: 'appearance', label: '形象' },
  { icon: Globe, id: 'channels', label: '通道' },
  { icon: Home, id: 'room', label: '房间' },
  { icon: Settings, id: 'settings', label: '设置' }
]

export function LivingRail(): React.JSX.Element {
  const companionMood = useStore($companionMood)
  const persona = useStore($persona)
  const portrait = useStore($portraitUrl)
  const activeAvatarId = useStore($activeAvatarId)
  const view = useStore($livingView)
  const spriteState = useStore($spriteState)
  const emotion = useStore($spriteEmotion)
  const displayName = persona?.name ?? '伙伴'
  const moodText = companionMood?.trim() || persona?.personality?.trim() || ''

  return (
    <aside className={styles.rail}>
      <div className={styles.identity}>
        <button
          aria-label={`${displayName} 的表情反馈`}
          className={styles.avatar}
          data-emotion={emotion && emotion !== 'neutral' ? emotion : undefined}
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
          <span className={styles.statusDesc} title={moodText || undefined}>
            {moodText}
          </span>
        </div>
      </div>

      <nav className={styles.nav}>
        {NAV_ENTRIES.map(entry => {
          const Icon = entry.icon
          const isActive = view === entry.id

          return (
            <button
              className={cn(styles.navItem, isActive && styles.navItemActive)}
              key={entry.id}
              onClick={() => setLivingView(entry.id)}
              type="button"
            >
              <Icon className={styles.navItemIcon} />
              <span className={styles.navItemLabel}>{entry.label}</span>
            </button>
          )
        })}
      </nav>
    </aside>
  )
}
