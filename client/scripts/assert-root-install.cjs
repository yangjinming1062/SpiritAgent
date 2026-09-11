'use strict'

const fs = require('fs')
const path = require('path')

const root = path.resolve(__dirname, '..')

try {
  fs.accessSync(path.join(root, 'node_modules', 'vite', 'package.json'))
} catch {
  console.error(`Run from desktop root: cd ${root} && pnpm install`)
  process.exit(1)
}
