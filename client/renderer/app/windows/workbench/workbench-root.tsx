// 工作台根组件：三栏 + 顶栏 + 全屏工位环境设置。
//
// 三栏：会话侧栏（256） / 对话（flex） / Run Rail（320，可折到 0）；
// 顶栏：会话名 + 工位环境 + 回生活空间；工位环境打开时挂载全屏设置并隐藏底层会话区。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { SpriteStatusBadge } from '@/modules/character'
import {
  $chatSessionId,
  $currentSessionTitle,
  $sessions,
  ChatPanel,
  isCompanionSession,
  switchSession,
  useIsReadOnlySession
} from '@/modules/conversation'
import { MediaViewerOverlay } from '@/modules/media'
import { useInteractiveRegion, useWindowMouseCapture } from '@/shared'
import { normalizeHashPath } from '@/shared/lib/hash-route'
import { ArrowLeft, Home, SlidersHorizontal, Terminal } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { WindowControls } from '@/shared/panel'
import { $gatewayState } from '@/shared/store/gateway'
import { requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import { RunRail } from './run-rail'
import { SessionSidebar } from './session-sidebar'
import { StationSettings } from './station-settings'
import { WorkbenchCompanion } from './workbench-companion'
import styles from './workbench.module.css'

function isStationSettingsHash(rawHash: string): boolean {
  const clean = normalizeHashPath(rawHash)

  return (
    clean.includes('settings') ||
    clean.startsWith('inference') ||
    clean.startsWith('runner') ||
    clean.startsWith('skills') ||
    clean.startsWith('station')
  )
}

export function WorkbenchRoot(): React.JSX.Element {
  useWindowMouseCapture(1, { setIgnoreMouseEvents: window.spiritagent?.surface?.setIgnoreMouseEvents })
  const shellRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('workbench-shell', shellRef, undefined, undefined, 1)

  const title = useStore($currentSessionTitle)
  const gatewayState = useStore($gatewayState)
  const currentSessionId = useStore($chatSessionId)
  const dict = useStrings()
  const t = dict.workbench
  const sessions = useStore($sessions)

  useEffect(() => {
    document.title = `${dict.brand.name} · ${t.title}`
  }, [dict.brand.name, t.title])
  const isReadOnlySession = useIsReadOnlySession()
  const activeSession = sessions.find(session => session.id === currentSessionId)
  const canShowChat = activeSession !== undefined && !isCompanionSession(activeSession)

  const scrollRef = useRef<HTMLDivElement>(null)

  const [settingsOpen, setSettingsOpen] = useState(() => {
    if (typeof window !== 'undefined' && window.location.hash) {
      return isStationSettingsHash(window.location.hash)
    }

    return false
  })

  const sessionFromUrlHandledRef = useRef(false)

  // 保证工作台处于有效工作会话下（不处于生活空间的「陪伴」会话下，且空会话时自动定位到开发工位）
  useEffect(() => {
    if (gatewayState !== 'open' || sessions.length === 0) {
      return
    }

    const current = sessions.find(s => s.id === currentSessionId)

    if (!current || isCompanionSession(current)) {
      const workTarget =
        sessions.find(s => s.system_preset_id === 'developer') ?? sessions.find(s => !isCompanionSession(s))

      if (workTarget && workTarget.id !== currentSessionId) {
        void switchSession(workTarget.id)
      }
    }
  }, [sessions, currentSessionId, gatewayState])

  useEffect(() => {
    if (gatewayState !== 'open' || sessionFromUrlHandledRef.current) {
      return
    }

    if (typeof window !== 'undefined' && window.location.search) {
      const params = new URLSearchParams(window.location.search)
      const sid = params.get('sessionId')

      if (sid) {
        sessionFromUrlHandledRef.current = true
        void switchSession(sid)
      }
    }
  }, [gatewayState])

  useEffect(() => {
    const onHash = (): void => {
      if (isStationSettingsHash(window.location.hash)) {
        setSettingsOpen(true)
      }
    }

    window.addEventListener('hashchange', onHash)

    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const clearSettingsHash = useCallback((): void => {
    if (typeof window !== 'undefined' && isStationSettingsHash(window.location.hash)) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
    }
  }, [])

  const toggleSettings = (): void => {
    setSettingsOpen(open => {
      if (open) {
        clearSettingsHash()
      }

      return !open
    })
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && settingsOpen) {
        clearSettingsHash()
        setSettingsOpen(false)
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [clearSettingsHash, settingsOpen])

  return (
    <div className={styles.windowContainer}>
      <aside className={styles.companionSlot}>
        <WorkbenchCompanion />
      </aside>

      <div className={styles.shell} data-surface="workbench" ref={shellRef}>
        <header
          className={styles.titlebar}
          onDoubleClick={() => {
            void window.spiritagent?.surface?.maximize?.()
          }}
        >
          <div className={styles.titleArea}>
            <Terminal className={styles.titleIcon} size={18} />
            <h1 className={styles.brandTitle}>{t.title}</h1>
            <SpriteStatusBadge />
            <div className={styles.sessionBadge} title={settingsOpen ? t.stationSettingsTooltip : title}>
              <span>{settingsOpen ? t.stationSettingsBadge : title || t.stationBadge}</span>
            </div>
          </div>

          <div className={styles.actionsArea}>
            <button
              className={cn(styles.glassButton, settingsOpen && styles.glassButtonActive)}
              onClick={toggleSettings}
              title={settingsOpen ? t.backToChatTooltip : t.stationSettingsTooltip}
              type="button"
            >
              {settingsOpen ? <ArrowLeft size={13} /> : <SlidersHorizontal size={13} />}
              <span>{settingsOpen ? t.backToChat : t.stationEnvironment}</span>
            </button>
            <button
              className={styles.glassButton}
              onClick={() => {
                void requestOpenSurface('living')
              }}
              title={t.openLivingTooltip}
              type="button"
            >
              <Home size={13} />
              <span>{t.openLiving}</span>
            </button>
            <WindowControls />
          </div>
        </header>

        {settingsOpen ? (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-transparent">
            <StationSettings />
          </div>
        ) : null}

        <div
          className={cn(styles.body, settingsOpen && styles.bodyHidden)}
          style={settingsOpen ? { display: 'none' } : undefined}
        >
          <div className={styles.sidebarArea}>
            <SessionSidebar />
          </div>

          <main className={styles.center}>
            {canShowChat && (
              <ChatPanel
                className="flex-1 min-h-0"
                gatewayState={gatewayState}
                inputWrapperClassName={styles.chatInputWrapper}
                isReadOnlySession={isReadOnlySession}
                scrollRef={scrollRef}
                surfaceClassName={styles.chatSurface}
                variant="workbench"
              />
            )}
          </main>

          <RunRail />
          <MediaViewerOverlay windowId={1} />
        </div>
      </div>
    </div>
  )
}
