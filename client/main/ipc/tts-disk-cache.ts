import crypto from 'node:crypto'
import fsp from 'node:fs/promises'
import path from 'node:path'

import { atomicWriteFile, createSerialQueue, isAccountId } from '../shared/utils'

export interface TtsDiskCache {
  clear: (accountId: string) => Promise<void>
  read: (options: { accountId: string; language: string; text: string; voice: string }) => Promise<Buffer | null>
  /** 只落盘 mp3，返回是否写入；写盘失败时抛出。 */
  write: (options: {
    accountId: string
    buffer: Buffer
    language: string
    mimeType: string
    text: string
    voice: string
  }) => Promise<boolean>
}

export function createTtsDiskCache({ spiritagentHome }: { spiritagentHome: string }): TtsDiskCache {
  const root = path.resolve(spiritagentHome, 'audio', 'tts-cache')
  const queues = new Map<string, ReturnType<typeof createSerialQueue>>()

  function withAccount<T>(accountId: string, operation: (directory: string) => Promise<T>): Promise<T> {
    if (!isAccountId(accountId)) {
      throw new Error('Invalid TTS cache account')
    }

    let queue = queues.get(accountId)

    if (!queue) {
      queue = createSerialQueue()
      queues.set(accountId, queue)
    }

    return queue(() => operation(path.join(root, accountId)))
  }

  const filenameFor = (voice: string, text: string, language: string): string =>
    `${crypto.hash('sha1', `${voice}\n${language}\n${text}`)}.mp3`

  return {
    clear: accountId => withAccount(accountId, directory => fsp.rm(directory, { recursive: true, force: true })),
    async read({ accountId, language, text, voice }): Promise<Buffer | null> {
      return withAccount(accountId, async directory => {
        const cachePath = path.join(directory, filenameFor(voice, text, language))

        try {
          const stat = await fsp.stat(cachePath)

          if (!stat.isFile()) {
            return null
          }

          return await fsp.readFile(cachePath)
        } catch {
          return null
        }
      })
    },

    async write({ accountId, buffer, language, mimeType, text, voice }): Promise<boolean> {
      if (mimeType !== 'audio/mpeg') {
        return false
      }

      await withAccount(accountId, directory =>
        atomicWriteFile(path.join(directory, filenameFor(voice, text, language)), buffer)
      )

      return true
    }
  }
}
