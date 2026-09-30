// 生活空间右栏：根据 living-view 切换内容（chat 共用 ChatPanel；appearance 衣柜；moments/diary 后端直连；channels 单文件页；scene 库/详情/创建；settings 分区胶囊）。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef } from 'react'

import { ChatPanel, openMainSession, pushExternalAttachment, useIsReadOnlySession } from '@/modules/conversation'
import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $gatewayState } from '@/shared/store/gateway'

import { AppearancePage } from './appearance/appearance-page'
import { ChannelsPage } from './channels-page'
import { DiaryPage } from './diary-page'
import { $livingView, type LivingView } from './living-store'
import styles from './living.module.css'
import { MomentsPage } from './moments-page'
import { ScenePage } from './scene-page'
import { LivingSettings } from './settings/living-settings'

// 视图 → 渲染组件闭包表（`chat` 走 ChatStage 局部组件）；Record<LivingView,…> 强制新成员时报缺 key 错。
const VIEW_RENDERERS: Record<LivingView, () => React.JSX.Element> = {
  appearance: () => <AppearancePage />,
  channels: () => <ChannelsPage />,
  chat: () => <ChatStage />,
  diary: () => <DiaryPage />,
  moments: () => <MomentsPage />,
  scene: () => <ScenePage />,
  settings: () => <LivingSettings />
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
  const isReadOnlySession = useIsReadOnlySession()
  const scrollRef = useRef<HTMLDivElement>(null)

  // 会话 ID 可从持久缓存恢复，但各窗口的消息列表独立；打开对话或重连时仍须加载历史。
  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    void openMainSession()
  }, [gatewayState])

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
        .catch(error => log.warn('living-stage', 'takePendingFeed failed', error))
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
