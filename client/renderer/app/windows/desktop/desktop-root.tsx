import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { AppContentPage } from '@/app/components/app-content-page'
import { AccountMenu } from '@/app/features/accounts/account-menu'
import { ConversationPage } from '@/app/features/conversation/conversation-page'
import { $appNavigationRevision, $appPage, applyAppNavigation, setAppPage } from '@/app/navigation/app-navigation'
import { $portraitUrl, hydratePersona, hydratePortrait } from '@/modules/character'
import {
  $chatSessionId,
  $companionSessionId,
  ensureCompanionSession,
  setChatSession,
  switchSession,
  useConversationUnread
} from '@/modules/conversation'
import { $desktopVideoError, $desktopVideoState } from '@/modules/desktop-videos'
import { $diaryHasUnread, hydrateDiaryUnread } from '@/modules/diary'
import { $postsHasUnread, hydratePostsUnread } from '@/modules/posts'
import { hydrateScene } from '@/modules/scene'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useDismissOnOutside } from '@/shared/hooks/use-dismiss-on-outside'
import { isPointInteractive, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { desktopPlatform, shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $desktopChatActive } from '@/shared/store/chat-visibility'
import { $gateway, $gatewayState } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceScreenLocked } from '@/shared/store/surfaces'
import { $uiStateReady } from '@/shared/store/ui-state'
import { useStrings } from '@/shared/strings'
import { DESKTOP_LAYOUT, getDesktopRailLayout } from '@client-shared/desktop-layout'

import { DesktopAvatarSignal } from './desktop-avatar-signal'
import { DesktopBottomBar } from './desktop-bottom-bar'
import { DesktopCompanionBubble } from './desktop-companion-bubble'
import { DesktopControls } from './desktop-controls'
import { type DesktopApp, useDesktopLayout } from './desktop-layout'
import { DesktopProductNav } from './desktop-product-nav'
import { useDesktopVideoWallpaper } from './desktop-video-wallpaper'
import { DesktopWindow } from './desktop-window'
import styles from './desktop.module.css'

type DesktopShortcut = 'desktopLife'

export function DesktopRoot(): React.JSX.Element {
  useDesktopVideoWallpaper()
  const strings = useStrings()
  const t = strings.desktopShell
  const desktopLife = strings.desktopLife
  const auth = useStore($auth)
  const uiStateReady = useStore($uiStateReady)
  const appPage = useStore($appPage)
  const navigationRevision = useStore($appNavigationRevision)
  const gatewayState = useStore($gatewayState)
  const gateway = useStore($gateway)
  const screenLocked = useStore($surfaceScreenLocked)
  const portrait = useStore($portraitUrl)
  const companionSessionId = useStore($companionSessionId)
  const { hasUnread: companionUnread, firstUnheard } = useConversationUnread(companionSessionId)
  const postsUnread = useStore($postsHasUnread)
  const diaryUnread = useStore($diaryHasUnread)
  const [unreadReveal, setUnreadReveal] = useState<{ key: string; request: number }>()
  const beginAsync = useAsyncGuard()
  const revealRequest = useRef(0)
  const [windowFocused, setWindowFocused] = useState(() => document.hasFocus())
  const presentation = useStore($presentation)
  const isMac = desktopPlatform() === 'darwin'
  const systemWorkArea = presentation.workArea

  const workArea =
    isMac && systemWorkArea
      ? { ...systemWorkArea, height: Math.max(0, systemWorkArea.height - DESKTOP_LAYOUT.bottomInset) }
      : systemWorkArea

  const [viewport, setViewport] = useState(() => ({ width: window.innerWidth, height: window.innerHeight }))
  const safeLeft = isMac ? (systemWorkArea?.x ?? 0) : 0
  const safeRight = isMac && systemWorkArea ? Math.max(0, viewport.width - systemWorkArea.x - systemWorkArea.width) : 0

  const safeBottom =
    isMac && systemWorkArea ? Math.max(0, viewport.height - systemWorkArea.y - systemWorkArea.height) : 0

  const rail = getDesktopRailLayout(Math.max(0, viewport.width - 2 * Math.max(safeLeft, safeRight)))
  const popupBottom = safeBottom + DESKTOP_LAYOUT.bottomInset

  const videoState = useStore($desktopVideoState)
  const videoError = useStore($desktopVideoError)
  const idleVideo = videoState?.current?.actions.find(action => action.key === 'idle')

  const wallpaperStatus =
    videoState?.preparation_error ||
    idleVideo?.error ||
    videoError ||
    (!videoState?.current
      ? desktopLife.chooseBackground
      : idleVideo?.status === 'processing'
        ? idleVideo.stage === 'paused'
          ? desktopLife.paused
          : strings.common.actionVariants.statuses.processing
        : !idleVideo?.video_url
          ? desktopLife.noPreview
          : null)

  const [focusedConversation, setFocusedConversation] = useState<'main' | null>(null)
  const [overlay, setOverlay] = useState<'account' | 'controls' | 'dock' | 'product' | null>(null)
  const accountOpen = overlay === 'account'

  const [area, setArea] = useState<{ width: number; height: number; left: number; top: number }>({
    width: 1000,
    height: 700,
    left: DESKTOP_LAYOUT.contentInset,
    top: DESKTOP_LAYOUT.contentInset
  })

  const initialized = useRef(false)
  const handledNavigationRevision = useRef(0)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const avatarRef = useRef<HTMLButtonElement>(null)
  useInteractiveRegion('desktop-avatar', menuRef)
  const { layout, activate, updateWindow, close } = useDesktopLayout()

  const desktopVisible =
    presentation?.effectiveMode === 'desktop' &&
    presentation.status === 'active' &&
    !presentation.fullscreen &&
    !screenLocked

  const foreground = desktopVisible && presentation.foreground && windowFocused
  useEffect(() => {
    const resize = (): void => setViewport({ width: window.innerWidth, height: window.innerHeight })
    window.addEventListener('resize', resize)

    return () => window.removeEventListener('resize', resize)
  }, [])

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
  const chatWindow = layout.windows.find(item => item.id === 'chat')

  const avatarLabel = [
    companionUnread ? t.openUnread : t.chat,
    chatWindow && t.running,
    chatWindow?.minimized && t.minimizedWindow
  ]
    .filter(Boolean)
    .join(' · ')

  // 内部窗口的活动承载以用户最后激活的面板为准，最小化上层窗口不误激活下层对话。
  const focused = focusedConversation

  const panelsEnabled = foreground && uiStateReady && auth.kind === 'authenticated' && overlay === null

  const activePanel = panelsEnabled ? activeWindow : null
  const mainChatActive = activePanel === 'chat' && focused === 'main'

  const dockOpenChange = useCallback((open: boolean): void => {
    setOverlay(current => (open ? 'dock' : current === 'dock' ? null : current))
  }, [])

  const productOpenChange = useCallback((open: boolean): void => {
    setOverlay(current => (open ? 'product' : current === 'product' ? null : current))
  }, [])

  const controlsOpenChange = useCallback((open: boolean): void => {
    setOverlay(current => (open ? 'controls' : current === 'controls' ? null : current))
  }, [])

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
        !isPointInteractive(event.clientX, event.clientY, 1) ||
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

      setOverlay(null)
      activate(page)
      setFocusedConversation(page === 'chat' ? 'main' : null)
    },
    [activate]
  )

  const openCompanionChat = useCallback((): void => {
    setOverlay(null)
    activatePanel('chat')

    if (companionSessionId) {
      setChatSession(companionSessionId)
      setUnreadReveal(firstUnheard ? { key: firstUnheard, request: ++revealRequest.current } : undefined)
      void switchSession(companionSessionId)
    } else {
      setUnreadReveal(undefined)
    }
  }, [activatePanel, companionSessionId, firstUnheard])

  const openAccountMenu = (): void => {
    setOverlay('account')
  }

  useEffect(() => {
    $desktopChatActive.set(mainChatActive)
  }, [mainChatActive])

  useEffect(() => () => $desktopChatActive.set(false), [])

  useEffect(
    () =>
      shellApi().desktop.onNavigate(payload => {
        const isLive = beginAsync()
        void applyAppNavigation(payload)
          .then(() => {
            if (isLive() && payload.messageKey) {
              setUnreadReveal({ key: payload.messageKey, request: ++revealRequest.current })
            }
          })
          .catch(error => {
            if (isLive()) {
              notifyError(error, t.chat)
            }
          })
      }),
    [beginAsync, t.chat]
  )

  useEffect(() => {
    if (!uiStateReady || navigationRevision <= handledNavigationRevision.current) {
      return
    }

    // 持久页面只记录选择；显式导航与模式交接才激活对应窗口。
    handledNavigationRevision.current = navigationRevision
    activate(appPage)
    setOverlay(null)
    setFocusedConversation(appPage === 'chat' ? 'main' : null)
  }, [uiStateReady, navigationRevision, appPage, activate])

  useEffect(() => {
    document.title = `${strings.brand.name} · ${t.title}`
  }, [strings.brand.name, t.title])

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
  }, [auth.kind, workArea?.x, workArea?.y, workArea?.width, workArea?.height])

  useEffect(() => {
    const closeMenus = (): void => {
      // 程序目录自行处理原生文件选择期间的失焦。
      setOverlay(current => (current === 'dock' ? current : null))
    }

    window.addEventListener('blur', closeMenus)

    if (!desktopVisible) {
      setOverlay(null)
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

  useDismissOnOutside(menuRef, accountOpen, () => setOverlay(null))

  const page = (id: DesktopApp, visible: boolean): React.ReactNode => {
    const reading = visible && activePanel === id

    if (id === 'chat') {
      return (
        <ConversationPage
          active={mainChatActive}
          companionSessionId={companionSessionId}
          companionUnread={companionUnread}
          containerRef={rootRef}
          foreground={foreground}
          receiveExternalAttachments={visible && desktopVisible}
          reveal={unreadReveal}
          viewId="desktop-main"
          visible={visible && desktopVisible}
          windowId={1}
        />
      )
    }

    return <AppContentPage containerRef={rootRef} foreground={reading} mediaViewId="desktop-posts" page={id} />
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
      inert={['starting', 'recovering'].includes(presentation.status)}
      ref={rootRef}
      style={
        {
          '--desktop-bar-height': `${DESKTOP_LAYOUT.barHeight}px`,
          '--desktop-bar-bottom': `${safeBottom + DESKTOP_LAYOUT.barBottom}px`,
          '--desktop-safe-top': `${isMac ? (systemWorkArea?.y ?? 0) : 0}px`,
          '--desktop-safe-left': `${safeLeft}px`,
          '--desktop-safe-right': `${safeRight}px`,
          '--desktop-safe-width': isMac && systemWorkArea ? `${systemWorkArea.width}px` : '100vw',
          '--desktop-avatar-size': `${DESKTOP_LAYOUT.avatarSize}px`,
          '--desktop-bottom-inset': `${DESKTOP_LAYOUT.bottomInset}px`,
          '--desktop-content-inset': `${DESKTOP_LAYOUT.contentInset}px`,
          '--desktop-work-area-top': `${workArea?.y ?? 0}px`,
          '--desktop-work-area-left': `${workArea?.x ?? 0}px`,
          '--desktop-work-area-right': workArea ? `calc(100vw - ${workArea.x + workArea.width}px)` : '0px',
          '--desktop-work-area-bottom': workArea
            ? `calc(100vh - ${workArea.y + workArea.height}px)`
            : `${DESKTOP_LAYOUT.bottomInset}px`,
          '--desktop-work-area-width': workArea ? `${workArea.width}px` : '100vw',
          '--desktop-work-area-height': workArea
            ? `${workArea.height}px`
            : `calc(100vh - ${DESKTOP_LAYOUT.bottomInset}px)`,
          '--desktop-center-width': `${DESKTOP_LAYOUT.centerWidth}px`,
          '--desktop-control-size': `${DESKTOP_LAYOUT.controlSize}px`,
          '--desktop-control-edge': `${DESKTOP_LAYOUT.controlEdge}px`,
          '--desktop-control-right': `${safeRight + rail.controlRight}px`,
          '--desktop-bar-available-width': `${rail.width}px`,
          '--desktop-popup-bottom': `${popupBottom}px`
        } as React.CSSProperties
      }
    >
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
      <DesktopBottomBar
        avatar={
          <div className={styles.avatarMenu} ref={menuRef}>
            <button
              aria-current={activeWindow === 'chat' ? 'page' : undefined}
              aria-expanded={accountOpen}
              aria-label={avatarLabel}
              className={styles.avatarButton}
              data-current={activeWindow === 'chat'}
              data-minimized={chatWindow?.minimized ?? false}
              data-unread={companionUnread}
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
              ref={avatarRef}
              title={companionUnread ? t.avatarUnreadHint : t.avatarChatHint}
              type="button"
            >
              <DesktopAvatarSignal unread={companionUnread} />
              {portrait ? (
                <img alt="" className={styles.avatarPortrait} src={portrait} />
              ) : (
                <span className={styles.avatarPortrait}>{strings.brand.name.slice(0, 1)}</span>
              )}
              {chatWindow && <i aria-hidden="true" className={styles.runningDot} />}
            </button>
            <DesktopCompanionBubble onOpen={openCompanionChat} visible={desktopVisible && overlay === null} />
            {accountOpen && (
              <AccountMenu
                bottomInset={popupBottom}
                onClose={() => {
                  setOverlay(null)
                  avatarRef.current?.focus()
                }}
                placement="top-center"
              />
            )}
          </div>
        }
        dockOverlayActive={overlay === 'dock'}
        menuEnabled={desktopVisible}
        navigation={
          <DesktopProductNav
            activeWindow={activeWindow}
            diaryUnread={diaryUnread}
            onActivate={activatePanel}
            onBeforeOpen={() => setOverlay('product')}
            onClose={close}
            onMenuOpenChange={productOpenChange}
            overlayActive={overlay === 'product'}
            postsUnread={postsUnread}
            windows={layout.windows}
          />
        }
        onDockOpen={() => setOverlay('dock')}
        onDockOpenChange={dockOpenChange}
        stageEpoch={presentation.stageEpoch}
        systemTray={presentation.systemTray}
      />
      <DesktopControls
        onDesktopLife={() => activatePanel('desktopLife')}
        onOpenChange={controlsOpenChange}
        open={overlay === 'controls'}
        wallpaperError={Boolean(videoState?.preparation_error || idleVideo?.error || videoError)}
        wallpaperStatus={wallpaperStatus}
      />
    </div>
  )
}
