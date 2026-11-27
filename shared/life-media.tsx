import './life-media.css'

import type { ComponentType, ReactNode } from 'react'

import type { lifeCopy } from './life-copy'
import type { LifeEpisode } from './life-trajectory'

type MediaCopy = typeof lifeCopy.zh.media

interface LifeMediaTileProps {
  item: LifeEpisode['media'][number]
  active: boolean
  t: MediaCopy
}

export type LifeMediaTile = ComponentType<LifeMediaTileProps>

export function LifeMedia({
  media,
  compact = false,
  active = true,
  t,
  Tile
}: {
  media: LifeEpisode['media']
  compact?: boolean
  active?: boolean
  t: MediaCopy
  Tile: LifeMediaTile
}) {
  if (!media.length) {
    return null
  }
  const items = compact ? media.slice(0, 1) : media
  return (
    <div className="life-media" data-layout={compact ? 'compact' : items.length > 1 ? 'album' : items[0].kind}>
      {items.map(item => (
        <div className="life-media-tile" key={item.id}>
          <Tile active={active} item={item} t={t} />
        </div>
      ))}
    </div>
  )
}

export function LifeMediaPlay({
  children,
  label,
  onClick
}: {
  children: ReactNode
  label: string
  onClick: () => void
}) {
  return (
    <button aria-label={label} className="life-media-button" onClick={onClick} type="button">
      {children}
      <span aria-hidden="true" className="life-media-play" />
    </button>
  )
}

export function LifeMediaError({ label, retry, onRetry }: { label: string; retry: string; onRetry: () => void }) {
  return (
    <div className="life-media-error" role="status">
      <span>{label}</span>
      <button onClick={onRetry} type="button">
        {retry}
      </button>
    </div>
  )
}
