'use strict'

const fs = require('node:fs')
const path = require('node:path')

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
    if (!arch) throw new Error('Windows desktop mode requires an x64 or ARM64 executable or DLL')
    return arch
  } finally {
    fs.closeSync(file)
  }
}

function assertStaticRuntime(filePath) {
  const data = fs.readFileSync(filePath)
  const requireBytes = (offset, size) => {
    if (offset < 0 || offset + size > data.length) throw new Error('Windows desktop component has a truncated PE image')
    return offset
  }
  requireBytes(0, 64)
  const pe = data.readUInt32LE(0x3c)
  requireBytes(pe, 24)
  if (data.readUInt16LE(0) !== 0x5a4d || data.readUInt32LE(pe) !== 0x4550)
    throw new Error('Windows desktop component has an invalid PE image')
  const optional = pe + 24
  const optionalSize = data.readUInt16LE(pe + 20)
  requireBytes(optional, optionalSize)
  if (optionalSize < 224 || data.readUInt16LE(optional) !== 0x20b)
    throw new Error('Windows desktop component requires a PE32+ image')
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
    throw new Error('Windows desktop component has an invalid PE import address')
  }
  const imports = []
  const readImports = (directory, descriptorSize, nameOffset, delayed = false) => {
    const rva = data.readUInt32LE(optional + 112 + directory * 8)
    const size = data.readUInt32LE(optional + 116 + directory * 8)
    if (!rva && !size) return
    if (!rva || size < descriptorSize) throw new Error('Windows desktop component has an invalid PE import directory')
    for (let offset = 0; offset + descriptorSize <= size; offset += descriptorSize) {
      const descriptor = rvaOffset(rva + offset, descriptorSize)
      if (data.subarray(descriptor, descriptor + descriptorSize).every(byte => byte === 0)) return
      if (delayed && data.readUInt32LE(descriptor) !== 1)
        throw new Error('Windows desktop component requires RVA-based delay imports')
      const nameRva = data.readUInt32LE(descriptor + nameOffset)
      let name = ''
      for (let index = 0; index < 256; index++) {
        const byte = data[rvaOffset(nameRva + index, 1)]
        if (!byte) break
        name += String.fromCharCode(byte)
        if (index === 255) throw new Error('Windows desktop component has an invalid DLL import name')
      }
      if (!name) throw new Error('Windows desktop component has an empty DLL import name')
      imports.push(name)
    }
    throw new Error('Windows desktop component has an unterminated PE import directory')
  }
  readImports(1, 20, 12)
  readImports(13, 32, 4, true)
  const externalRuntime = imports.find(name =>
    /^(?:msvcp|msvcr|vcruntime|concrt)\d.*\.dll$|^(?:ucrtbased?|api-ms-win-crt-.*)\.dll$/i.test(
      path.win32.basename(name)
    )
  )
  if (externalRuntime) throw new Error(`Windows desktop component requires an external C++ runtime: ${externalRuntime}`)
}

module.exports = { assertStaticRuntime, readWindowsExecutableArch }
