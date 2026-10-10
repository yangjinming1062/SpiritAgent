import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { errorMessage } from '../shared/utils'

import { assertUserSelectedPath } from './user-selected-paths'

export const DEFAULT_FETCH_TIMEOUT_MS = 15_000
export const DATA_URL_READ_MAX_BYTES = 16 * 1024 * 1024

function buildCspPolicy(scriptSrc: string): string {
  return [
    "default-src 'self'",
    `script-src ${scriptSrc}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",
    "media-src 'self' data: blob: https:",
    "connect-src 'self' data: blob: ws://127.0.0.1:* ws://localhost:* http://127.0.0.1:* http://localhost:* http: https: ws: wss:",
    "font-src 'self' data:",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'none'"
  ].join('; ')
}

export const DEFAULT_CSP_POLICY = buildCspPolicy("'self'")

// Dev-only：Vite 的 React Fast Refresh preamble 以内联脚本注入 HTML，DEFAULT_CSP_POLICY 不允许 inline 会导致白屏（HMR 重试还会烧 CPU）；HMR websocket 已被 connect-src 通配覆盖。生产仍用 DEFAULT_CSP_POLICY 锁紧。
export const DEV_CSP_POLICY = buildCspPolicy("'self' 'unsafe-inline' 'unsafe-eval'")

// 本地生图任务最多等待 24 小时，同步生成另留提示词、下载、透明化和落盘预算；超时不自动重发。
const IMAGE_GENERATION_FETCH_TIMEOUT_MS = 25 * 60 * 60_000
// 外观提示词、上传采纳与确认沿用独立预算，不随生图任务等待时长放宽。
const IMAGE_PROCESSING_FETCH_TIMEOUT_MS = 15 * 60_000
// 候选图身体特征分析同步等待视觉模型，后端上限是 avatar_service 的 _CARD_ANALYSIS_TIMEOUT（180 秒），另留读写余量。
const CANDIDATE_ANALYSIS_FETCH_TIMEOUT_MS = 210_000
// 提示词整合与自备图采纳涉及模型调用、图片校验与落盘，确认类请求还可能等待用户级形象任务锁，默认 15 秒不够。
const AVATAR_FETCH_TIMEOUT_MS = 120_000

const IMAGE_GENERATION_PATH_PATTERN =
  /^\/api\/companion\/(?:avatar(?:\/from-image|\/\d+\/fullbody\/reference)?|outfits(?:\/\d+\/regenerate)?)$/i

// 全身参考上传在确认身份后还会同步分析身体特征；外观的采纳、确认和提示词只处理已有图片或文字。
const IMAGE_PROCESSING_PATH_PATTERN =
  /^\/api\/companion\/(?:outfits(?:\/(?:prompt|adopt)|\/\d+\/(?:prompt|adopt|confirm))|avatar\/\d+\/fullbody\/reference\/adopt)$/i

const CANDIDATE_ANALYSIS_PATH_PATTERN = /^\/api\/companion\/avatar\/\d+\/fullbody\/candidate\/\d+\/analyze$/i

const AVATAR_SLOW_PATH_PATTERN =
  /^\/api\/companion\/(?:avatar(?:\/(?:prompt|adopt)|\/\d+\/fullbody\/(?:reference\/prompt|confirm|candidate\/\d+\/accept))|portrait\/confirm|scenes\/(?:prompt|adopt|\d+\/adopt))$/i

const DESKTOP_VIDEO_PROMPT_PATH_PATTERN = /^\/api\/companion\/desktop-videos\/actions\/\d+\/external-prompt$/i

const SAFE_ENV_SUFFIXES: Set<string> = new Set(['dist', 'example', 'sample', 'template'])
const SENSITIVE_EXTENSIONS: Set<string> = new Set(['.kdbx', '.p12', '.pem', '.pfx'])

function positiveRounded(value: unknown): null | number {
  const parsed = Number(value)

  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : null
}

export function resolveTimeoutMs(timeoutMs?: null | number | string, fallbackMs = DEFAULT_FETCH_TIMEOUT_MS): number {
  return positiveRounded(timeoutMs) ?? positiveRounded(fallbackMs) ?? DEFAULT_FETCH_TIMEOUT_MS
}

// 仅 POST 路径——读路径只是数据库查询，不涉及供应商调用。
export function resolvePathTimeoutMs(
  pathStr?: null | string,
  method?: null | string,
  fallbackMs = DEFAULT_FETCH_TIMEOUT_MS
): number {
  const isPost = String(method || 'GET').toUpperCase() === 'POST' && typeof pathStr === 'string'

  if (isPost && IMAGE_GENERATION_PATH_PATTERN.test(pathStr)) {
    return IMAGE_GENERATION_FETCH_TIMEOUT_MS
  }

  if (isPost && IMAGE_PROCESSING_PATH_PATTERN.test(pathStr)) {
    return IMAGE_PROCESSING_FETCH_TIMEOUT_MS
  }

  if (isPost && CANDIDATE_ANALYSIS_PATH_PATTERN.test(pathStr)) {
    return CANDIDATE_ANALYSIS_FETCH_TIMEOUT_MS
  }

  if (isPost && /^\/api\/sessions\/messages\/\d+\/voice\/\d+$/.test(pathStr)) {
    return 150_000
  }

  const isSlowPost = isPost && AVATAR_SLOW_PATH_PATTERN.test(pathStr)

  if (isPost && DESKTOP_VIDEO_PROMPT_PATH_PATTERN.test(pathStr)) {
    return AVATAR_FETCH_TIMEOUT_MS
  }

  return isSlowPost ? AVATAR_FETCH_TIMEOUT_MS : resolveTimeoutMs(fallbackMs)
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

  return path.resolve(raw)
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

  const { maxBytes } = options

  if (typeof maxBytes === 'number' && maxBytes > 0 && stat.size > maxBytes) {
    throw new Error(`${purpose} failed: file is too large (${stat.size} bytes; limit ${maxBytes} bytes).`)
  }

  try {
    await fs.promises.access(resolvedPath, fs.constants.R_OK)
  } catch {
    throw new Error(`${purpose} failed: file is not readable.`)
  }

  return { resolvedPath, stat }
}

/** 读取渲染层经选择器或拖拽登记过的文件：先核对白名单，再做敏感路径、类型、大小与可读性校验。 */
export async function readUserSelectedFile(
  filePath: string,
  options: { maxBytes: number; purpose: string }
): Promise<{ data: Buffer; resolvedPath: string }> {
  assertUserSelectedPath(filePath, options.purpose)

  const { resolvedPath } = await resolveReadableFileForIpc(filePath, options)

  return { data: await fs.promises.readFile(resolvedPath), resolvedPath }
}
