import '../../../styles.css'

import { useEffect, useRef } from 'react'
import { createRoot } from 'react-dom/client'

import { createDesktopPlayer } from './player/engine'

function DesktopWallpaper() {
  const first = useRef<HTMLVideoElement>(null)
  const second = useRef<HTMLVideoElement>(null)
  const backdrop = useRef<HTMLDivElement>(null)
  const poster = useRef<HTMLImageElement>(null)
  const transitionFrame = useRef<HTMLCanvasElement>(null)
  const actorFirst = useRef<HTMLVideoElement>(null)
  const actorSecond = useRef<HTMLVideoElement>(null)
  const actorPoster = useRef<HTMLImageElement>(null)
  const actorRoot = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const firstVideo = first.current
    const secondVideo = second.current
    const fill = backdrop.current
    const still = poster.current
    const transition = transitionFrame.current
    const actorFirstVideo = actorFirst.current
    const actorSecondVideo = actorSecond.current
    const actorStill = actorPoster.current
    const actorContainer = actorRoot.current

    if (
      !firstVideo ||
      !secondVideo ||
      !fill ||
      !still ||
      !transition ||
      !actorFirstVideo ||
      !actorSecondVideo ||
      !actorStill ||
      !actorContainer
    ) {
      throw new Error('Desktop background bridge is unavailable')
    }

    return createDesktopPlayer({
      first: firstVideo,
      second: secondVideo,
      backdrop: fill,
      poster: still,
      transitionFrame: transition,
      actorFirst: actorFirstVideo,
      actorSecond: actorSecondVideo,
      actorPoster: actorStill,
      actorRoot: actorContainer
    })
  }, [])

  const videoStyle = {
    position: 'absolute',
    left: 0,
    top: 0,
    width: '100%',
    height: '100%',
    objectFit: 'contain',
    opacity: 0
  } as const

  return (
    <div
      aria-hidden="true"
      style={{ position: 'fixed', inset: 0, overflow: 'hidden', backgroundColor: 'var(--ui-app-bg)' }}
    >
      <div
        ref={backdrop}
        style={{
          position: 'absolute',
          inset: -32,
          backgroundPosition: 'center',
          backgroundSize: 'cover',
          filter: 'blur(24px) brightness(.6)',
          transition: 'filter 1s ease'
        }}
      />
      <img alt="" ref={poster} style={{ ...videoStyle, zIndex: 1, transition: 'filter 1s ease' }} />
      <video muted playsInline preload="auto" ref={first} style={{ ...videoStyle, transition: 'filter 1s ease' }} />
      <video muted playsInline preload="auto" ref={second} style={{ ...videoStyle, transition: 'filter 1s ease' }} />
      <div ref={actorRoot} style={{ position: 'absolute', inset: 0, zIndex: 2, opacity: 0 }}>
        <img
          alt=""
          ref={actorPoster}
          style={{ ...videoStyle, height: '90%', width: '50%', left: '25%', top: '10%', objectPosition: 'bottom' }}
        />
        <video muted playsInline preload="auto" ref={actorFirst} style={videoStyle} />
        <video muted playsInline preload="auto" ref={actorSecond} style={videoStyle} />
      </div>
      <canvas ref={transitionFrame} style={{ ...videoStyle, zIndex: 3, pointerEvents: 'none' }} />
    </div>
  )
}

const root = document.getElementById('root')

if (!root) {
  throw new Error('desktop-background: missing root element')
}

createRoot(root).render(<DesktopWallpaper />)
