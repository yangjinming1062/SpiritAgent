'use strict'

const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const { buildDesktopTrayStyle } = require('./build-desktop-tray-style.cjs')
const { assertStaticRuntime, readWindowsExecutableArch } = require('./windows-native-image.cjs')

const targets = { x64: 'x86_64-pc-windows-msvc', arm64: 'aarch64-pc-windows-msvc' }

function windowsArchFromBuilder(arch) {
  const windowsArch = { 1: 'x64', 3: 'arm64' }[arch]
  if (!windowsArch) throw new Error(`Unsupported Windows desktop architecture: ${arch}`)
  return windowsArch
}

function nativeSourceModified(nativeRoot) {
  const sourceFiles = ['Cargo.toml', 'Cargo.lock'].map(file => path.join(nativeRoot, file))
  sourceFiles.push(__filename)
  const visit = directory => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name)
      if (entry.isDirectory()) visit(file)
      else if (entry.isFile() && entry.name.endsWith('.rs')) sourceFiles.push(file)
    }
  }
  visit(path.join(nativeRoot, 'src'))
  return Math.max(...sourceFiles.map(file => fs.statSync(file).mtimeMs))
}

function buildDesktopHost(target = targets[process.arch], { ifNeeded = false } = {}) {
  const arch = Object.keys(targets).find(arch => targets[arch] === target)
  if (!arch) throw new Error(`Unsupported Windows desktop host target: ${target ?? process.arch}`)

  const clientRoot = path.resolve(__dirname, '..')
  const nativeRoot = path.join(clientRoot, 'desktop-windows')
  const targetRoot = path.join(nativeRoot, 'target')
  const packaged = path.join(clientRoot, 'build', arch, 'desktop-host.exe')
  buildDesktopTrayStyle(arch)

  if (ifNeeded && fs.existsSync(packaged)) {
    try {
      if (
        readWindowsExecutableArch(packaged) === arch &&
        fs.statSync(packaged).mtimeMs >= nativeSourceModified(nativeRoot)
      ) {
        assertStaticRuntime(packaged)
        console.log(`[desktop-host] reusing ${arch} development helper: ${packaged}`)
        return
      }
    } catch (error) {
      console.warn(`[desktop-host] development helper must be rebuilt: ${error.message}`)
    }
  }

  const cargoHome = process.env.CARGO_HOME || (process.env.USERPROFILE && path.join(process.env.USERPROFILE, '.cargo'))
  const installedCargo = cargoHome && path.join(cargoHome, 'bin', process.platform === 'win32' ? 'cargo.exe' : 'cargo')
  const cargo = installedCargo && fs.existsSync(installedCargo) ? installedCargo : 'cargo'
  const env = { ...process.env }
  if (env.CARGO_ENCODED_RUSTFLAGS !== undefined)
    env.CARGO_ENCODED_RUSTFLAGS += `${env.CARGO_ENCODED_RUSTFLAGS ? '\u001f' : ''}-C\u001ftarget-feature=+crt-static`
  else env.RUSTFLAGS = `${env.RUSTFLAGS || ''} -C target-feature=+crt-static`.trim()
  const result = spawnSync(cargo, ['build', '--locked', '--release', '--target', target, '--target-dir', targetRoot], {
    cwd: nativeRoot,
    stdio: 'inherit',
    timeout: 10 * 60_000,
    windowsHide: true,
    env
  })

  if (result.error) {
    if (result.error.code === 'ENOENT') {
      throw new Error(
        '未找到 Rust Cargo。Windows 桌面模式需要 Rust 1.85 以上、Visual Studio C++ 构建工具和 Windows SDK。'
      )
    }
    throw new Error(`Windows 桌面组件编译失败：${result.error.message}`)
  }
  if (result.status !== 0) {
    throw new Error(`Windows 桌面组件编译失败（退出码 ${result.status ?? '未知'}），请检查上方 Rust 和 MSVC 诊断。`)
  }

  const source = path.join(targetRoot, target, 'release', 'desktop-host.exe')
  if (readWindowsExecutableArch(source) !== arch)
    throw new Error(`Windows desktop helper architecture does not match ${arch}`)
  assertStaticRuntime(source)
  fs.mkdirSync(path.dirname(packaged), { recursive: true })
  fs.copyFileSync(source, packaged)
  console.log(`Windows desktop host built for ${target}: ${packaged}`)
}

module.exports = { buildDesktopHost, targets, windowsArchFromBuilder }

if (require.main === module) {
  try {
    const args = process.argv.slice(2)
    if (args.length && (args.length !== 2 || args[0] !== '--target' || !Object.values(targets).includes(args[1]))) {
      throw new Error('Usage: build-desktop-host.cjs [--target x86_64-pc-windows-msvc|aarch64-pc-windows-msvc]')
    }
    buildDesktopHost(args[1])
  } catch (error) {
    console.error(`[desktop-host] ${error.message}`)
    process.exitCode = 1
  }
}
