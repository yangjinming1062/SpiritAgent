import crypto from 'node:crypto'
import fsp from 'node:fs/promises'
import path from 'node:path'

import { atomicWriteFile } from '../shared/utils'

function cacheKey(voice: string, text: string): string {
  return crypto.createHash('sha1').update(`${voice}\n${text}`).digest('hex')
}

export interface TtsDiskCache {
  read: (options: { language: string; text: string; voice: string }) => Promise<Buffer | null>
  /** 只落盘 mp3，返回是否写入；写盘失败时抛出。 */
  write: (options: {
    buffer: Buffer
    language: string
    mimeType: string
    text: string
    voice: string
  }) => Promise<boolean>
}

export function createTtsDiskCache({ spiritagentHome }: { spiritagentHome: string }): TtsDiskCache {
  const dirFor = (language: string) => path.resolve(spiritagentHome, 'audio', 'tts-cache', language)

  const pathFor = (voice: string, text: string, language: string) =>
    path.join(dirFor(language), `${cacheKey(voice, text)}.mp3`)

  return {
    async read({ language, text, voice }): Promise<Buffer | null> {
      try {
        const stat = await fsp.stat(pathFor(voice, text, language))

        if (!stat.isFile()) {
          return null
        }

        return await fsp.readFile(pathFor(voice, text, language))
      } catch {
        return null
      }
    },

    async write({ buffer, language, mimeType, text, voice }): Promise<boolean> {
      if (mimeType !== 'audio/mpeg') {
        return false
      }

      await atomicWriteFile(pathFor(voice, text, language), buffer)

      return true
    }
  }
}
