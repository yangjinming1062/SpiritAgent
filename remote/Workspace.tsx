import { IconDeviceTv, IconHeart, IconMessageCircle, IconSettings, IconSparkles } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { GatewayEvent } from '../shared/gateway'
import { isRecord } from '../shared/is-record'

import { errorText, type RemoteApi, type RemoteGateway } from './api'
import { Chat } from './Chat'
import { Companion } from './Companion'
import { Creation, type CreationSection, useCreationSection } from './Creation'
import { Life } from './Life'
import { usePageState } from './page-state'
import { Settings } from './Settings'
import type { RemoteSession } from './types'
import { Notice, pauseMedia } from './ui'

const TABS = [
  { id: 'messages', label: '聊天', icon: IconMessageCircle },
  { id: 'life', label: '生活', icon: IconSparkles },
  { id: 'creation', label: '创作', icon: IconDeviceTv },
  { id: 'companion', label: '伙伴', icon: IconHeart },
  { id: 'me', label: '设置', icon: IconSettings }
] as const
type Tab = (typeof TABS)[number]['id']
type Page = Exclude<Tab, 'creation'> | CreationSection
const EMPTY_UNREAD = { messages: false, diary: false }

function savedUnread(key: string): typeof EMPTY_UNREAD {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(key) ?? 'null')
    if (isRecord(raw)) {
      return { messages: raw.messages === true, diary: raw.diary === true }
    }
  } catch {
    // 浏览器存储不可用时从本次会话开始。
  }
  return EMPTY_UNREAD
}

// 话题反馈就地更新。其余变更只唤醒当前打开的相关页面。
function contentChanged(page: Page, type: GatewayEvent['type']): boolean {
  switch (type) {
    case 'companion.life.updated':
    case 'companion.diary.created':
    case 'companion.diary.read':
      return page === 'life'
    case 'companion.scene.updated':
      return page === 'scene'
    case 'companion.outfit.updated':
      return page === 'outfit'
    case 'companion.action.updated':
      return page === 'action'
    case 'companion.visual.updated':
    case 'companion.visual.initial_requested':
    case 'companion.visual.playback':
      return page === 'visuals'
    case 'media.review.updated':
      return page === 'outfit' || page === 'scene' || page === 'visuals'
    case 'companion.appearance_preparation.updated':
    case 'companion.visual_identity.updated':
      return page === 'companion' || page === 'outfit'
    case 'companion.mood':
      return page === 'life' || page === 'companion'
    case 'memory.changed':
      return page === 'companion'
    case 'companion.message':
      return page === 'messages'
    default:
      return false
  }
}

export function Workspace({
  api,
  gateway,
  session,
  onLogout,
  pairingError
}: {
  api: RemoteApi
  gateway: RemoteGateway
  session: RemoteSession
  onLogout: () => Promise<void>
  pairingError: string
}): ReactElement {
  const [savedTab, setTab] = usePageState<Tab>('tab', 'messages')
  const tab = TABS.find(item => item.id === savedTab)?.id ?? 'messages'
  const [section, setSection] = useCreationSection()
  const page = tab === 'creation' ? section : tab
  const currentPage = useRef<Page>(page)
  currentPage.current = page
  const [connected, setConnected] = useState(gateway.open)
  const [connectionError, setConnectionError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [unreadRefresh, setUnreadRefresh] = useState(0)
  const unreadKey = `remote:${session.user.id}:${session.device.id}:unread`
  const [unread, setUnread] = useState(() => savedUnread(unreadKey))
  const refreshSnapshot = useCallback(() => {
    setRefresh(value => value + 1)
    setUnreadRefresh(value => value + 1)
  }, [])

  useEffect(() => {
    document.querySelector('.content')?.scrollTo({ top: 0 })
  }, [tab])

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let dirtyPage: Page | undefined
    let unreadDirty = false
    let wasOpen = false
    const removeState = gateway.subscribeState((open, message) => {
      setConnected(open)
      setConnectionError(message)
      if (open && !wasOpen && gateway.lastRecovery?.recovery === 'snapshot_required') {
        refreshSnapshot()
      }
      wasOpen = open
    })
    const removeEvent = gateway.subscribeEvent(event => {
      if (
        ((event.type === 'message.complete' && !event.payload.decision) || event.type === 'companion.message') &&
        (currentPage.current !== 'messages' || document.visibilityState !== 'visible')
      ) {
        setUnread(previous => ({ ...previous, messages: true }))
      }
      if (contentChanged(currentPage.current, event.type)) {
        dirtyPage = currentPage.current
      }
      unreadDirty ||= event.type === 'companion.diary.created' || event.type === 'companion.diary.read'
      if ((!dirtyPage && !unreadDirty) || timer !== undefined) {
        return
      }
      timer = setTimeout(() => {
        timer = undefined
        if (document.visibilityState === 'visible') {
          if (dirtyPage === currentPage.current) {
            setRefresh(value => value + 1)
          }
          if (unreadDirty) {
            setUnreadRefresh(value => value + 1)
          }
        }
        dirtyPage = undefined
        unreadDirty = false
      }, 200)
    })
    void gateway.connect()
    return () => {
      clearTimeout(timer)
      removeState()
      removeEvent()
    }
  }, [gateway, refreshSnapshot])

  useEffect(() => {
    try {
      localStorage.setItem(unreadKey, JSON.stringify(unread))
    } catch {
      // 浏览器存储不可用时仍保留本次连接的提醒。
    }
  }, [unread, unreadKey])

  useEffect(() => {
    if (document.visibilityState !== 'visible') {
      return
    }

    let current = true
    void api
      .invoke('companion_journal.get_diary_unread')
      .then(result => {
        if (current) {
          setUnread(previous => ({ ...previous, diary: result.has_unread }))
        }
      })
      .catch((error: unknown) => {
        if (current) {
          console.warn('远程日记未读状态校准失败', errorText(error))
        }
      })

    return () => {
      current = false
    }
  }, [api, unreadRefresh])

  useEffect(() => {
    const foreground = () => {
      if (document.visibilityState === 'visible') {
        refreshSnapshot()
        void gateway.connect()
      } else {
        pauseMedia()
      }
    }

    document.addEventListener('visibilitychange', foreground)
    window.addEventListener('online', foreground)
    window.addEventListener('focus', foreground)

    return () => {
      document.removeEventListener('visibilitychange', foreground)
      window.removeEventListener('online', foreground)
      window.removeEventListener('focus', foreground)
    }
  }, [gateway, refreshSnapshot])

  const selectTab = (next: Tab) => {
    setTab(next)
    if (next === 'messages') {
      setUnread(previous => ({ ...previous, messages: false }))
    }
    pauseMedia()
  }

  return (
    <div className="app">
      <div aria-live="polite" className={`connection ${connected ? '' : 'offline'}`}>
        {connected ? '已连接' : connectionError || '实时连接正在恢复，已受理的任务会继续运行'}
      </div>
      <main className="content">
        {pairingError && <Notice>{pairingError}</Notice>}
        {tab === 'messages' ? (
          <Chat
            api={api}
            connected={connected}
            device={session.device.id}
            gateway={gateway}
            refresh={refresh}
            userId={session.user.id}
          />
        ) : tab === 'life' ? (
          <Life
            api={api}
            diaryUnread={unread.diary}
            onRead={() => setUnreadRefresh(value => value + 1)}
            refresh={refresh}
          />
        ) : tab === 'creation' ? (
          <Creation api={api} onSelect={setSection} refresh={refresh} section={section} />
        ) : tab === 'companion' ? (
          <Companion api={api} refresh={refresh} />
        ) : (
          <Settings onLogout={onLogout} session={session} />
        )}
      </main>
      <nav aria-label="主导航" className="bottom-nav">
        {TABS.map(item => (
          <button
            aria-current={tab === item.id ? 'page' : undefined}
            className={tab === item.id ? 'selected' : ''}
            key={item.id}
            onClick={() => selectTab(item.id)}
            type="button"
          >
            <span>
              <item.icon size={23} stroke={1.7} />
              {(item.id === 'messages' && unread.messages) || (item.id === 'life' && unread.diary) ? (
                <i aria-label="有未读内容" className="unread-dot" />
              ) : null}
            </span>
            {item.label}
          </button>
        ))}
      </nav>
    </div>
  )
}
