import type React from 'react'

import styles from './desktop.module.css'

const SIGNAL_HEIGHTS = [
  3, 5, 8, 5, 11, 17, 9, 22, 28, 14, 20, 30, 23, 12, 26, 18, 30, 22, 12, 24, 15, 19, 10, 14, 8, 5, 9, 5, 3
]

export function DesktopAvatarSignal({ unread }: { unread: boolean }): React.JSX.Element {
  return (
    <>
      <span aria-hidden="true" className={styles.avatarGlow} />
      {unread && (
        <span aria-hidden="true" className={styles.avatarSignal}>
          <span className={styles.avatarSignalBars}>
            {SIGNAL_HEIGHTS.map((height, index) => (
              <span
                className={styles.avatarSignalBar}
                key={index}
                style={{
                  height,
                  animationDelay: `${-index * 0.11}s`,
                  animationDuration: `${1.1 + (index % 5) * 0.13}s`
                }}
              />
            ))}
          </span>
        </span>
      )}
    </>
  )
}
