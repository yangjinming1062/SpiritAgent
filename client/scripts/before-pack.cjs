'use strict'

const fs = require('node:fs')

/**
 * electron-builder beforePack hook: removes a stale unpacked app directory (`appOutDir`).
 *
 * An interrupted pack leaves `release/<platform>-unpacked/` half-staged: the Chromium payload is
 * present but the `electron` binary is missing. The next pack skips re-copying it and fails with
 * `ENOENT: rename '.../electron' -> '.../SpiritAgent'`. The directory is a pure build artifact that
 * electron-builder recreates on every pack, so wiping it first is safe on both macOS and Windows.
 *
 * Cleanup is best-effort: a failure is logged and the build continues, at worst hitting the
 * original ENOENT.
 */

function cleanStaleAppOutDir(appOutDir) {
  if (!appOutDir || typeof appOutDir !== 'string') {
    return false
  }
  if (!fs.existsSync(appOutDir)) {
    return false
  }
  // Recursive + force so a half-written tree (read-only bits, partial files)
  // can't block the wipe. retry/maxRetries rides out transient EBUSY on
  // Windows where an AV/indexer may briefly hold a handle.
  fs.rmSync(appOutDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  return true
}

exports.default = async function beforePack(context) {
  const appOutDir = context && context.appOutDir
  try {
    if (cleanStaleAppOutDir(appOutDir)) {
      console.log(`[before-pack] removed stale unpacked dir before staging: ${appOutDir}`)
    }
  } catch (err) {
    // Never fail the build over cleanup; surface why so a genuinely stuck
    // directory (permissions, mount) is still diagnosable.
    console.warn(`[before-pack] could not clean ${appOutDir} (${err.message}); continuing`)
  }
}
