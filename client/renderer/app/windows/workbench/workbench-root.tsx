// 工作台根组件：三栏（会话侧栏 256 / 对话 flex / Run Rail 320 可折到 0）+ 顶栏（会话名+工位环境+回生活空间）+ 全屏工位环境设置；工位环境打开时挂载全屏设置并隐藏底层会话区。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { PresentationModeButton } from '@/app/components/presentation-mode-switch'
import { CompanionMenu } from '@/app/components/surface-companion/companion-menu'
import { SurfaceCompanion } from '@/app/components/surface-companion/surface-companion'
import { RunRail } from '@/app/features/workbench/run-rail'
import { SessionSidebar } from '@/app/features/workbench/session-sidebar'
import { StationSettings } from '@/app/features/workbench/station-settings'
import styles from '@/app/features/workbench/workbench.module.css'
import { SpriteStatusBadge } from '@/modules/character'
import {
  $archivedSessions,
  $chatSessionId,
  $currentSessionTitle,
  $searchResults,
  $sessions,
  $sessionsLoading,
  ChatPanel,
  switchSession,
  useConversationView,
  useIsReadOnlySession
} from '@/modules/conversation'
import { MediaViewerOverlay } from '@/modules/media'
import { useInteractiveRegion, useWindowMouseCapture } from '@/shared'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { normalizeHashPath } from '@/shared/lib/hash-route'
import { ArrowLeft, Home, SlidersHorizontal, Terminal } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { EmptyState, LoadingBlock, WindowControls } from '@/shared/panel'
import { $gatewayState } from '@/shared/store/gateway'
import { requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

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
  const archivedSessions = useStore($archivedSessions)
  const searchResults = useStore($searchResults)
  const sessionsLoading = useStore($sessionsLoading)
  const { runtime } = useConversationView()
  const mountedPresetId = useStore(runtime.$chatSessionPresetId)

  useEffect(() => {
    document.title = `${dict.brand.name} · ${t.title}`
  }, [dict.brand.name, t.title])

  const isReadOnlySession = useIsReadOnlySession()

  const activeSession =
    sessions.find(session => session.id === currentSessionId) ??
    archivedSessions.find(session => session.id === currentSessionId) ??
    searchResults.find(session => session.id === currentSessionId)

  const presetId = mountedPresetId ?? activeSession?.system_preset_id
  const canShowChat = Boolean(currentSessionId && presetId && presetId !== 'companion')

  const scrollRef = useRef<HTMLDivElement>(null)

  const [settingsOpen, setSettingsOpen] = useState(() => isStationSettingsHash(window.location.hash))

  const sessionFromUrlHandledRef = useRef(false)

  useEffect(() => {
    if (gatewayState !== 'open' || sessionFromUrlHandledRef.current) {
      return
    }

    const sid = new URLSearchParams(window.location.search).get('sessionId')

    if (sid) {
      sessionFromUrlHandledRef.current = true
      void switchSession(sid)
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

  const closeSettings = (): void => {
    if (isStationSettingsHash(window.location.hash)) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
    }

    setSettingsOpen(false)
  }

  const toggleSettings = (): void => {
    if (settingsOpen) {
      closeSettings()
    } else {
      setSettingsOpen(true)
    }
  }

  useEscapeKey(closeSettings, { capture: false, enabled: settingsOpen, preventDefault: false, stopPropagation: false })

  return (
    <div className={styles.windowContainer}>
      <SurfaceCompanion surface="workbench" />

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
            <div
              className={styles.sessionBadge}
              title={settingsOpen ? t.stationSettingsTooltip : canShowChat ? title : t.stationBadge}
            >
              <span>{settingsOpen ? t.stationSettingsBadge : (canShowChat && title) || t.stationBadge}</span>
            </div>
          </div>

          <div className={styles.actionsArea}>
            <PresentationModeButton className={styles.glassButton} />
            <CompanionMenu surface="workbench" />
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

        <div className={cn(styles.body, settingsOpen && styles.bodyHidden)}>
          <div className={styles.sidebarArea}>
            <SessionSidebar />
          </div>

          <main className={styles.center}>
            {canShowChat ? (
              <ChatPanel
                className="flex-1 min-h-0"
                gatewayState={gatewayState}
                inputWrapperClassName={styles.chatInputWrapper}
                isReadOnlySession={isReadOnlySession}
                scrollRef={scrollRef}
                surfaceClassName={styles.chatSurface}
                variant="workbench"
              />
            ) : (
              <div className="flex flex-1 items-center justify-center">
                {sessionsLoading ? (
                  <LoadingBlock label={dict.common.loading} />
                ) : (
                  <EmptyState title={t.selectSession} />
                )}
              </div>
            )}
          </main>

          <RunRail />
          <MediaViewerOverlay containerRef={shellRef} windowId={1} />
        </div>
      </div>
    </div>
  )
}
