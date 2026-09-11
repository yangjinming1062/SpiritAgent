// 生活空间右栏：根据 living-view 切换内容。
//
// - chat: ChatPanel（生活空间变体的对话 + 输入；与工作台共用）
// - wardrobe: WardrobePage（衣橱）
// - appearance: AppearancePage（形象 / 渲染模式）
// - moments / diary: 后端直连两页
// - channels / room: 单文件页
// - settings: LivingSettings（分区胶囊：角色/音色/交互/外观/快捷键/关于）

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef } from 'react'

import {
  $chatSessionId,
  $companionSessionId,
  ChatPanel,
  openMainSession,
  pushExternalAttachment,
  useIsReadOnlySession
} from '@/modules/conversation'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { $gatewayState } from '@/shared/store/gateway'

import { AppearancePage } from './appearance-page'
import { ChannelsPage } from './channels-page'
import { DiaryPage } from './diary-page'
import { $livingView, type LivingView } from './living-store'
import styles from './living.module.css'
import { MomentsPage } from './moments-page'
import { RoomPage } from './room-page'
import { LivingSettings } from './settings/living-settings'
import { WardrobePage } from './wardrobe-page'

// 视图 → 渲染组件的闭包表（`chat` 走 ChatStage 局部组件，其他直接挂页）。
// Record<LivingView, …> 强制 LivingView 出现新成员时报缺 key 错。
const VIEW_RENDERERS: Record<LivingView, () => React.JSX.Element> = {
  appearance: () => <AppearancePage />,
  channels: () => <ChannelsPage />,
  chat: () => <ChatStage />,
  diary: () => <DiaryPage />,
  moments: () => <MomentsPage />,
  room: () => <RoomPage />,
  settings: () => <LivingSettings />,
  wardrobe: () => <WardrobePage />
}

export function LivingStage(): React.JSX.Element {
  const view = useStore($livingView)

  return VIEW_RENDERERS[view]()
}

function ChatStage(): React.JSX.Element {
  const gatewayState = useStore($gatewayState)

  return <LivingChatView gatewayState={gatewayState} />
}

function LivingChatView({ gatewayState }: { gatewayState: ConnectionState }): React.JSX.Element {
  const chatSessionId = useStore($chatSessionId)
  const companionSessionId = useStore($companionSessionId)
  const isReadOnlySession = useIsReadOnlySession()
  const scrollRef = useRef<HTMLDivElement>(null)

  // 生活空间全生命周期只使用唯一的「陪伴」对话；若尚未加载或当前处于工作会话，自动定位为主陪伴会话
  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    if (!companionSessionId || chatSessionId !== companionSessionId) {
      void openMainSession()
    }
  }, [gatewayState, companionSessionId, chatSessionId])

  // 精灵窗投喂的混合文件：广播只作信号，统一经主进程 take 取走（取走即清，避免重复附件）。
  useEffect(() => {
    const drain = (): void => {
      void window.spiritagent.chat
        .takePendingFeed()
        .then(paths => {
          if (paths.length > 0) {
            pushExternalAttachment(paths)
          }
        })
        .catch(() => {})
    }

    const off = window.spiritagent.chat.onPendingFeed(() => {
      drain()
    })

    // 刚创建的窗口可能错过广播，挂载后补取一次。
    drain()

    return off
  }, [gatewayState])

  return (
    <div className={styles.chatStage}>
      <ChatPanel
        gatewayState={gatewayState}
        inputWrapperClassName={styles.chatInputWrapper}
        isReadOnlySession={isReadOnlySession}
        scrollRef={scrollRef}
        surfaceClassName={styles.chatSurface}
        variant="living"
      />
    </div>
  )
}
