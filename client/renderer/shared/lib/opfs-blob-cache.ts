import { $auth } from '@/shared/store/auth'

import { log } from './log'
import { currentClearEpoch } from './storage'

const SCHEMA_VERSION = 1
const META_SUFFIX = '.meta.json'
const DEFAULT_MAX_FILES = 10
const DEFAULT_MAX_BYTES = 512 * 1024 * 1024

/** v1 元数据：仅保留 LRU 必需三件套（version / size / writtenAt）。
 * `contentHash` 早期版本曾作为字段写入，2026-08 后被剔除——filename `<hash>.meta.json` 已经
 * 是其唯一来源，再写一份是冗余。旧版 meta.json 仍带 contentHash 字段，新代码 touchInternal
 * 直接 JSON.stringify 旧对象回写，contentHash 字段会被原样保留；功能不受影响，旧条目最终
 * 通过 LRU 自然淘汰，无需主动迁移。 */
interface MetaFile {
  version: number
  writtenAt: number
  size: number
}

interface OpfsBlobCacheOptions {
  dirName: string
  blobSuffix: string
  maxFiles?: number
  maxBytes?: number
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
  private readonly maxFiles: number
  private readonly maxBytes: number
  private readonly logTag: string
  private queue: Promise<unknown> = Promise.resolve()
  private readonly lastTouched = new Map<string, number>()
  private readonly inFlightFetches = new Map<string, InFlightFetch>()

  constructor(options: OpfsBlobCacheOptions) {
    this.dirName = options.dirName
    this.blobSuffix = options.blobSuffix
    this.maxFiles = options.maxFiles ?? DEFAULT_MAX_FILES
    this.maxBytes = options.maxBytes ?? DEFAULT_MAX_BYTES
    this.logTag = options.logTag
  }

  private metaKey(contentHash: string): string {
    return `${contentHash}${META_SUFFIX}`
  }

  private blobKey(contentHash: string): string {
    return `${contentHash}${this.blobSuffix}`
  }

  private isMetaFile(name: string): boolean {
    return name.endsWith(META_SUFFIX)
  }

  private hashFromMetaFile(name: string): string | null {
    if (!name.endsWith(META_SUFFIX)) {
      return null
    }

    return name.slice(0, -META_SUFFIX.length)
  }

  private isBlobFile(name: string): boolean {
    return name.endsWith(this.blobSuffix)
  }

  private hashFromBlobFile(name: string): string | null {
    if (!name.endsWith(this.blobSuffix)) {
      return null
    }

    return name.slice(0, -this.blobSuffix.length)
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
        meta.size < 0 ||
        typeof meta.writtenAt !== 'number' ||
        !Number.isFinite(meta.writtenAt)
      ) {
        // meta 损坏：直接清掉当前 dir 的两条条目，不重入 runSerialized 队列
        // （read 不在队列里，delete 进队列会让后续 read 也排队，反而慢）。
        try {
          await dir.removeEntry(this.metaKey(contentHash))
        } catch {}

        try {
          await dir.removeEntry(this.blobKey(contentHash))
        } catch {}

        // lastTouched 走 runSerialized：与 writeInternal / pruneInternal 的 set 操作保持 FIFO，
        // 避免 read 的同步 delete 落到 writeInternal 的 set 之后把刚写入的条目抹掉。
        void this.runSerialized(() => {
          this.lastTouched.delete(contentHash)

          return Promise.resolve()
        })

        return null
      }

      const blobHandle = await dir.getFileHandle(this.blobKey(contentHash))
      const blobFile = await blobHandle.getFile()

      if (blobFile.size !== meta.size) {
        // size mismatch：调用方拿到的字节将与后续 prune 的字节预算计数不一致；
        // 走 try/catch 容忍并发 OPFS 锁异常，下一次 read 会再次尝试清理。
        try {
          await dir.removeEntry(this.blobKey(contentHash))
        } catch {}

        try {
          await dir.removeEntry(this.metaKey(contentHash))
        } catch {}

        // 理由同上：lastTouched.delete 必须排在 writeInternal.set 之后执行，否则会把
        // 同期 writeInternal 已写入的新条目抹掉，造成下一轮 touch 误判 LRU 顺序。
        void this.runSerialized(() => {
          this.lastTouched.delete(contentHash)

          return Promise.resolve()
        })

        return null
      }

      const buffer = await blobFile.arrayBuffer()
      // 直接走 runSerialized：touch 必须串行，避免两个并发 read 都过 last<1s 闸门
      // 把后到的 meta.writtenAt 覆盖掉先到的（LRU 毒化）。
      void this.runSerialized(() => this.touchInternal(dir, contentHash))

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

    // 单文件大小守卫：超过缓存上限的文件不落盘，避免写完立刻被本次 prune 删除
    if (bytes.byteLength > this.maxBytes) {
      log.warn(this.logTag, `File size ${bytes.byteLength} exceeds maxBytes ${this.maxBytes}; skipping cache`)

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

      this.lastTouched.delete(contentHash)
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

        this.lastTouched.clear()
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

  private async touchInternal(dir: FileSystemDirectoryHandle, contentHash: string): Promise<void> {
    const now = Date.now()
    const last = this.lastTouched.get(contentHash) ?? 0

    if (now - last < 1000) {
      return
    }

    // 闸门一过立即占位：后续并发 read 会因 last≥now-1000 而直接 short-circuit，
    // 避免两个并发 touch 各自跑完 meta 写而互相覆盖 writtenAt（LRU 毒化）。
    this.lastTouched.set(contentHash, now)

    try {
      // 确认 blob 仍在（prune 后可能已被淘汰）——否则续 meta 会让幽灵条目
      // 误导下一次 prune 把有效条目也清掉。
      await dir.getFileHandle(this.blobKey(contentHash))

      const metaHandle = await dir.getFileHandle(this.metaKey(contentHash))
      const metaFile = await metaHandle.getFile()
      const meta = JSON.parse(await metaFile.text()) as Partial<MetaFile>

      if (meta.version !== SCHEMA_VERSION) {
        return
      }

      meta.writtenAt = now
      const writable = await metaHandle.createWritable()

      try {
        await writable.write(JSON.stringify(meta))
      } finally {
        await writable.close().catch(() => {})
      }
    } catch {}
  }

  private async writeInternal(contentHash: string, bytes: ArrayBuffer): Promise<void> {
    const dir = await this.getDir()

    if (!dir) {
      return
    }

    let blobWritten = false
    let metaWritten = false

    try {
      const blobHandle = await dir.getFileHandle(this.blobKey(contentHash), { create: true })
      const blobWritable = await blobHandle.createWritable()

      try {
        await blobWritable.write(bytes)
      } finally {
        await blobWritable.close().catch(() => {})
      }

      blobWritten = true

      const meta: MetaFile = {
        size: bytes.byteLength,
        version: SCHEMA_VERSION,
        writtenAt: Date.now()
      }

      const metaHandle = await dir.getFileHandle(this.metaKey(contentHash), { create: true })
      const metaWritable = await metaHandle.createWritable()

      try {
        await metaWritable.write(JSON.stringify(meta))
      } finally {
        await metaWritable.close().catch(() => {})
      }

      metaWritten = true
      this.lastTouched.set(contentHash, meta.writtenAt)

      await this.pruneInternal(dir, this.maxFiles, this.maxBytes)
    } catch (err) {
      log.warn(this.logTag, 'write failed; pruning and cleaning up partials', err)

      if (blobWritten || metaWritten) {
        try {
          await dir.removeEntry(this.blobKey(contentHash))
        } catch {}

        try {
          await dir.removeEntry(this.metaKey(contentHash))
        } catch {}

        this.lastTouched.delete(contentHash)
      }

      // 仅在真实配额溢出时执行激进清理，淘汰旧文件解楔；普通 I/O 或路径错误不误伤其他有效条目
      const isQuotaError =
        err instanceof Error && (err.name === 'QuotaExceededError' || err.name === 'NS_ERROR_DOM_QUOTA_REACHED')

      if (isQuotaError) {
        await this.pruneInternal(dir, Math.max(1, this.maxFiles - 2), this.maxBytes * 0.75).catch(() => {})
      }
    }
  }

  private async pruneInternal(dir: FileSystemDirectoryHandle, maxFiles: number, maxBytes: number): Promise<void> {
    try {
      const metaEntries: { hash: string; size: number; writtenAt: number }[] = []
      const blobHashes = new Set<string>()
      const metaHashes = new Set<string>()
      const handles: FileSystemHandle[] = []

      // 先收集所有句柄，避免在遍历 AsyncIterator 的过程中删除条目导致迭代器失效
      for await (const handle of (dir as unknown as { values: () => AsyncIterable<FileSystemHandle> }).values()) {
        handles.push(handle)
      }

      for (const handle of handles) {
        if (handle.kind === 'file' && typeof handle.name === 'string') {
          if (this.isMetaFile(handle.name)) {
            const hash = this.hashFromMetaFile(handle.name)

            if (!hash) {
              continue
            }

            metaHashes.add(hash)

            try {
              const file = await (handle as FileSystemFileHandle).getFile()
              const meta = JSON.parse(await file.text()) as Partial<MetaFile>

              if (
                meta.version === SCHEMA_VERSION &&
                typeof meta.size === 'number' &&
                Number.isFinite(meta.size) &&
                typeof meta.writtenAt === 'number' &&
                Number.isFinite(meta.writtenAt) &&
                meta.size >= 0
              ) {
                metaEntries.push({
                  hash,
                  size: meta.size,
                  writtenAt: meta.writtenAt
                })
              } else {
                try {
                  await dir.removeEntry(handle.name)
                } catch {}
              }
            } catch {
              try {
                await dir.removeEntry(handle.name)
              } catch {}
            }
          } else if (this.isBlobFile(handle.name)) {
            const hash = this.hashFromBlobFile(handle.name)

            if (hash) {
              blobHashes.add(hash)
            }
          }
        }
      }

      for (const blobHash of blobHashes) {
        if (!metaHashes.has(blobHash)) {
          try {
            await dir.removeEntry(this.blobKey(blobHash))
          } catch {}
        }
      }

      const validEntries: typeof metaEntries = []
      let totalBytes = 0

      for (const entry of metaEntries) {
        if (blobHashes.has(entry.hash)) {
          try {
            const blobHandle = await dir.getFileHandle(this.blobKey(entry.hash))
            const blobFile = await blobHandle.getFile()

            if (blobFile.size === entry.size) {
              validEntries.push(entry)
              totalBytes += entry.size
            } else {
              try {
                await dir.removeEntry(this.blobKey(entry.hash))
                await dir.removeEntry(this.metaKey(entry.hash))
              } catch {}
            }
          } catch {
            try {
              await dir.removeEntry(this.metaKey(entry.hash))
            } catch {}
          }
        } else {
          try {
            await dir.removeEntry(this.metaKey(entry.hash))
          } catch {}

          this.lastTouched.delete(entry.hash)
        }
      }

      if (validEntries.length <= maxFiles && totalBytes <= maxBytes) {
        return
      }

      validEntries.sort((a, b) => a.writtenAt - b.writtenAt)

      while (validEntries.length > maxFiles || totalBytes > maxBytes) {
        const oldest = validEntries.shift()

        if (!oldest) {
          break
        }

        try {
          await dir.removeEntry(this.blobKey(oldest.hash))
        } catch {}

        try {
          await dir.removeEntry(this.metaKey(oldest.hash))
        } catch {}

        this.lastTouched.delete(oldest.hash)
        totalBytes -= oldest.size
      }
    } catch (err) {
      log.warn(this.logTag, 'prune failed:', err)
    }
  }
}
