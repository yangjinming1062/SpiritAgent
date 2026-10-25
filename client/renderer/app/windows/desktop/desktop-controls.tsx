import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useId, useRef, useState } from 'react'

import { usePresentationModeSwitch } from '@/app/components/presentation-mode-switch'
import { $userPreferredTier, setDisturbanceTier } from '@/modules/character'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { Monitor, SlidersHorizontal, Video, Wifi, WifiOff, X } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { presentationDisplayOptions } from '@/shared/lib/presentation-displays'
import { PanelSelect } from '@/shared/panel'
import { shellApi } from '@/shared/platform/desktop'
import { $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import styles from './desktop-controls.module.css'

type ConnectionTone = 'offline' | 'online' | 'pending'

const CONNECTION_TONE: Record<ConnectionState, ConnectionTone> = {
  closed: 'offline',
  connecting: 'pending',
  error: 'offline',
  idle: 'pending',
  open: 'online'
}

interface DesktopControlsProps {
  open: boolean
  onOpenChange: (open: boolean, restoreFocus?: boolean) => void
  onOpenVisuals: () => void
  wallpaperStatus: string | null
  wallpaperError: boolean
}

export function DesktopControls({
  open,
  onOpenChange,
  onOpenVisuals,
  wallpaperStatus,
  wallpaperError
}: DesktopControlsProps): React.JSX.Element {
  const strings = useStrings()
  const t = strings.desktopShell
  const { busy, switchMode, state } = usePresentationModeSwitch()
  const preferredTier = useStore($userPreferredTier)
  const gatewayState = useStore($gatewayState)
  const locale = useStore($locale)
  const [clock, setClock] = useState(() => new Date())
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const gapRef = useRef<HTMLDivElement>(null)
  const changeOpenRef = useLatestRef(onOpenChange)
  const panelId = useId()
  const headingId = useId()
  const connectionTone = CONNECTION_TONE[gatewayState]
  const connectionLabel = { offline: t.disconnected, online: t.connected, pending: t.loading }[connectionTone]

  const hasIssue =
    connectionTone === 'offline' || wallpaperError || Boolean(state.compatibilityWarning || state.switchBlockedReason)

  useInteractiveRegion('desktop-control-button', triggerRef)
  useInteractiveRegion('desktop-control-panel', panelRef)
  useInteractiveRegion('desktop-control-gap', gapRef)

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 30000)

    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!open) {
      return
    }

    panelRef.current?.focus({ preventScroll: true })

    const closeOutside = (event: PointerEvent): void => {
      if (!rootRef.current?.contains(event.target as Node | null)) {
        changeOpenRef.current(false)
      }
    }

    const closeOnBlur = (): void => changeOpenRef.current(false)

    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        event.preventDefault()
        changeOpenRef.current(false, true)
        triggerRef.current?.focus({ preventScroll: true })
      }
    }

    // 下拉框先处理自己的 Esc；面板只在冒泡阶段收起。
    window.addEventListener('pointerdown', closeOutside)
    window.addEventListener('blur', closeOnBlur)
    window.addEventListener('keydown', closeOnEscape)

    return () => {
      window.removeEventListener('pointerdown', closeOutside)
      window.removeEventListener('blur', closeOnBlur)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [changeOpenRef, open])

  const closePanel = (): void => {
    onOpenChange(false, true)
    triggerRef.current?.focus({ preventScroll: true })
  }

  return (
    <div
      className={styles.root}
      onBlur={event => {
        if (open && event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) {
          onOpenChange(false)
        }
      }}
      ref={rootRef}
    >
      <button
        aria-controls={open ? panelId : undefined}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={t.desktopSettings}
        className={styles.trigger}
        data-issue={hasIssue}
        onClick={() => onOpenChange(!open)}
        ref={triggerRef}
        title={t.desktopSettings}
        type="button"
      >
        <SlidersHorizontal aria-hidden="true" size={20} />
        {hasIssue && (
          <span className={styles.issueDot}>
            <span className={styles.srOnly}>{t.needsAttention}</span>
          </span>
        )}
      </button>
      {open && (
        <>
          <div aria-hidden="true" className={styles.gap} ref={gapRef} />
          <div
            aria-labelledby={headingId}
            className={styles.panel}
            id={panelId}
            ref={panelRef}
            role="dialog"
            tabIndex={-1}
          >
            <header className={styles.header}>
              <strong id={headingId}>{t.desktopSettings}</strong>
              <button aria-label={t.close} className={styles.close} onClick={closePanel} type="button">
                <X aria-hidden="true" size={16} />
              </button>
            </header>
            <time className={styles.clock} dateTime={clock.toISOString()}>
              <span className={styles.time}>
                {clock.toLocaleTimeString(locale === 'en' ? 'en-US' : 'zh-CN', {
                  hour: '2-digit',
                  minute: '2-digit'
                })}
              </span>
              <span className={styles.date}>
                {clock.toLocaleDateString(locale === 'en' ? 'en-US' : 'zh-CN', {
                  year: 'numeric',
                  month: 'long',
                  day: 'numeric',
                  weekday: 'long'
                })}
              </span>
            </time>
            <span className={styles.connection} data-tone={connectionTone} role="status">
              {connectionTone === 'offline' ? (
                <WifiOff aria-hidden="true" size={15} />
              ) : (
                <Wifi aria-hidden="true" size={15} />
              )}
              {connectionLabel}
            </span>
            <button
              className={styles.wallpaper}
              data-error={wallpaperError}
              onClick={() => {
                onOpenChange(false)
                onOpenVisuals()
              }}
              type="button"
            >
              <Video aria-hidden="true" size={17} />
              <span>
                <strong>{strings.visuals.title}</strong>
                {wallpaperStatus && <span role="status">{wallpaperStatus}</span>}
              </span>
            </button>
            <div className={styles.field}>
              <span>{t.display}</span>
              <PanelSelect
                ariaLabel={t.display}
                disabled={busy || !state.displays.length}
                onChange={value =>
                  value &&
                  void shellApi()
                    .presentation.setDisplay(Number(value))
                    .catch(error => notifyError(error, strings.common.operationFailed))
                }
                options={presentationDisplayOptions(state, t)}
                value={String(state.displayId ?? '')}
                widthClass="w-full"
              />
              <span className={styles.hint}>{t.displayHint}</span>
            </div>
            <div className={styles.field}>
              <span>{t.disturbance}</span>
              <PanelSelect
                ariaLabel={t.disturbance}
                onChange={setDisturbanceTier}
                options={[
                  { value: 'still', label: t.still },
                  { value: 'normal', label: t.normal },
                  { value: 'autonomous', label: t.autonomous }
                ]}
                value={preferredTier}
                widthClass="w-full"
              />
            </div>
            {state.compatibilityWarning && (
              <p className={styles.notice} role="status">
                {state.compatibilityWarning}
              </p>
            )}
            {state.switchBlockedReason && (
              <p className={styles.notice} role="status">
                {state.switchBlockedReason}
              </p>
            )}
            <footer className={styles.actions}>
              <button
                disabled={busy}
                onClick={() => void switchMode('window')}
                title={state.switchBlockedReason ?? t.windowMode}
                type="button"
              >
                <Monitor aria-hidden="true" size={16} />
                {t.windowMode}
              </button>
              <button
                className={styles.quit}
                onClick={() =>
                  void shellApi()
                    .desktop.quit()
                    .catch(error => notifyError(error, t.quit))
                }
                type="button"
              >
                {t.quit}
              </button>
            </footer>
          </div>
        </>
      )}
    </div>
  )
}
