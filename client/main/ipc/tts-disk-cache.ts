import crypto from 'node:crypto'
import fsp from 'node:fs/promises'
import path from 'node:path'

import { atomicWriteFile } from '../shared/utils'

const MAX_CACHE_FILES = 800
const MAX_ENTRY_BYTES = 1024 * 1024

function cacheKey(voice: string, text: string): string {
  return crypto.createHash('sha1').update(`${voice}\n${text}`).digest('hex')
}

export interface TtsDiskCache {
  read: (options: { language: string; text: string; voice: string }) => Promise<Buffer | null>
  write: (options: { buffer: Buffer; language: string; mimeType: string; text: string; voice: string }) => Promise<void>
}

export function createTtsDiskCache({ spiritagentHome }: { spiritagentHome?: null | string }): TtsDiskCache {
  if (!spiritagentHome) {
    throw new Error('createTtsDiskCache: spiritagentHome is required')
  }

  const dirFor = (language: string) => path.resolve(spiritagentHome, 'audio', 'tts-cache', language)

  const pathFor = (voice: string, text: string, language: string) =>
    path.join(dirFor(language), `${cacheKey(voice, text)}.mp3`)

  async function sweep(language: string): Promise<void> {
    try {
      const dir = dirFor(language)
      const names = await fsp.readdir(dir)

      if (names.length <= MAX_CACHE_FILES) {
        return
      }

      const entries = await Promise.all(
        names.map(async name => {
          const filePath = path.join(dir, name)
          const stat = await fsp.stat(filePath).catch(() => null)

          return stat?.isFile() ? { filePath, mtimeMs: stat.mtimeMs } : null
        })
      )

      const files = entries.filter((e): e is NonNullable<typeof e> => e !== null).sort((a, b) => a.mtimeMs - b.mtimeMs)

      for (const { filePath } of files.slice(0, files.length - MAX_CACHE_FILES)) {
        await fsp.unlink(filePath).catch(() => {})
      }
    } catch (err) {
      console.warn('[tts-disk-cache] sweep failed', err)
    }
  }

  return {
    async read({ language, text, voice }): Promise<Buffer | null> {
      try {
        const stat = await fsp.stat(pathFor(voice, text, language))

        if (!stat.isFile() || stat.size > MAX_ENTRY_BYTES) {
          return null
        }

        return await fsp.readFile(pathFor(voice, text, language))
      } catch {
        return null
      }
    },

    async write({ buffer, language, mimeType, text, voice }): Promise<void> {
      if (mimeType !== 'audio/mpeg' || buffer.length > MAX_ENTRY_BYTES) {
        return
      }

      try {
        await atomicWriteFile(pathFor(voice, text, language), buffer)
        await sweep(language)
      } catch (err) {
        console.warn('[tts-disk-cache] write failed', err)
      }
    }
  }
}
