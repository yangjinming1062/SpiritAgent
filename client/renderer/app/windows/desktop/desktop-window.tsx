import { IconMaximize as Maximize2, IconMinimize as Minimize2, IconMinus as Minus } from '@tabler/icons-react'
import type React from 'react'
import { useCallback, useEffect, useRef } from 'react'

import { PanelActivityProvider } from '@/shared/context/panel-activity'
import { X } from '@/shared/lib/icons'
import { holdWindowMouseCapture, useInteractiveRegion } from '@/shared/lib/interactive-regions'

import { type DesktopRect, type DesktopWindowState, fitRect } from './desktop-layout'
import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

interface Props {
  children: React.ReactNode
  item: DesktopWindowState
  title: string
  active: boolean
  index: number
  area: { width: number; height: number; left: number; top: number }
  onActivate: () => void
  onClose: () => void
  onChange: (patch: Partial<DesktopWindowState>) => void
}

export function DesktopWindow({
  children,
  item,
  title,
  active,
  index,
  area,
  onActivate,
  onClose,
  onChange
}: Props): React.JSX.Element {
  const t = useDesktopStrings()
  const windowRef = useRef<HTMLElement>(null)
  const releaseCapture = useRef<(() => void) | null>(null)
  useInteractiveRegion(`desktop-window-${item.id}`, windowRef, element =>
    element.hidden ? null : element.getBoundingClientRect()
  )

  const drag = useRef<{
    x: number
    y: number
    bounds: DesktopRect
    resize: boolean
    pointerId: number
    target: HTMLElement
  } | null>(null)

  const bounds = item.maximized
    ? { x: 0, y: 0, width: area.width, height: area.height }
    : fitRect(item.bounds, area.width, area.height)

  const start = (event: React.PointerEvent<HTMLElement>, resize: boolean): void => {
    if (
      drag.current !== null ||
      event.button !== 0 ||
      item.maximized ||
      (event.target instanceof Element && event.target.closest('button') && !resize)
    ) {
      return
    }

    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    releaseCapture.current = holdWindowMouseCapture(1)
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      bounds,
      resize,
      pointerId: event.pointerId,
      target: event.currentTarget
    }
  }

  const finish = useCallback((): void => {
    releaseCapture.current?.()
    releaseCapture.current = null
    const current = drag.current
    drag.current = null

    if (current?.target.hasPointerCapture(current.pointerId)) {
      current.target.releasePointerCapture(current.pointerId)
    }
  }, [])

  useEffect(() => {
    window.addEventListener('blur', finish)

    return () => {
      window.removeEventListener('blur', finish)
      finish()
    }
  }, [finish])

  useEffect(() => {
    if (!active || item.minimized || item.maximized) {
      finish()
    }
  }, [active, finish, item.minimized, item.maximized])

  useEffect(() => finish(), [area.width, area.height, area.left, area.top, finish])

  const move = (event: React.PointerEvent<HTMLElement>): void => {
    const initial = drag.current

    if (!initial || initial.pointerId !== event.pointerId) {
      return
    }

    if ((event.buttons & 1) === 0) {
      finish()

      return
    }

    const dx = event.clientX - initial.x
    const dy = event.clientY - initial.y

    const next = initial.resize
      ? { ...initial.bounds, width: initial.bounds.width + dx, height: initial.bounds.height + dy }
      : { ...initial.bounds, x: initial.bounds.x + dx, y: initial.bounds.y + dy }

    onChange({ bounds: fitRect(next, area.width, area.height) })
  }

  return (
    <section
      aria-label={title}
      className={styles.window}
      data-active={active}
      hidden={item.minimized}
      onFocusCapture={onActivate}
      onPointerDownCapture={onActivate}
      ref={windowRef}
      style={{ left: bounds.x, top: bounds.y, width: bounds.width, height: bounds.height, zIndex: index + 1 }}
    >
      <header
        className={styles.windowHeader}
        onDoubleClick={() => onChange({ maximized: !item.maximized })}
        onLostPointerCapture={finish}
        onPointerCancel={finish}
        onPointerDown={event => start(event, false)}
        onPointerMove={move}
        onPointerUp={finish}
      >
        <span className={styles.windowTitle}>{title}</span>
        <div className={styles.windowControls} onDoubleClick={event => event.stopPropagation()}>
          <button
            aria-label={t.minimize}
            onClick={() => onChange({ minimized: true })}
            title={t.minimize}
            type="button"
          >
            <Minus size={13} />
          </button>
          <button
            aria-label={item.maximized ? t.restore : t.maximize}
            onClick={() => onChange({ maximized: !item.maximized })}
            title={item.maximized ? t.restore : t.maximize}
            type="button"
          >
            {item.maximized ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          </button>
          <button aria-label={t.close} className={styles.closeButton} onClick={onClose} title={t.close} type="button">
            <X size={14} />
          </button>
        </div>
      </header>
      <div className={styles.windowContent}>
        <PanelActivityProvider active={active && !item.minimized}>{children}</PanelActivityProvider>
      </div>
      {!item.maximized && (
        <button
          aria-label={t.resize}
          className={styles.resizeHandle}
          onKeyDown={event => {
            const delta = event.shiftKey ? 40 : 10
            const dx = event.key === 'ArrowRight' ? delta : event.key === 'ArrowLeft' ? -delta : 0
            const dy = event.key === 'ArrowDown' ? delta : event.key === 'ArrowUp' ? -delta : 0

            if (dx || dy) {
              event.preventDefault()
              onChange({
                bounds: fitRect(
                  { ...bounds, width: bounds.width + dx, height: bounds.height + dy },
                  area.width,
                  area.height
                )
              })
            }
          }}
          onLostPointerCapture={finish}
          onPointerCancel={finish}
          onPointerDown={event => start(event, true)}
          onPointerMove={move}
          onPointerUp={finish}
          title={t.resize}
          type="button"
        />
      )}
    </section>
  )
}
