'use strict'

const fs = require('node:fs')
const { buildDesktopHost, targets } = require('./build-desktop-host.cjs')

function cleanStaleAppOutDir(appOutDir) {
  if (!appOutDir || typeof appOutDir !== 'string') {
    return false
  }
  if (!fs.existsSync(appOutDir)) {
    return false
  }
  // 半成品目录会阻断 electron-builder 下次暂存；Windows 的杀毒扫描可能暂时占用它。
  fs.rmSync(appOutDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  return true
}

exports.default = async function beforePack(context) {
  if (context.electronPlatformName === 'win32') {
    const arch = { 1: 'x64', 3: 'arm64' }[context.arch]
    if (!arch) throw new Error(`Unsupported Windows desktop architecture: ${context.arch}`)
    buildDesktopHost(targets[arch])
  }
  const appOutDir = context && context.appOutDir
  try {
    if (cleanStaleAppOutDir(appOutDir)) {
      console.log(`[before-pack] removed stale unpacked dir before staging: ${appOutDir}`)
    }
  } catch (err) {
    console.warn(`[before-pack] could not clean ${appOutDir} (${err.message}); continuing`)
  }
}
