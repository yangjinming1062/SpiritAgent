import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { AppearancePage } from '@/app/features/living/appearance/appearance-page'
import { ChannelsPage } from '@/app/features/living/channels-page'
import { DiaryPage } from '@/app/features/living/diary-page'
import { PostsPage } from '@/app/features/living/posts-page'
import { SceneBackdrop } from '@/app/features/living/scene-backdrop'
import { ScenePage } from '@/app/features/living/scene-page'
import { useDesktopStage } from '@/app/workflows/desktop-stage'
import { $persona, $portraitUrl, hydratePersona, hydratePortrait, SpriteStatusBadge } from '@/modules/character'
import {
  ensureCompanionSession,
  pendingMessages,
  pushExternalAttachment,
  setChatSession,
  switchSession
} from '@/modules/conversation'
import { MediaViewerOverlay } from '@/modules/media'
import { hydrateDiaryUnread } from '@/modules/memory'
import { hydratePostsUnread } from '@/modules/posts'
import { $activeScene, hydrateScene } from '@/modules/scene'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { ArrowLeft, ArrowRight, ChevronDown, MessageCircle, Settings, Wifi, WifiOff, X } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $desktopChatActive } from '@/shared/store/chat-visibility'
import { $gateway, $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceScreenLocked } from '@/shared/store/surfaces'
import { $theme } from '@/shared/store/theme'
import { useStrings } from '@/shared/strings'

import { DesktopChat, DesktopWhisper } from './desktop-chat'
import { DesktopCompanion } from './desktop-companion'
import { DesktopDock } from './desktop-dock'
import { type DesktopApp, useDesktopLayout } from './desktop-layout'
import { DesktopPreferences } from './desktop-preferences'
import { DesktopAccounts, DesktopSettings } from './desktop-settings'
import { useDesktopStrings } from './desktop-strings'
import { DesktopWindow } from './desktop-window'
import styles from './desktop.module.css'

const MENU: DesktopApp[] = ['chat', 'posts', 'diary', 'scene', 'appearance', 'channels', 'settings']

// 顶栏是两个独立状态：绿点只说伙伴在不在忙，网络用图标说，免得两个同色圆点指代不清。
type ConnectionTone = 'offline' | 'online' | 'pending'

const CONNECTION_TONE: Record<ConnectionState, ConnectionTone> = {
  closed: 'offline',
  connecting: 'pending',
  error: 'offline',
  idle: 'pending',
  open: 'online'
}

export function DesktopRoot(): React.JSX.Element {
  const t = useDesktopStrings()
  const dict = useStrings()
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const gateway = useStore($gateway)
  const screenLocked = useStore($surfaceScreenLocked)
  const persona = useStore($persona)
  const portrait = useStore($portraitUrl)
  const locale = useStore($locale)
  const theme = useStore($theme)
  const scene = useStore($activeScene)
  const pending = useStore(pendingMessages.$atom)
  const [clock, setClock] = useState(() => new Date())
  const presentation = useStore($presentation)
  const [companionSessionId, setCompanionSessionId] = useState<string | null>(null)
  const [focusedConversation, setFocusedConversation] = useState<'main' | 'whisper' | null>('whisper')
  const [accountOpen, setAccountOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [dockOverlayOpen, setDockOverlayOpen] = useState(false)
  const [companionMenuOpen, setCompanionMenuOpen] = useState(false)
  const [area, setArea] = useState({ width: 1000, height: 700, left: 16, top: 60 })
  const initialized = useRef(false)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const settingsRef = useRef<HTMLDivElement>(null)
  const { layout, setLayout, activate, updateWindow, close } = useDesktopLayout()
  const desktopVisible = presentation?.effectiveMode === 'desktop' && presentation.status === 'active' && !screenLocked
  const foreground = desktopVisible && presentation.foreground
  const activeWindow = layout.windows.findLast(item => !item.minimized)?.id

  const panelsEnabled = foreground && !accountOpen && !settingsOpen && !dockOverlayOpen && !companionMenuOpen
  const activePanel = panelsEnabled && focusedConversation !== 'whisper' ? activeWindow : null
  const mainChatActive = activePanel === 'chat' && focusedConversation === 'main'
  const whisperChatActive = panelsEnabled && focusedConversation === 'whisper' && layout.whisperOpen
  const whisperHasUnread = pending.some(message => message.sessionId === companionSessionId)
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

      void window.spiritagent.presentation.focus(current.stageEpoch).catch(error => {
        console.warn('[desktop] pointer activation failed', error)
      })
    }

    window.addEventListener('pointerup', onPointerUp, true)

    return () => window.removeEventListener('pointerup', onPointerUp, true)
  }, [])

  const activatePanel = useCallback(
    (id: DesktopApp): void => {
      activate(id)
      setFocusedConversation(id === 'chat' ? 'main' : null)
    },
    [activate]
  )

  const openWhisper = useCallback((): void => {
    setLayout(current => (current.whisperOpen ? current : { ...current, whisperOpen: true }))
    setFocusedConversation('whisper')
  }, [setLayout])

  useEffect(() => {
    $desktopChatActive.set(mainChatActive || whisperChatActive)
  }, [mainChatActive, whisperChatActive])

  useEffect(() => () => $desktopChatActive.set(false), [])

  useDesktopStage({
    enabled: auth.kind === 'authenticated',
    visible: desktopVisible && layout.spriteVisible,
    insets: {
      top: 48,
      bottom: 96,
      left: layout.whisperOpen && layout.whisperSide === 'left' ? 396 : 16,
      right: layout.whisperOpen && layout.whisperSide === 'right' ? 396 : 16
    }
  })

  useEffect(
    () =>
      window.spiritagent.desktop.onNavigate(payload => {
        const view = payload.view?.split('/')[0]

        // 工位与设置深链统一进设置窗口，具体分区由 DesktopPreferences 从 hash 解析。
        const target: DesktopApp =
          view !== undefined && ['inference', 'runner', 'skills', 'station'].includes(view)
            ? 'settings'
            : view && (MENU as readonly string[]).includes(view)
              ? (view as DesktopApp)
              : 'chat'

        activatePanel(target)

        if (payload.sessionId) {
          void switchSession(payload.sessionId)
        }

        if (payload.view) {
          window.location.hash = `#/${payload.view}`
        }
      }),
    [activatePanel]
  )

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
    if (!foreground) {
      setAccountOpen(false)
      setSettingsOpen(false)
    }
  }, [foreground])

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
    if (gatewayState !== 'open') {
      return
    }

    let disposed = false
    void ensureCompanionSession()
      .then(id => {
        if (disposed || !id) {
          return
        }

        setCompanionSessionId(id)

        if (!initialized.current) {
          initialized.current = true
          const target = new URLSearchParams(window.location.search).get('sessionId')

          if (target) {
            void switchSession(target)
          } else {
            setChatSession(id)
          }
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
  }, [gateway, gatewayState, t.chat])

  useEffect(() => {
    if (!companionSessionId || gatewayState !== 'open') {
      return
    }

    let disposed = false

    const drain = (): void => {
      void window.spiritagent.chat
        .takePendingFeed()
        .then(paths => {
          if (!disposed && paths.length) {
            pushExternalAttachment(paths)
            openWhisper()
          }
        })
        .catch(error => {
          if (!disposed) {
            notifyError(error, t.chat)
          }
        })
    }

    const off = window.spiritagent.chat.onPendingFeed(drain)
    // StrictMode 的首次清理不得提前取走一次性信箱内容。
    queueMicrotask(() => {
      if (!disposed) {
        drain()
      }
    })

    return () => {
      disposed = true
      off()
    }
  }, [companionSessionId, gatewayState, openWhisper, t.chat])

  useEffect(() => {
    let disposed = false
    const image = scene?.url ?? null

    const send = async (): Promise<void> => {
      const resolved = image && !image.startsWith('data:') ? await window.spiritagent.apiAsset({ url: image }) : image

      if (image && !resolved) {
        throw new Error('Desktop background asset is unavailable')
      }

      if (!disposed) {
        await window.spiritagent.presentation.setBackground({
          image: resolved,
          theme,
          reduceMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        })
      }
    }

    void send().catch(error => log.warn('desktop', 'background publish failed', error))

    return () => {
      disposed = true
    }
  }, [scene?.url, theme])

  useEffect(() => {
    const onPointer = (event: PointerEvent): void => {
      if (event.target instanceof Node) {
        if (!menuRef.current?.contains(event.target)) {
          setAccountOpen(false)
        }

        if (!settingsRef.current?.contains(event.target)) {
          setSettingsOpen(false)
        }
      }
    }

    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        setAccountOpen(false)
        setSettingsOpen(false)
      }
    }

    window.addEventListener('pointerdown', onPointer)
    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('pointerdown', onPointer)
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  const toggleWhisper = (): void => {
    if (layout.whisperOpen) {
      setLayout(current => ({ ...current, whisperOpen: false }))
      setFocusedConversation(activeWindow === 'chat' ? 'main' : null)
    } else {
      openWhisper()
    }
  }

  const toggleSprite = (): void => setLayout(current => ({ ...current, spriteVisible: !current.spriteVisible }))

  const page = (id: DesktopApp, visible: boolean): React.ReactNode => {
    const reading = visible && activePanel === id

    switch (id) {
      case 'chat':
        return (
          <DesktopChat
            active={mainChatActive}
            companionSessionId={companionSessionId}
            containerRef={rootRef}
            foreground={foreground}
            visible={visible && desktopVisible}
          />
        )

      case 'posts':
        return (
          <>
            <PostsPage foreground={reading} mediaViewId="desktop-posts" />
            <MediaViewerOverlay containerRef={rootRef} viewId="desktop-posts" windowId={1} />
          </>
        )

      case 'diary':
        return <DiaryPage foreground={reading} />

      case 'scene':
        return <ScenePage />

      case 'appearance':
        return <AppearancePage />

      case 'channels':
        return <ChannelsPage />

      case 'settings':
        return <DesktopPreferences />
    }
  }

  if (auth.kind !== 'authenticated') {
    return (
      <div className={styles.waiting}>
        <span>{t.waiting}</span>
        <button
          onClick={() =>
            void window.spiritagent.presentation.setMode('window').catch(error => notifyError(error, t.title))
          }
          type="button"
        >
          {t.windowMode}
        </button>
      </div>
    )
  }

  return (
    <div className={styles.desktop} data-whisper={layout.whisperOpen ? layout.whisperSide : 'closed'} ref={rootRef}>
      <SceneBackdrop desktop />
      <div className={styles.backgroundTint} />
      <header className={styles.topbar}>
        <div className={styles.avatarMenu} ref={menuRef}>
          <button
            aria-expanded={accountOpen}
            aria-label={t.accounts}
            className={styles.avatarButton}
            onClick={() => setAccountOpen(value => !value)}
            title={persona?.name || dict.brand.name}
            type="button"
          >
            {portrait ? <img alt="" src={portrait} /> : <span>{dict.brand.name.slice(0, 1)}</span>}
          </button>
          {accountOpen && <DesktopAccounts />}
        </div>
        <nav aria-label={t.title} className={styles.mainMenu}>
          {MENU.map(id => (
            <button
              data-current={activeWindow === id}
              key={id}
              onClick={() => activatePanel(id)}
              type="button"
            >
              {t[id]}
            </button>
          ))}
        </nav>
        <div className={styles.status}>
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
            onClick={() => setSettingsOpen(value => !value)}
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
              onSpriteToggle={toggleSprite}
              presentation={presentation}
              spriteVisible={layout.spriteVisible}
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
      {layout.spriteVisible && desktopVisible && (
        <DesktopCompanion
          menuEnabled={foreground && !accountOpen && !settingsOpen && !dockOverlayOpen}
          onHide={toggleSprite}
          onMenuOpenChange={setCompanionMenuOpen}
          onOpenWhisper={openWhisper}
          onToggleWhisper={toggleWhisper}
        />
      )}
      <aside
        aria-label={t.whisper}
        className={styles.whisper}
        data-side={layout.whisperSide}
        hidden={!layout.whisperOpen}
        onFocusCapture={() => setFocusedConversation('whisper')}
        onPointerDownCapture={() => setFocusedConversation('whisper')}
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
          title={t.showWhisper}
          type="button"
        >
          <MessageCircle size={17} />
          <span>{t.whisper}</span>
          {whisperHasUnread && <span aria-hidden="true" className={styles.whisperUnread} />}
        </button>
      )}
      <DesktopDock
        menuEnabled={foreground && !accountOpen && !settingsOpen && !companionMenuOpen}
        onActivate={activatePanel}
        onMenuOpenChange={setDockOverlayOpen}
        windows={layout.windows}
      />
    </div>
  )
}
