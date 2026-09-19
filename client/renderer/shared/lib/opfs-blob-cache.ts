import { $auth } from '@/shared/store/auth'

import { log } from './log'
import { currentClearEpoch } from './storage'

const SCHEMA_VERSION = 1
const META_SUFFIX = '.meta.json'

/** 元数据只校验结构与字节完整性；文件名中的哈希标识内容。 */
interface MetaFile {
  version: number
  size: number
}

interface OpfsBlobCacheOptions {
  dirName: string
  blobSuffix: string
  logTag: string
}

interface InFlightFetch {
  controller: AbortController
  epoch: number
  promise: Promise<ArrayBuffer | null>
}

interface FetchWithCacheOptions {
  url: string
  contentHash?: string | null
  signal?: AbortSignal
  /** 真正执行拉取的回调（主进程 IPC / spiritagent-media:// / 同源 fetch）。
   * 必须自行吞掉 abort 情况：abort 时返回 null（不要抛 DOMException）。
   * 非 abort 错误可抛，由 throwOnError 决定 fetchWithCache 是否吞掉。 */
  fetcher: (signal: AbortSignal) => Promise<ArrayBuffer | null>
  /** 字节级校验（如 PSD 魔术字节）。失败时调度 delete 并跳过缓存写。 */
  validate?: (buffer: ArrayBuffer) => boolean
  /** true：abort / fetch 错误直接抛（保留 PSD 旧契约）。false：吞掉并返回 null（GLB 旧契约）。 */
  throwOnError?: boolean
}

export class OpfsBlobCache {
  private readonly dirName: string
  private readonly blobSuffix: string
  private readonly logTag: string
  private queue: Promise<unknown> = Promise.resolve()
  private readonly inFlightFetches = new Map<string, InFlightFetch>()

  constructor(options: OpfsBlobCacheOptions) {
    this.dirName = options.dirName
    this.blobSuffix = options.blobSuffix
    this.logTag = options.logTag
  }

  private metaKey(contentHash: string): string {
    return `${contentHash}${META_SUFFIX}`
  }

  private blobKey(contentHash: string): string {
    return `${contentHash}${this.blobSuffix}`
  }

  private async getDir(): Promise<FileSystemDirectoryHandle | null> {
    if (typeof navigator === 'undefined' || !navigator.storage?.getDirectory) {
      return null
    }

    try {
      const root = await navigator.storage.getDirectory()

      return await root.getDirectoryHandle(this.dirName, { create: true })
    } catch (err) {
      log.warn(this.logTag, 'OPFS unavailable:', err)

      return null
    }
  }

  private runSerialized<T>(task: () => Promise<T>): Promise<T> {
    const next = this.queue.then(
      () => task(),
      () => task()
    )

    this.queue = next.then(
      () => undefined,
      () => undefined
    )

    return next
  }

  async read(contentHash: string): Promise<ArrayBuffer | null> {
    if (!contentHash) {
      return null
    }

    const dir = await this.getDir()

    if (!dir) {
      return null
    }

    try {
      const metaHandle = await dir.getFileHandle(this.metaKey(contentHash))
      const metaFile = await metaHandle.getFile()
      const meta = JSON.parse(await metaFile.text()) as Partial<MetaFile>

      if (
        meta.version !== SCHEMA_VERSION ||
        typeof meta.size !== 'number' ||
        !Number.isFinite(meta.size) ||
        meta.size < 0
      ) {
        // meta 损坏：直接清掉当前 dir 的两条条目，不重入 runSerialized 队列
        // （read 不在队列里，delete 进队列会让后续 read 也排队，反而慢）。
        try {
          await dir.removeEntry(this.metaKey(contentHash))
        } catch {}

        try {
          await dir.removeEntry(this.blobKey(contentHash))
        } catch {}

        return null
      }

      const blobHandle = await dir.getFileHandle(this.blobKey(contentHash))
      const blobFile = await blobHandle.getFile()

      if (blobFile.size !== meta.size) {
        // 字节数不匹配意味着缓存不完整；
        // 走 try/catch 容忍并发 OPFS 锁异常，下一次 read 会再次尝试清理。
        try {
          await dir.removeEntry(this.blobKey(contentHash))
        } catch {}

        try {
          await dir.removeEntry(this.metaKey(contentHash))
        } catch {}

        return null
      }

      const buffer = await blobFile.arrayBuffer()

      return buffer
    } catch (error) {
      // 内层已单独处理 meta 损坏与 size mismatch；外层按 DOMException 分类，
      // 配额/权限/不支持等异常必须留痕，不能一律静默当 miss 反复走网络。
      if (error instanceof DOMException) {
        const name = error.name

        if (name === 'QuotaExceededError' || name === 'SecurityError' || name === 'NotSupportedError') {
          log.warn(this.logTag, `OPFS read failed (${name}); treating as cache miss`, error)
        }
      }

      return null
    }
  }

  async write(contentHash: string, bytes: ArrayBuffer): Promise<void> {
    if (!contentHash || !bytes || bytes.byteLength === 0) {
      return
    }

    await this.runSerialized(() => this.writeInternal(contentHash, bytes))
  }

  async delete(contentHash: string): Promise<void> {
    if (!contentHash) {
      return
    }

    await this.runSerialized(async () => {
      const dir = await this.getDir()

      if (!dir) {
        return
      }

      try {
        await dir.removeEntry(this.blobKey(contentHash))
      } catch {}

      try {
        await dir.removeEntry(this.metaKey(contentHash))
      } catch {}
    })
  }

  async clear(): Promise<void> {
    // 先同步废掉所有进行中 fetch：abort 让 fetcher 内部的 await 早退。
    // 注意：若 IIFE 已经过了 write 闸门并调度了 `void this.write(...)`，controller abort 已晚，
    // writeInternal 仍会跑（写完被后续的 clear-task 清掉）——这是浪费的 I/O，不是正确性问题。
    // 写入不会污染新用户：clearEpoch 已被 clearCompanionStorage 推进，下次 read 会拒掉过期 hash。
    for (const item of this.inFlightFetches.values()) {
      item.controller.abort()
    }

    this.inFlightFetches.clear()

    await this.runSerialized(async () => {
      const dir = await this.getDir()

      if (!dir) {
        return
      }

      try {
        const entries: string[] = []

        for await (const handle of (dir as unknown as { values: () => AsyncIterable<FileSystemHandle> }).values()) {
          if (typeof handle.name === 'string') {
            entries.push(handle.name)
          }
        }

        for (const name of entries) {
          try {
            // recursive: false —— 缓存目录约定 flat，不允许子目录被静默清空。
            await dir.removeEntry(name, { recursive: false })
          } catch {}
        }

        log.info(this.logTag, `Cleared OPFS cache directory ${this.dirName}`)
      } catch (err) {
        log.warn(this.logTag, `Failed to clear cache directory ${this.dirName}:`, err)
      }
    })
  }

  /** 通用「OPFS 缓存 + 远端拉取」包装：epoch 闸门、读侧校验、in-flight dedupe、写侧三道闸门。
   * 单一来源替代 glb-opfs-cache.ts / psd-opfs-cache.ts 各 ~140 行 twin 实现。 */
  async fetchWithCache(opts: FetchWithCacheOptions): Promise<ArrayBuffer | null> {
    const { contentHash, fetcher, signal, throwOnError = false, url, validate } = opts

    const failOrThrow = (): null => {
      if (throwOnError) {
        throw new DOMException('Aborted', 'AbortError')
      }

      return null
    }

    if (signal?.aborted) {
      return failOrThrow()
    }

    // 登出 race：clearCompanionStorage 可能在本 fetch 启动后才推进 epoch。
    // 用快照 epoch 在 commit 前对照 currentClearEpoch()，把过期 fetch 拦下来。
    const fetchEpoch = currentClearEpoch()

    if (contentHash && currentClearEpoch() === fetchEpoch) {
      const cached = await this.read(contentHash)

      if (currentClearEpoch() !== fetchEpoch) {
        return failOrThrow()
      }

      if (cached) {
        if (validate && !validate(cached)) {
          // 读侧校验失败：清掉条目但不让 read() 抛 —— 让上层走正常 fetch 路径。
          void this.delete(contentHash)
        } else if (signal?.aborted) {
          return failOrThrow()
        } else {
          log.info(this.logTag, 'OPFS hit:', contentHash)

          return cached
        }
      }
    }

    if (signal?.aborted) {
      return failOrThrow()
    }

    const dedupeKey = contentHash || url
    let inFlight = this.inFlightFetches.get(dedupeKey)

    if (!inFlight) {
      const controller = new AbortController()

      const promise = (async () => {
        try {
          const buffer = await fetcher(controller.signal)

          if (!buffer) {
            // fetcher 在内部检测到 signal 中止时返回 null：throwOnError=true 时（PSD 旧契约）
            // 抛 DOMException，调用方的 try/catch 就能识别为「干净的取消」而非错误。
            if (throwOnError) {
              throw new DOMException('Aborted', 'AbortError')
            }

            return null
          }

          if (validate && !validate(buffer)) {
            throw new Error(`Downloaded asset failed validation (${this.logTag})`)
          }

          // 三道闸门：clearEpoch 未变 + authed + 未被 abort，避免过期 fetch 复活刚清空的 OPFS
          if (
            contentHash &&
            currentClearEpoch() === fetchEpoch &&
            $auth.get().kind === 'authenticated' &&
            !controller.signal.aborted
          ) {
            void this.write(contentHash, buffer)
          }

          return buffer
        } catch (err) {
          // throwOnError=true（PSD 旧契约）：把错误原样抛给调用方，不在本层吞。
          // throwOnError=false（GLB 旧契约）：吞掉并返回 null。
          if (throwOnError) {
            throw err
          }

          if (!controller.signal.aborted) {
            log.warn(this.logTag, 'Fetch failed:', err)
          }

          return null
        } finally {
          this.inFlightFetches.delete(dedupeKey)
        }
      })()

      inFlight = { controller, epoch: fetchEpoch, promise }
      this.inFlightFetches.set(dedupeKey, inFlight)
    }

    const result = await inFlight.promise

    if (signal?.aborted) {
      return failOrThrow()
    }

    return result
  }

  private async writeInternal(contentHash: string, bytes: ArrayBuffer): Promise<void> {
    const dir = await this.getDir()

    if (!dir) {
      return
    }

    try {
      const blobHandle = await dir.getFileHandle(this.blobKey(contentHash), { create: true })
      const blobWritable = await blobHandle.createWritable()

      try {
        await blobWritable.write(bytes)
      } finally {
        await blobWritable.close()
      }

      const meta: MetaFile = {
        size: bytes.byteLength,
        version: SCHEMA_VERSION
      }

      const metaHandle = await dir.getFileHandle(this.metaKey(contentHash), { create: true })
      const metaWritable = await metaHandle.createWritable()

      try {
        await metaWritable.write(JSON.stringify(meta))
      } finally {
        await metaWritable.close()
      }
    } catch (err) {
      log.warn(this.logTag, 'write failed; cleaning up partials', err)

      try {
        await dir.removeEntry(this.blobKey(contentHash))
      } catch {}

      try {
        await dir.removeEntry(this.metaKey(contentHash))
      } catch {}
    }
  }
}
