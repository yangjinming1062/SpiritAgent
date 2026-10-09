import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef } from 'react'

import { ChatPanel, openMainSession, pushExternalAttachment, useIsReadOnlySession } from '@/modules/conversation'
import { log } from '@/shared/lib/log'
import { $gatewayState } from '@/shared/store/gateway'

import { AppearancePage } from './appearance/appearance-page'
import { DesktopLifePage } from './desktop-life-page'
import { DiaryPage } from './diary-page'
import { $livingView, type LivingView } from './living-store'
import styles from './living.module.css'
import { PostsPage } from './posts-page'
import { RemotePage } from './remote-page'
import { ScenePage } from './scene-page'
import { LivingSettings } from './settings/living-settings'

// 视图 → 页面组件；Record<LivingView,…> 强制新成员时报缺 key 错。
const VIEWS: Record<LivingView, React.ComponentType> = {
  appearance: AppearancePage,
  desktopLife: DesktopLifePage,
  remote: RemotePage,
  chat: LivingChatView,
  diary: DiaryPage,
  posts: PostsPage,
  scene: ScenePage,
  settings: LivingSettings
}

export function LivingStage(): React.JSX.Element {
  const view = useStore($livingView)
  const View = VIEWS[view]

  return <View />
}

function LivingChatView(): React.JSX.Element {
  const gatewayState = useStore($gatewayState)
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
