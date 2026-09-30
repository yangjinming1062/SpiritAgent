import type { App } from 'electron'

export interface SingleInstanceGate {
  /** 完整转发器注册后调用：移除早期监听，期间收到过第二实例事件时兑现一次。 */
  replayEarlySecondInstance: (onSecondInstance: () => void) => void
}

/** 须在 app ready 前调用。默认单实例，未取得锁即退出；`SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 只用于并行验证。完整转发器等精灵窗创建后才注册，此前第二实例事件折叠成标志，就绪后兑现。 */
export function acquireSingleInstance(
  app: Pick<App, 'exit' | 'on' | 'removeListener' | 'requestSingleInstanceLock'>
): SingleInstanceGate {
  if (process.env.SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK !== '1' && !app.requestSingleInstanceLock()) {
    app.exit(0)
  }

  let pending = false

  const onEarlySecondInstance = (): void => {
    pending = true
  }

  app.on('second-instance', onEarlySecondInstance)

  return {
    replayEarlySecondInstance: onSecondInstance => {
      app.removeListener('second-instance', onEarlySecondInstance)

      if (pending) {
        pending = false
        onSecondInstance()
      }
    }
  }
}
