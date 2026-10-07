'use strict'

const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const targets = { x64: 'x86_64-pc-windows-msvc', arm64: 'aarch64-pc-windows-msvc' }

function readWindowsExecutableArch(filePath) {
  const file = fs.openSync(filePath, 'r')
  try {
    const dos = Buffer.alloc(64)
    if (fs.readSync(file, dos, 0, dos.length, 0) !== dos.length || dos.readUInt16LE(0) !== 0x5a4d) {
      throw new Error('Windows executable has an invalid DOS header')
    }
    const offset = dos.readUInt32LE(0x3c)
    const pe = Buffer.alloc(6)
    if (
      offset > 1_048_576 ||
      fs.readSync(file, pe, 0, pe.length, offset) !== pe.length ||
      pe.readUInt32LE(0) !== 0x4550
    ) {
      throw new Error('Windows executable has an invalid PE header')
    }
    const arch = { 0x8664: 'x64', 0xaa64: 'arm64' }[pe.readUInt16LE(4)]
    if (!arch) throw new Error('Windows desktop mode requires an x64 or ARM64 Electron executable')
    return arch
  } finally {
    fs.closeSync(file)
  }
}

function assertStaticRuntime(filePath) {
  const data = fs.readFileSync(filePath)
  const requireBytes = (offset, size) => {
    if (offset < 0 || offset + size > data.length) throw new Error('Windows desktop helper has a truncated PE image')
    return offset
  }
  requireBytes(0, 64)
  const pe = data.readUInt32LE(0x3c)
  requireBytes(pe, 24)
  if (data.readUInt16LE(0) !== 0x5a4d || data.readUInt32LE(pe) !== 0x4550)
    throw new Error('Windows desktop helper has an invalid PE image')
  const optional = pe + 24
  const optionalSize = data.readUInt16LE(pe + 20)
  requireBytes(optional, optionalSize)
  if (optionalSize < 224 || data.readUInt16LE(optional) !== 0x20b)
    throw new Error('Windows desktop helper requires a PE32+ image')
  const sections = optional + optionalSize
  const sectionCount = data.readUInt16LE(pe + 6)
  requireBytes(sections, sectionCount * 40)
  const rvaOffset = (rva, size) => {
    if (rva < data.readUInt32LE(optional + 60)) return requireBytes(rva, size)
    for (let index = 0; index < sectionCount; index++) {
      const section = sections + index * 40
      const start = data.readUInt32LE(section + 12)
      const rawSize = data.readUInt32LE(section + 16)
      if (rva >= start && rva - start + size <= rawSize)
        return requireBytes(data.readUInt32LE(section + 20) + rva - start, size)
    }
    throw new Error('Windows desktop helper has an invalid PE import address')
  }
  const imports = []
  const readImports = (directory, descriptorSize, nameOffset, delayed = false) => {
    const rva = data.readUInt32LE(optional + 112 + directory * 8)
    const size = data.readUInt32LE(optional + 116 + directory * 8)
    if (!rva && !size) return
    if (!rva || size < descriptorSize) throw new Error('Windows desktop helper has an invalid PE import directory')
    for (let offset = 0; offset + descriptorSize <= size; offset += descriptorSize) {
      const descriptor = rvaOffset(rva + offset, descriptorSize)
      if (data.subarray(descriptor, descriptor + descriptorSize).every(byte => byte === 0)) return
      if (delayed && data.readUInt32LE(descriptor) !== 1)
        throw new Error('Windows desktop helper requires RVA-based delay imports')
      const nameRva = data.readUInt32LE(descriptor + nameOffset)
      let name = ''
      for (let index = 0; index < 256; index++) {
        const byte = data[rvaOffset(nameRva + index, 1)]
        if (!byte) break
        name += String.fromCharCode(byte)
        if (index === 255) throw new Error('Windows desktop helper has an invalid DLL import name')
      }
      if (!name) throw new Error('Windows desktop helper has an empty DLL import name')
      imports.push(name)
    }
    throw new Error('Windows desktop helper has an unterminated PE import directory')
  }
  readImports(1, 20, 12)
  readImports(13, 32, 4, true)
  const externalRuntime = imports.find(name =>
    /^(?:msvcp|msvcr|vcruntime|concrt)\d.*\.dll$|^(?:ucrtbased?|api-ms-win-crt-.*)\.dll$/i.test(
      path.win32.basename(name)
    )
  )
  if (externalRuntime) throw new Error(`Windows desktop helper requires an external C++ runtime: ${externalRuntime}`)
  return imports
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
  const nativeRoot = path.join(clientRoot, 'desktop-host')
  const targetRoot = path.join(nativeRoot, 'target')
  const packaged = path.join(clientRoot, 'build', arch, 'desktop-host.exe')

  if (ifNeeded && fs.existsSync(packaged)) {
    try {
      if (
        readWindowsExecutableArch(packaged) === arch &&
        fs.statSync(packaged).mtimeMs >= nativeSourceModified(nativeRoot)
      ) {
        assertStaticRuntime(packaged)
        console.log(`[desktop-host] reusing ${arch} development helper: ${packaged}`)
        return packaged
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
  return packaged
}

module.exports = { assertStaticRuntime, buildDesktopHost, readWindowsExecutableArch, targets }

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
