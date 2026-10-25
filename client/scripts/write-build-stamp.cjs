'use strict'

const fs = require('fs')
const path = require('path')
const { execSync } = require('child_process')

const STAMP_SCHEMA_VERSION = 1

const DESKTOP_ROOT = path.resolve(__dirname, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..')
const OUT_DIR = path.join(DESKTOP_ROOT, 'build')
const OUT_FILE = path.join(OUT_DIR, 'install-stamp.json')

function tryExec(cmd) {
  try {
    return execSync(cmd, { cwd: REPO_ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch {
    return null
  }
}

function fromCI() {
  const sha = process.env.GITHUB_SHA
  if (!sha) return null
  const branch = process.env.GITHUB_REF_NAME || process.env.GITHUB_HEAD_REF || null
  return {
    commit: sha,
    branch,
    dirty: false,
    source: 'ci'
  }
}

function fromLocalGit() {
  const sha = tryExec('git rev-parse HEAD')
  if (!sha) return null
  const branch = tryExec('git rev-parse --abbrev-ref HEAD')
  // 只记录受 Git 跟踪文件的改动，未跟踪的临时目录不影响构建来源。
  const status = tryExec('git status --porcelain -uno')
  if (status === null) {
    console.warn('[write-build-stamp] WARNING: `git status` failed; marking the build dirty.')
  }
  const dirty = status === null || status.length > 0
  return {
    commit: sha,
    branch: branch === 'HEAD' ? null : branch,
    dirty,
    source: 'local'
  }
}

function main() {
  const stamp = fromCI() || fromLocalGit()
  if (!stamp || !stamp.commit) {
    console.error(
      '[write-build-stamp] ERROR: could not determine git commit.\n' +
        '  - $GITHUB_SHA not set\n' +
        '  - `git rev-parse HEAD` failed at ' +
        REPO_ROOT +
        '\n' +
        'Packaged builds record the git commit they were built from.\n' +
        'Run from a git checkout or set $GITHUB_SHA explicitly.'
    )
    process.exit(1)
  }

  if (stamp.dirty) {
    console.warn(
      '[write-build-stamp] WARNING: working tree is dirty.\n' +
        '  Stamping ' +
        stamp.commit.slice(0, 12) +
        ' but the packaged code may differ from that commit.\n' +
        '  Commit your changes before publishing this build.'
    )
  }

  const payload = {
    schemaVersion: STAMP_SCHEMA_VERSION,
    commit: stamp.commit,
    branch: stamp.branch,
    builtAt: new Date().toISOString(),
    dirty: stamp.dirty,
    source: stamp.source
  }

  fs.mkdirSync(OUT_DIR, { recursive: true })
  fs.writeFileSync(OUT_FILE, JSON.stringify(payload, null, 2) + '\n', 'utf8')
  console.log(
    '[write-build-stamp] wrote ' +
      path.relative(REPO_ROOT, OUT_FILE) +
      ' -> ' +
      stamp.commit.slice(0, 12) +
      (stamp.branch ? ' (' + stamp.branch + ')' : '') +
      (stamp.dirty ? ' [DIRTY]' : '')
  )
}

main()
