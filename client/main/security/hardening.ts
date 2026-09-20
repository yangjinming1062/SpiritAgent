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

// 头像 / 精灵生成：供应商调用 + Pillow 重编码 + 关键帧写入通常要 15–25 秒，
// 默认 15 秒会在后端返回 201 之前就超时，所以这里放宽。
const AVATAR_FETCH_TIMEOUT_MS = 120_000
// 外观列表同步请求包含供应商回退；单个供应商默认可等待 300 秒，另需下载与落盘。
const OUTFIT_FETCH_TIMEOUT_MS = 15 * 60_000

// 自备图的 prompt 端点含 LLM 往返（身体结构判断 / 参考图整合 / 房间 brief），adopt 含
// 8MiB 图片校验与落盘：与各自生成端点共用同一放宽档。
const OUTFIT_GENERATION_PATH_PATTERN =
  /^\/api\/companion\/outfits(?:\/(?:prompt|adopt)|\/\d+\/(?:regenerate|prompt|adopt|confirm))?$/i

const AVATAR_SLOW_PATH_PATTERN =
  /^\/api\/(?:companion\/(?:avatar(?:\/(?:from-image|prompt|adopt)|\/\d+\/fullbody\/(?:reference|confirm)(?:\/(?:prompt|adopt))?)?|sprite|room\/(?:prompt|adopt|\d+\/adopt))|media\/(?:image_gen|video_gen))$/i

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

// 仅 POST 路径——读路径只是数据库查询，不涉及供应商调用。
export function resolvePathTimeoutMs(
  pathStr?: null | string,
  method?: null | string,
  fallbackMs = DEFAULT_FETCH_TIMEOUT_MS
): number {
  const isPost = String(method || 'GET').toUpperCase() === 'POST' && typeof pathStr === 'string'

  if (isPost && OUTFIT_GENERATION_PATH_PATTERN.test(pathStr)) {
    return OUTFIT_FETCH_TIMEOUT_MS
  }

  if (isPost && /^\/api\/sessions\/messages\/\d+\/voice\/\d+$/.test(pathStr)) {
    return 150_000
  }

  const isSlowPost = isPost && AVATAR_SLOW_PATH_PATTERN.test(pathStr)

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

function resolveRequestedFilePath(filePath: string, purpose = 'File read'): string {
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

  return path.resolve(process.cwd(), raw)
}

interface ResolveReadableFileOptions {
  maxBytes?: null | number
  purpose?: string
}

export async function resolveReadableFileForIpc(
  filePath: string,
  options: ResolveReadableFileOptions = {}
): Promise<{ resolvedPath: string; stat: fs.Stats }> {
  const purpose = String(options.purpose || 'File read')
  const resolvedPath = resolveRequestedFilePath(filePath, purpose)

  const blockReason = sensitiveFileBlockReason(resolvedPath)

  if (blockReason) {
    throw new Error(`${purpose} blocked for sensitive file: ${blockReason}`)
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

  const realPath = await fs.promises.realpath(resolvedPath)

  if (realPath !== resolvedPath) {
    const realBlockReason = sensitiveFileBlockReason(realPath)

    if (realBlockReason) {
      throw new Error(`${purpose} blocked for sensitive file (symlink target): ${realBlockReason}`)
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
