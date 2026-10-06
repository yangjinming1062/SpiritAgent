import type { WritableAtom } from 'nanostores'

import { apiSucceeded, authedApi, captureAuthScope } from './authed-api'
import { isRecord } from './is-record'
import { registerStorageClearHandler } from './storage'

interface UnreadWire {
  has_unread: boolean
}

export interface UnreadMirror {
  /** 合并同轮信号后查询；在途期间的新信号由请求结束后的补查处理。 */
  hydrate: () => Promise<boolean>
  /** 确认已读；每次调用返回独立 promise，供调用方跟踪在途确认。 */
  markRead: (ids: string[]) => Promise<boolean>
}

export interface UnreadMirrorConfig {
  scope: string
  unreadPath: string
  readPath: string
  /** markRead 请求体承载 id 列表的字段名。 */
  idField: string
  $hasUnread: WritableAtom<boolean>
}

function isUnreadWire(value: unknown): value is UnreadWire {
  return isRecord(value) && typeof value.has_unread === 'boolean'
}

/** 未读水合/确认状态机；revision 代次与在途请求为实例私有，多个镜像互不共享代次。 */
export function createUnreadMirror(config: UnreadMirrorConfig): UnreadMirror {
  const { $hasUnread, idField, readPath, scope, unreadPath } = config
  let unreadRevision = 0
  let unreadRequest: Promise<boolean> | undefined

  function hydrate(): Promise<boolean> {
    unreadRevision++

    if (unreadRequest !== undefined) {
      return unreadRequest
    }

    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return Promise.resolve(false)
    }

    let version = unreadRevision

    const request = Promise.resolve()
      .then(async () => {
        if (!isCurrent()) {
          return false
        }

        version = unreadRevision
        const result = await authedApi<UnreadWire>({ path: unreadPath })

        if (
          !isCurrent() ||
          version !== unreadRevision ||
          !apiSucceeded(result, scope, 'unread failed') ||
          !isUnreadWire(result.value)
        ) {
          return false
        }

        $hasUnread.set(result.value.has_unread)

        return true
      })
      .finally(() => {
        if (unreadRequest === request) {
          unreadRequest = undefined

          if (isCurrent() && version !== unreadRevision) {
            void hydrate()
          }
        }
      })

    unreadRequest = request

    return request
  }

  async function markRead(ids: string[]): Promise<boolean> {
    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return false
    }

    if (ids.length === 0) {
      return true
    }

    // 作废确认前的查询；发布或其他窗口的事件会再次推进代次。
    const version = ++unreadRevision

    const result = await authedApi<UnreadWire>({
      method: 'POST',
      path: readPath,
      body: { [idField]: ids }
    })

    if (!isCurrent() || !apiSucceeded(result, scope, 'read failed') || !isUnreadWire(result.value)) {
      return false
    }

    if (version === unreadRevision) {
      $hasUnread.set(result.value.has_unread)
    } else {
      void hydrate()
    }

    return true
  }

  // 账户清理时推进代次并作废在途请求，旧会话结果不得写回。
  registerStorageClearHandler(() => {
    unreadRevision++
    unreadRequest = undefined
    $hasUnread.set(false)
  })

  return { hydrate, markRead }
}
