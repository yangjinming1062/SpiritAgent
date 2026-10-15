import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { AccountMenu } from '@/app/features/accounts/account-menu'
import { CompanionHub } from '@/app/features/companion/companion-page'
import { ConversationPage } from '@/app/features/conversation/conversation-page'
import { useDesktopLifeStrings } from '@/app/features/desktop-life/desktop-life-strings'
import { LifePage } from '@/app/features/posts/life-page'
import { AppSettings } from '@/app/features/settings/app-settings'
import { $appPage, applyAppNavigation, setAppPage } from '@/app/navigation/app-navigation'
import { $persona, $portraitUrl, hydratePersona, hydratePortrait, SpriteStatusBadge } from '@/modules/character'
import {
  $chatSessionId,
  $companionSessionId,
  ensureCompanionSession,
  pendingMessages,
  setChatSession,
  switchSession,
  useConversationUnread
} from '@/modules/conversation'
import { $desktopVideoError, $desktopVideoState } from '@/modules/desktop-videos'
import { $diaryHasUnread, hydrateDiaryUnread } from '@/modules/diary'
import { $postsHasUnread, hydratePostsUnread } from '@/modules/posts'
import { hydrateScene } from '@/modules/scene'
import { useDismissOnOutside } from '@/shared/hooks/use-dismiss-on-outside'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { ArrowLeft, ArrowRight, ChevronDown, MessageCircle, Settings, Wifi, WifiOff, X } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $desktopChatActive } from '@/shared/store/chat-visibility'
import { $gateway, $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceScreenLocked } from '@/shared/store/surfaces'
import { $uiStateReady } from '@/shared/store/ui-state'
import { useStrings } from '@/shared/strings'

import { DesktopWhisper } from './desktop-chat'
import { DesktopDock } from './desktop-dock'
import { DESKTOP_APPS, type DesktopApp, useDesktopLayout } from './desktop-layout'
import { DesktopSettings } from './desktop-settings'
import { useDesktopStrings } from './desktop-strings'
import { useDesktopVideoWallpaper } from './desktop-video-wallpaper'
import { DesktopWindow } from './desktop-window'
import styles from './desktop.module.css'

// 顶栏是两个独立状态：绿点只说伙伴在不在忙，网络用图标说，免得两个同色圆点指代不清。
type ConnectionTone = 'offline' | 'online' | 'pending'

const CONNECTION_TONE: Record<ConnectionState, ConnectionTone> = {
  closed: 'offline',
  connecting: 'pending',
  error: 'offline',
  idle: 'pending',
  open: 'online'
}

type DesktopShortcut = 'desktopLife'

export function DesktopRoot(): React.JSX.Element {
  useDesktopVideoWallpaper()
  const t = useDesktopStrings()
  const desktopLife = useDesktopLifeStrings()
  const dict = useStrings()
  const auth = useStore($auth)
  const uiStateReady = useStore($uiStateReady)
  const appPage = useStore($appPage)
  const gatewayState = useStore($gatewayState)
  const gateway = useStore($gateway)
  const screenLocked = useStore($surfaceScreenLocked)
  const persona = useStore($persona)
  const portrait = useStore($portraitUrl)
  const locale = useStore($locale)
  const companionSessionId = useStore($companionSessionId)
  const { hasUnread: whisperHasUnread, firstUnheard } = useConversationUnread(companionSessionId)
  const pending = useStore(pendingMessages.$atom)
  const postsUnread = useStore($postsHasUnread)
  const diaryUnread = useStore($diaryHasUnread)
  const [unreadReveal, setUnreadReveal] = useState<{ key: string; request: number }>()
  const revealRequest = useRef(0)
  const [clock, setClock] = useState(() => new Date())
  const [windowFocused, setWindowFocused] = useState(() => document.hasFocus())
  const presentation = useStore($presentation)
  const videoState = useStore($desktopVideoState)
  const videoError = useStore($desktopVideoError)
  const idleVideo = videoState?.current?.actions.find(action => action.key === 'idle')

  const wallpaperStatus =
    videoState?.preparation_error ||
    idleVideo?.error ||
    videoError ||
    (idleVideo?.status === 'processing'
      ? idleVideo.stage === 'paused'
        ? desktopLife.paused
        : desktopLife.statuses.processing
      : !idleVideo?.video_url
        ? desktopLife.noPreview
        : null)

  const [focusedConversation, setFocusedConversation] = useState<'main' | 'whisper' | null>('whisper')
  const [accountOpen, setAccountOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [dockOverlayOpen, setDockOverlayOpen] = useState(false)
  const [area, setArea] = useState({ width: 1000, height: 700, left: 16, top: 44 })
  const initialized = useRef(false)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const settingsRef = useRef<HTMLDivElement>(null)
  const topbarRef = useRef<HTMLElement>(null)
  const whisperRef = useRef<HTMLElement>(null)
  const whisperTabRef = useRef<HTMLButtonElement>(null)
  useInteractiveRegion('desktop-topbar', topbarRef)
  useInteractiveRegion('desktop-avatar', menuRef)
  useInteractiveRegion('desktop-account-menu', menuRef)
  useInteractiveRegion('desktop-settings-menu', settingsRef)
  useInteractiveRegion('desktop-whisper', whisperRef, element =>
    element.hidden ? null : element.getBoundingClientRect()
  )
  useInteractiveRegion('desktop-whisper-tab', whisperTabRef)
  const { layout, setLayout, activate, updateWindow, close } = useDesktopLayout()

  const desktopVisible =
    presentation?.effectiveMode === 'desktop' &&
    presentation.status === 'active' &&
    !presentation.fullscreen &&
    !screenLocked

  const foreground = desktopVisible && presentation.foreground && windowFocused
  useEffect(() => {
    const update = (): void => setWindowFocused(document.hasFocus())
    window.addEventListener('focus', update)
    window.addEventListener('blur', update)

    return () => {
      window.removeEventListener('focus', update)
      window.removeEventListener('blur', update)
    }
  }, [])
  const activeWindow = layout.windows.findLast(item => !item.minimized)?.id

  // 轻语收起时遗留的 whisper 焦点按当前布局归一，覆盖持久化收起启动与互斥收起路径。
  const focused =
    layout.whisperOpen || focusedConversation !== 'whisper'
      ? focusedConversation
      : activeWindow === 'chat'
        ? 'main'
        : null

  const panelsEnabled =
    foreground && uiStateReady && auth.kind === 'authenticated' && !accountOpen && !settingsOpen && !dockOverlayOpen

  const activePanel = panelsEnabled && focused !== 'whisper' ? activeWindow : null
  const mainChatActive = activePanel === 'chat' && focused === 'main'
  const whisperChatActive = panelsEnabled && focused === 'whisper' && layout.whisperOpen
  const connectionTone = CONNECTION_TONE[gatewayState]
  const connectionLabel = { offline: t.disconnected, online: t.connected, pending: t.loading }[connectionTone]

  useEffect(() => {
    const onPointerUp = (event: PointerEvent): void => {
      if (
        !event.isTrusted ||
        !event.isPrimary ||
        event.button !== 0 ||
        event.buttons !== 0 ||
        event.altKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.metaKey ||
        !navigator.userActivation.isActive
      ) {
        return
      }

      const current = $presentation.get()

      if (current?.effectiveMode !== 'desktop' || current.status !== 'active' || $surfaceScreenLocked.get()) {
        return
      }

      void shellApi()
        .presentation.focus(current.stageEpoch)
        .catch(error => {
          console.warn('[desktop] pointer activation failed', error)
        })
    }

    window.addEventListener('pointerup', onPointerUp, true)

    return () => window.removeEventListener('pointerup', onPointerUp, true)
  }, [])

  const activatePanel = useCallback(
    (id: DesktopApp | DesktopShortcut): void => {
      const page = id === 'desktopLife' ? 'companion' : id

      if (id === 'desktopLife') {
        void applyAppNavigation({ page: 'companion', companionSection: 'desktop-life' })
      } else {
        setAppPage(page)
      }

      activate(page)
      setFocusedConversation(page === 'chat' ? 'main' : null)
    },
    [activate]
  )

  const openCompanionChat = useCallback((): void => {
    setSettingsOpen(false)
    setAccountOpen(false)
    activatePanel('chat')

    if (companionSessionId) {
      setChatSession(companionSessionId)
      setUnreadReveal(firstUnheard ? { key: firstUnheard, request: ++revealRequest.current } : undefined)
      void switchSession(companionSessionId)
    } else {
      setUnreadReveal(undefined)
    }

    setLayout(current => (current.whisperOpen ? { ...current, whisperOpen: false } : current))
  }, [activatePanel, companionSessionId, firstUnheard, setLayout])

  const openAccountMenu = (): void => {
    setSettingsOpen(false)
    setAccountOpen(true)
  }

  const openWhisper = useCallback((): void => {
    setLayout(current => (current.whisperOpen ? current : { ...current, whisperOpen: true }))
    setFocusedConversation('whisper')
  }, [setLayout])

  useEffect(() => {
    $desktopChatActive.set(mainChatActive || whisperChatActive)
  }, [mainChatActive, whisperChatActive])

  useEffect(() => () => $desktopChatActive.set(false), [])

  useEffect(
    () =>
      shellApi().desktop.onNavigate(payload => {
        void applyAppNavigation(payload)
          .then(() => {
            activatePanel($appPage.get())

            if (payload.messageKey) {
              setUnreadReveal({ key: payload.messageKey, request: ++revealRequest.current })
            }
          })
          .catch(error => notifyError(error, t.chat))
      }),
    [activatePanel, t.chat]
  )

  useEffect(() => {
    if (uiStateReady) {
      activatePanel(appPage)
    }
  }, [uiStateReady, appPage, activatePanel])

  useEffect(() => {
    document.title = `${dict.brand.name} · ${t.title}`
  }, [dict.brand.name, t.title])

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 30000)

    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    const element = workspaceRef.current

    if (!element) {
      return
    }

    const update = (): void => {
      const rect = element.getBoundingClientRect()
      setArea({ width: element.clientWidth, height: element.clientHeight, left: rect.left, top: rect.top })
    }

    const observer = new ResizeObserver(update)
    observer.observe(element)
    update()

    return () => observer.disconnect()
  }, [auth.kind, layout.whisperOpen, layout.whisperSide])

  useEffect(() => {
    const closeMenus = (): void => {
      setAccountOpen(false)
      setSettingsOpen(false)
    }

    window.addEventListener('blur', closeMenus)

    if (!desktopVisible) {
      closeMenus()
    }

    return () => window.removeEventListener('blur', closeMenus)
  }, [desktopVisible])

  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      return
    }

    void hydratePersona()
    void hydratePortrait()
    void hydrateScene()
    void hydratePostsUnread()
    void hydrateDiaryUnread()
  }, [auth.kind, gatewayState])

  useEffect(() => {
    if (gatewayState !== 'open' || !uiStateReady) {
      return
    }

    let disposed = false
    void ensureCompanionSession()
      .then(id => {
        if (disposed || !id) {
          return
        }

        if (!initialized.current) {
          initialized.current = true
          const target = new URLSearchParams(window.location.search).get('sessionId')

          const selected = target || $chatSessionId.get() || id
          setChatSession(selected)
          void switchSession(selected)
        }
      })
      .catch(error => {
        if (!disposed) {
          notifyError(error, t.chat)
        }
      })

    return () => {
      disposed = true
    }
  }, [gateway, gatewayState, uiStateReady, t.chat])

  useDismissOnOutside(menuRef, accountOpen, () => setAccountOpen(false))
  useDismissOnOutside(settingsRef, settingsOpen, () => setSettingsOpen(false))

  const toggleWhisper = (): void => {
    if (layout.whisperOpen) {
      setLayout(current => ({ ...current, whisperOpen: false }))
      setFocusedConversation(activeWindow === 'chat' ? 'main' : null)
    } else {
      openWhisper()
    }
  }

  const page = (id: DesktopApp, visible: boolean): React.ReactNode => {
    const reading = visible && activePanel === id

    switch (id) {
      case 'chat':
        return (
          <ConversationPage
            active={mainChatActive}
            companionSessionId={companionSessionId}
            companionUnread={whisperHasUnread}
            containerRef={rootRef}
            foreground={foreground}
            receiveExternalAttachments={visible && desktopVisible}
            reveal={unreadReveal}
            viewId="desktop-main"
            visible={visible && desktopVisible}
            windowId={1}
          />
        )

      case 'posts':
        return <LifePage containerRef={rootRef} foreground={reading} mediaViewId="desktop-posts" />

      case 'companion':
        return <CompanionHub />

      case 'settings':
        return <AppSettings />
    }
  }

  if (auth.kind !== 'authenticated') {
    return (
      <div className={styles.waiting}>
        <span>{t.waiting}</span>
        <button
          onClick={() =>
            void shellApi()
              .presentation.setMode('window')
              .catch(error => notifyError(error, t.title))
          }
          type="button"
        >
          {t.windowMode}
        </button>
      </div>
    )
  }

  return (
    <div
      className={styles.desktop}
      data-whisper={layout.whisperOpen ? layout.whisperSide : 'closed'}
      inert={['starting', 'recovering'].includes(presentation.status)}
      ref={rootRef}
    >
      <div className={`${styles.avatarMenu} ${styles.avatarDock}`} ref={menuRef}>
        <button
          aria-expanded={accountOpen}
          aria-label={whisperHasUnread ? t.openUnread : t.chat}
          className={styles.avatarButton}
          data-unread={whisperHasUnread}
          onClick={openCompanionChat}
          onContextMenu={event => {
            event.preventDefault()
            openAccountMenu()
          }}
          onKeyDown={event => {
            if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
              event.preventDefault()
              openAccountMenu()
            }
          }}
          title={whisperHasUnread ? t.avatarUnreadHint : t.avatarChatHint}
          type="button"
        >
          {portrait ? <img alt="" src={portrait} /> : <span>{dict.brand.name.slice(0, 1)}</span>}
          {whisperHasUnread && <span aria-hidden="true" className={styles.avatarUnread} />}
        </button>
        {accountOpen && <AccountMenu onClose={() => setAccountOpen(false)} />}
      </div>
      <header className={styles.topbar} ref={topbarRef}>
        <div aria-hidden="true" className={styles.avatarSlot} />
        <nav aria-label={t.title} className={styles.mainMenu}>
          {DESKTOP_APPS.map(id => {
            const unread =
              id === 'posts'
                ? postsUnread || diaryUnread
                : id === 'chat'
                  ? pending.length > 0 || whisperHasUnread
                  : false

            return (
              <button
                aria-label={unread ? `${t[id]} (${dict.living.rail.unread})` : t[id]}
                data-current={activeWindow === id}
                key={id}
                onClick={() => activatePanel(id)}
                type="button"
              >
                {t[id]}
                {unread && <span aria-hidden="true"> ·</span>}
              </button>
            )
          })}
        </nav>
        <div className={styles.status}>
          {wallpaperStatus && (
            <button
              className={styles.wallpaperStatus}
              onClick={() => activatePanel('desktopLife')}
              title={wallpaperStatus}
              type="button"
            >
              <span role="status">{wallpaperStatus}</span>
            </button>
          )}
          <SpriteStatusBadge />
          <span
            aria-label={connectionLabel}
            className={styles.connection}
            data-tone={connectionTone}
            role="img"
            title={connectionLabel}
          >
            {connectionTone === 'offline' ? <WifiOff size={14} /> : <Wifi size={14} />}
          </span>
        </div>
        <div className={styles.settingsMenu} ref={settingsRef}>
          <button
            aria-expanded={settingsOpen}
            aria-label={t.desktopSettings}
            className={styles.topIcon}
            onClick={() => {
              setAccountOpen(false)
              setSettingsOpen(value => !value)
            }}
            type="button"
          >
            <Settings size={17} />
            <ChevronDown size={11} />
          </button>
          {settingsOpen && (
            <DesktopSettings
              onSettings={() => {
                activatePanel('settings')
                setSettingsOpen(false)
              }}
            />
          )}
        </div>
        <time className={styles.clock} dateTime={clock.toISOString()}>
          {clock.toLocaleString(locale === 'en' ? 'en-US' : 'zh-CN', {
            month: 'short',
            day: 'numeric',
            weekday: 'short',
            hour: '2-digit',
            minute: '2-digit'
          })}
        </time>
      </header>
      <main className={styles.workspace} ref={workspaceRef}>
        {layout.windows.map((item, index) => (
          <DesktopWindow
            active={activePanel === item.id}
            area={area}
            index={index}
            item={item}
            key={item.id}
            onActivate={() => activatePanel(item.id)}
            onChange={patch => updateWindow(item.id, patch)}
            onClose={() => close(item.id)}
            title={t[item.id]}
          >
            {page(item.id, !item.minimized)}
          </DesktopWindow>
        ))}
      </main>
      <aside
        aria-label={t.whisper}
        className={styles.whisper}
        data-side={layout.whisperSide}
        hidden={!layout.whisperOpen}
        onFocusCapture={() => setFocusedConversation('whisper')}
        onPointerDownCapture={() => setFocusedConversation('whisper')}
        ref={whisperRef}
      >
        <header className={styles.whisperHeader}>
          <strong>{t.whisper}</strong>
          <span>{persona?.name || dict.brand.name}</span>
          <button
            aria-label={layout.whisperSide === 'right' ? t.leftWhisper : t.rightWhisper}
            onClick={() =>
              setLayout(current => ({ ...current, whisperSide: current.whisperSide === 'right' ? 'left' : 'right' }))
            }
            title={layout.whisperSide === 'right' ? t.leftWhisper : t.rightWhisper}
            type="button"
          >
            {layout.whisperSide === 'right' ? <ArrowLeft size={14} /> : <ArrowRight size={14} />}
          </button>
          <button aria-label={t.hideWhisper} onClick={toggleWhisper} title={t.hideWhisper} type="button">
            <X size={15} />
          </button>
        </header>
        <DesktopWhisper
          active={whisperChatActive}
          containerRef={rootRef}
          foreground={foreground}
          sessionId={companionSessionId}
          visible={layout.whisperOpen && desktopVisible}
        />
      </aside>
      {!layout.whisperOpen && (
        <button
          aria-label={whisperHasUnread ? `${t.showWhisper} (${dict.living.rail.unread})` : t.showWhisper}
          className={styles.whisperTab}
          data-side={layout.whisperSide}
          onClick={toggleWhisper}
          ref={whisperTabRef}
          title={t.showWhisper}
          type="button"
        >
          <MessageCircle size={17} />
          <span>{t.whisper}</span>
          {whisperHasUnread && <span aria-hidden="true" className={styles.whisperUnread} />}
        </button>
      )}
      <DesktopDock
        menuEnabled={desktopVisible && !accountOpen && !settingsOpen}
        onActivate={activatePanel}
        onClose={close}
        onMenuOpenChange={setDockOverlayOpen}
        windows={layout.windows}
      />
    </div>
  )
}
