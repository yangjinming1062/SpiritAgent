import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { errorMessage } from '../shared/utils'

export const DEFAULT_FETCH_TIMEOUT_MS = 15_000
export const DATA_URL_READ_MAX_BYTES = 16 * 1024 * 1024

export const DEFAULT_CSP_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: spiritagent-media: https:",
  "media-src 'self' data: blob: spiritagent-media: https:",
  "connect-src 'self' data: blob: ws://127.0.0.1:* ws://localhost:* http://127.0.0.1:* http://localhost:* http: https: ws: wss: spiritagent-media:",
  "font-src 'self' data:",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'none'"
].join('; ')

// Dev-only：Vite 的 @vitejs/plugin-react 会把 React Fast Refresh preamble 作为
// 内联脚本注入 HTML；DEFAULT_CSP_POLICY 不允许 inline，会被拦下导致
// "can't detect preamble" 白屏（同时 HMR 反复重试烧 CPU）。HMR websocket
// 走 127.0.0.1:5174，已被 connect-src 的 ws/http 通配覆盖，无需扩白名单。
// 生产仍用 DEFAULT_CSP_POLICY 锁紧——dev 放开只影响受信任本地源。
export const DEV_CSP_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: spiritagent-media: https:",
  "media-src 'self' data: blob: spiritagent-media: https:",
  "connect-src 'self' data: blob: ws://127.0.0.1:* ws://localhost:* http://127.0.0.1:* http://localhost:* http: https: ws: wss: spiritagent-media:",
  "font-src 'self' data:",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'none'"
].join('; ')

// 头像 / 精灵 / 衣橱生成：供应商调用 + Pillow 重编码 + 关键帧写入通常要 15–25 秒，
// 默认 15 秒会在后端返回 201 之前就超时，所以这里放宽。
const AVATAR_FETCH_TIMEOUT_MS = 120_000

const SAFE_ENV_SUFFIXES: Set<string> = new Set(['dist', 'example', 'sample', 'template'])
const SENSITIVE_EXTENSIONS: Set<string> = new Set(['.kdbx', '.p12', '.pem', '.pfx'])

export function resolveTimeoutMs(timeoutMs?: null | number | string, fallbackMs = DEFAULT_FETCH_TIMEOUT_MS): number {
  if (timeoutMs !== undefined && timeoutMs !== null) {
    const parsed = Number(timeoutMs)

    if (Number.isFinite(parsed) && parsed > 0) {
      return Math.round(parsed)
    }
  }

  const fallbackNum = Number(fallbackMs)

  return Number.isFinite(fallbackNum) && fallbackNum > 0 ? Math.round(fallbackNum) : DEFAULT_FETCH_TIMEOUT_MS
}

const AVATAR_SLOW_PATH_PATTERN =
  /^\/api\/(?:companion\/(?:avatar(?:\/from-image|\/\d+\/fullbody\/(?:samples|front|back|confirm-front))?|sprite)|media\/(?:image_gen|video_gen))$/i

// 仅 POST 路径——读路径只是数据库查询，不涉及供应商调用。
export function resolvePathTimeoutMs(
  pathStr?: null | string,
  method?: null | string,
  fallbackMs = DEFAULT_FETCH_TIMEOUT_MS
): number {
  const isSlowPost =
    String(method || 'GET').toUpperCase() === 'POST' &&
    typeof pathStr === 'string' &&
    AVATAR_SLOW_PATH_PATTERN.test(pathStr)

  return isSlowPost ? AVATAR_FETCH_TIMEOUT_MS : resolveTimeoutMs(undefined, fallbackMs)
}

export interface SafeStorageApi {
  decryptString?: (encrypted: Buffer) => string
  encryptString: (plainText: string) => Buffer
  isEncryptionAvailable: () => boolean
}

function sensitiveFileBlockReason(filePath: string): null | string {
  const normalized = String(filePath || '')
    .replace(/\\/g, '/')
    .toLowerCase()

  const basename = path.basename(normalized)
  const ext = path.extname(basename)

  if (!basename) {
    return null
  }

  if (normalized.includes('/.ssh/')) {
    return 'SSH key/config files are blocked.'
  }

  if (normalized.includes('/.gnupg/')) {
    return 'GPG key material is blocked.'
  }

  if (normalized.endsWith('/.aws/credentials')) {
    return 'AWS credential files are blocked.'
  }

  if (basename === '.env') {
    return '.env files are blocked because they commonly contain secrets.'
  }

  if (basename.startsWith('.env.')) {
    const suffix = basename.slice('.env.'.length)

    if (!SAFE_ENV_SUFFIXES.has(suffix)) {
      return `${basename} is blocked because it appears to contain environment secrets.`
    }
  }

  if (/^id_(rsa|dsa|ecdsa|ed25519)(?:\..+)?$/.test(basename) && !basename.endsWith('.pub')) {
    return 'SSH private key files are blocked.'
  }

  if (SENSITIVE_EXTENSIONS.has(ext)) {
    return `${ext} key/certificate files are blocked.`
  }

  if (basename === '.npmrc' || basename === '.netrc' || basename === '.pypirc') {
    return `${basename} is blocked because it may include auth credentials.`
  }

  return null
}

function resolveRequestedFilePath(filePath: string, baseDir = process.cwd(), purpose = 'File read'): string {
  const raw = String(filePath || '').trim()

  if (!raw) {
    throw new Error(`${purpose} failed: file path is required.`)
  }

  if (raw.includes('\0')) {
    throw new Error(`${purpose} failed: file path is invalid.`)
  }

  if (/^file:/i.test(raw)) {
    try {
      return fileURLToPath(raw)
    } catch {
      throw new Error(`${purpose} failed: file URL is invalid.`)
    }
  }

  const resolvedBase = path.resolve(String(baseDir || process.cwd()))

  return path.resolve(resolvedBase, raw)
}

interface ResolveReadableFileOptions {
  baseDir?: string
  blockSensitive?: boolean
  maxBytes?: null | number
  purpose?: string
}

export async function resolveReadableFileForIpc(
  filePath: string,
  options: ResolveReadableFileOptions = {}
): Promise<{ resolvedPath: string; stat: fs.Stats }> {
  const purpose = String(options.purpose || 'File read')
  const resolvedPath = resolveRequestedFilePath(filePath, options.baseDir, purpose)

  if (options.blockSensitive !== false) {
    const blockReason = sensitiveFileBlockReason(resolvedPath)

    if (blockReason) {
      throw new Error(`${purpose} blocked for sensitive file: ${blockReason}`)
    }
  }

  let stat: fs.Stats

  try {
    stat = await fs.promises.stat(resolvedPath)
  } catch (error: unknown) {
    const code =
      typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string' ? error.code : ''

    if (code === 'ENOENT' || code === 'ENOTDIR') {
      throw new Error(`${purpose} failed: file does not exist.`)
    }

    throw new Error(`${purpose} failed: ${errorMessage(error)}`)
  }

  if (stat.isDirectory()) {
    throw new Error(`${purpose} failed: path points to a directory.`)
  }

  if (!stat.isFile()) {
    throw new Error(`${purpose} failed: only regular files can be read.`)
  }

  if (options.blockSensitive !== false) {
    const realPath = await fs.promises.realpath(resolvedPath)

    if (realPath !== resolvedPath) {
      const realBlockReason = sensitiveFileBlockReason(realPath)

      if (realBlockReason) {
        throw new Error(`${purpose} blocked for sensitive file (symlink target): ${realBlockReason}`)
      }
    }
  }

  const maxBytes =
    typeof options.maxBytes === 'number' && Number.isFinite(options.maxBytes) && options.maxBytes > 0
      ? options.maxBytes
      : null

  if (maxBytes && stat.size > maxBytes) {
    throw new Error(`${purpose} failed: file is too large (${stat.size} bytes; limit ${maxBytes} bytes).`)
  }

  try {
    await fs.promises.access(resolvedPath, fs.constants.R_OK)
  } catch {
    throw new Error(`${purpose} failed: file is not readable.`)
  }

  return { resolvedPath, stat }
}
