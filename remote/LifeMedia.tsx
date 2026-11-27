import { useRef, useState } from 'react'

import { LifeMediaError, type LifeMediaTile } from '../shared/life-media'

import { pauseMedia } from './ui'

type TileProps = React.ComponentProps<LifeMediaTile>

function LifePhoto({ item, t, onError }: Pick<TileProps, 'item' | 't'> & { onError: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [open, setOpen] = useState(false)

  const enlarge = () => {
    dialog.current?.showModal()
    setOpen(true)
  }

  return (
    <>
      <button
        aria-label={item.alt ? `${t.openImage} · ${item.alt}` : t.openImage}
        className="life-media-button"
        onClick={enlarge}
        type="button"
      >
        <img alt={item.alt} className="life-media-surface" loading="lazy" onError={onError} src={item.url} />
      </button>
      <dialog
        aria-label={item.alt || t.openImage}
        className="life-media-dialog"
        onClose={() => setOpen(false)}
        ref={dialog}
      >
        {open && <img alt={item.alt} src={item.url} />}
        <button onClick={() => dialog.current?.close()} type="button">
          {t.closeImage}
        </button>
      </dialog>
    </>
  )
}

function LifeTileContent({ item, t }: Pick<TileProps, 'item' | 't'>) {
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)

  const fail = () => setFailed(true)
  const retry = () => {
    setFailed(false)
    setAttempt(value => value + 1)
  }

  if (failed) {
    return <LifeMediaError label={t.failed} onRetry={retry} retry={t.retry} />
  }
  if (item.kind === 'image') {
    return <LifePhoto item={item} key={attempt} onError={fail} t={t} />
  }
  return (
    <video
      aria-label={item.alt || t.playVideo}
      className="life-media-surface"
      controls
      key={attempt}
      onError={fail}
      onPlay={event => pauseMedia(event.currentTarget)}
      playsInline
      poster={item.poster_url ?? undefined}
      preload="none"
      src={item.url}
    />
  )
}

export const RemoteLifeMediaTile: LifeMediaTile = ({ active, item, t }) =>
  active ? <LifeTileContent item={item} key={item.url} t={t} /> : null
