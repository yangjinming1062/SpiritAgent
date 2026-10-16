import type { WritableAtom } from 'nanostores'

import type { ApiSchemas } from '../../../../shared/api.generated'

import { apiSucceeded, captureAuthScope } from './authed-api'
import { isRecord } from './is-record'
import { authedResource } from './resource-request'
import { registerStorageClearHandler } from './storage'

type UnreadWire = ApiSchemas['PostUnreadResponse'] | ApiSchemas['DiaryUnreadResponse']

export interface UnreadMirror {
  /** 合并同轮信号后查询；在途期间的新信号由请求结束后的补查处理。 */
  hydrate: () => Promise<boolean>
  /** 确认已读；每次调用返回独立 promise，供调用方跟踪在途确认。 */
  markRead: (ids: string[]) => Promise<boolean>
}

export interface UnreadMirrorConfig {
  scope: string
  resource: 'posts' | 'diary'
  $hasUnread: WritableAtom<boolean>
}

function isUnreadWire(value: unknown): value is UnreadWire {
  return isRecord(value) && typeof value.has_unread === 'boolean'
}

/** 未读水合/确认状态机；revision 代次与在途请求为实例私有，多个镜像互不共享代次。 */
export function createUnreadMirror(config: UnreadMirrorConfig): UnreadMirror {
  const { $hasUnread, resource, scope } = config
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

        const result = await (resource === 'posts'
          ? authedResource('GET /api/companion/posts/read-state')
          : authedResource('GET /api/companion/diary/read-state'))

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

    const result = await (resource === 'posts'
      ? authedResource('PATCH /api/companion/posts/read-state', { body: { post_ids: ids } })
      : authedResource('PATCH /api/companion/diary/read-state', { body: { diary_ids: ids } }))

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
