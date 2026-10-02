'use strict'

const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const args = process.argv.slice(2)
const targets = { x64: 'x86_64-pc-windows-msvc', arm64: 'aarch64-pc-windows-msvc' }
let target = targets[process.arch]

if (args.length) {
  if (args.length !== 2 || args[0] !== '--target' || !Object.values(targets).includes(args[1])) {
    throw new Error('Usage: build-desktop-host.cjs [--target x86_64-pc-windows-msvc|aarch64-pc-windows-msvc]')
  }

  target = args[1]
}

if (!target) {
  throw new Error(`Unsupported Windows desktop host architecture: ${process.arch}`)
}

const clientRoot = path.resolve(__dirname, '..')
const nativeRoot = path.join(clientRoot, 'native', 'desktop-host')
const targetRoot = path.join(nativeRoot, 'target')
const result = spawnSync('cargo', ['build', '--locked', '--release', '--target', target, '--target-dir', targetRoot], {
  cwd: nativeRoot,
  stdio: 'inherit',
  timeout: 10 * 60_000,
  windowsHide: true
})

if (result.error) {
  const reason = result.error.code === 'ENOENT' ? 'Rust cargo is required' : 'Windows desktop host build failed'
  throw new Error(`${reason}: ${result.error.message}`)
}

if (result.status !== 0) {
  process.exit(result.status ?? 1)
}

const source = path.join(targetRoot, target, 'release', 'desktop-host.exe')
const arch = Object.keys(targets).find(arch => targets[arch] === target)
const packaged = path.join(clientRoot, 'build', arch, 'desktop-host.exe')
const destination = path.join(clientRoot, 'build', 'desktop-host.exe')
fs.mkdirSync(path.dirname(packaged), { recursive: true })
fs.copyFileSync(source, packaged)
fs.copyFileSync(source, destination)
console.log(`Windows desktop host built for ${target}: ${packaged}`)
