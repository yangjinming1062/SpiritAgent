import js from '@eslint/js'
import typescriptEslint from '@typescript-eslint/eslint-plugin'
import typescriptParser from '@typescript-eslint/parser'
import perfectionist from 'eslint-plugin-perfectionist'
import reactPlugin from 'eslint-plugin-react'
import reactCompiler from 'eslint-plugin-react-compiler'
import hooksPlugin from 'eslint-plugin-react-hooks'
import unusedImports from 'eslint-plugin-unused-imports'
import globals from 'globals'

// —— 渲染层 import 边界的共用限制（client/renderer/README.md「分层与目录」）—— flat config 对同一文件按序合并配置对象，后面的对象再次配置 no-restricted-imports 会整体替换前面的 patterns，所以每个渲染层规则块都用 restrictImports 组合出该目录的完整限制，不能只写本目录新增的部分。
const RENDERER_MODULES = ['character', 'conversation', 'media', 'memory', 'scene', 'speech']

const MODULE_DEEP_IMPORT = {
  group: ['@/modules/*/*'],
  message: '跨模块只能经目标模块的公共 barrel（@/modules/*）；模块内部用相对路径。'
}

// 应用层另可经 character 渲染域公共入口 rendering/video。
const APP_MODULE_DEEP_IMPORT = {
  regex: '^@/modules/(?!character/rendering/video$)[^/]+/.+',
  message:
    '跨模块只能经目标模块的公共 barrel（@/modules/*）；character 渲染域另经 @/modules/character/rendering/video。'
}

const MODULE_APP_IMPORT = {
  group: ['@/app', '@/app/*', '../app', '../app/*'],
  message: '业务模块不得反向依赖应用层。'
}

// 只匹配 barrel 本身；深路径由 MODULE_DEEP_IMPORT 报告，避免同一导入重复报错。
function moduleBarrelImport(allowed, message) {
  const blocked = RENDERER_MODULES.filter(name => !allowed.includes(name))

  return { regex: `^@/modules/(${blocked.join('|')})$`, message }
}

function otherWindowImport(self) {
  return {
    group: ['living', 'sprite', 'workbench']
      .filter(name => name !== self)
      .flatMap(name => [`@/app/windows/${name}`, `@/app/windows/${name}/*`]),
    message: '窗口体验互不依赖；共享能力下沉 modules 或经 app/workflows。'
  }
}

function restrictImports(...patterns) {
  return { 'no-restricted-imports': ['error', { patterns }] }
}

export default [
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      '**/dist-electron/**',
      'assets/**',
      'public/**',
      'src/**/*.js',
      '*.config.*'
    ]
  },
  js.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: typescriptParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        ecmaVersion: 'latest',
        project: ['./tsconfig.json', './tsconfig.main.json'],
        sourceType: 'module',
        tsconfigRootDir: import.meta.dirname
      }
    },
    plugins: {
      '@typescript-eslint': typescriptEslint,
      perfectionist,
      'unused-imports': unusedImports
    },
    rules: {
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': [
        'error',
        {
          checksVoidReturn: {
            attributes: false
          }
        }
      ],
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
          varsIgnorePattern: '^_'
        }
      ],
      curly: ['error', 'all'],
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-fallthrough': ['error', { allowEmptyCase: true }],
      'no-undef': 'off',
      'no-unused-vars': 'off',
      'padding-line-between-statements': [
        1,
        {
          blankLine: 'always',
          next: [
            'block-like',
            'block',
            'return',
            'if',
            'class',
            'continue',
            'debugger',
            'break',
            'multiline-const',
            'multiline-let'
          ],
          prev: '*'
        },
        {
          blankLine: 'always',
          next: '*',
          prev: ['case', 'default', 'multiline-const', 'multiline-let', 'multiline-block-like']
        },
        { blankLine: 'never', next: ['block', 'block-like'], prev: ['case', 'default'] },
        { blankLine: 'always', next: ['block', 'block-like'], prev: ['block', 'block-like'] },
        { blankLine: 'always', next: ['empty'], prev: 'export' },
        { blankLine: 'never', next: 'iife', prev: ['block', 'block-like', 'empty'] }
      ],
      'perfectionist/sort-exports': ['error', { order: 'asc', type: 'natural' }],
      'perfectionist/sort-imports': [
        'error',
        {
          groups: ['side-effect', 'builtin', 'external', 'internal', 'parent', 'sibling', 'index'],
          order: 'asc',
          type: 'natural'
        }
      ],
      'perfectionist/sort-jsx-props': ['error', { order: 'asc', type: 'natural' }],
      'perfectionist/sort-named-exports': ['error', { order: 'asc', type: 'natural' }],
      'perfectionist/sort-named-imports': ['error', { order: 'asc', type: 'natural' }],
      'unused-imports/no-unused-imports': 'error'
    }
  },
  {
    files: ['renderer/**/*.{ts,tsx}'],
    languageOptions: {
      globals: {
        ...globals.browser
      }
    },
    plugins: {
      react: reactPlugin,
      'react-compiler': reactCompiler,
      'react-hooks': hooksPlugin
    },
    rules: {
      ...reactPlugin.configs.recommended.rules,
      'react-compiler/react-compiler': 'warn',
      'react-hooks/exhaustive-deps': 'warn',
      'react-hooks/rules-of-hooks': 'error',
      'react/prop-types': 'off',
      'react/react-in-jsx-scope': 'off'
    },
    settings: {
      react: { version: 'detect' }
    }
  },
  {
    files: ['main/**/*.{ts,tsx}', 'scripts/**/*.{ts,tsx,mts}'],
    languageOptions: {
      globals: {
        ...globals.node
      }
    }
  },
  // —— main 子域边界：shared 为叶子；backend↛runner；runner↛backend/ipc；ipc↛lifecycle ——
  {
    files: ['main/shared/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../backend/*', '../../backend/*', '../ipc/*', '../../ipc/*', '../lifecycle/*', '../../lifecycle/*', '../runner/*', '../../runner/*'],
              message: 'shared 必须是叶子：依赖经 entry 注入，不得 import backend/ipc/lifecycle/runner。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['main/backend/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../runner/*', '../../runner/*', '../ipc/*', '../../ipc/*', '../lifecycle/*', '../../lifecycle/*'],
              message: 'backend 不得 import runner/ipc/lifecycle。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['main/runner/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../backend/*', '../../backend/*', '../ipc/*', '../../ipc/*'],
              message: 'runner 不得 import backend/ipc；会话与 HTTP 走 shared/backend-port。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['main/ipc/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../lifecycle/*', '../../lifecycle/*'],
              message: 'ipc 不得 import lifecycle；托盘/表面经 entry 注入回调或窄接口。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['main/lifecycle/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../runner/*', '../../runner/*'],
              message: 'lifecycle 不得 import runner 实现；由 entry 注入工厂/端口。'
            }
          ]
        }
      ]
    }
  },
  // —— 渲染层模块边界：跨模块只走公共 barrel，业务模块互不导入，shared 不反向依赖 ——
  {
    files: ['renderer/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: restrictImports(MODULE_DEEP_IMPORT)
  },
  {
    files: ['renderer/app/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: restrictImports(APP_MODULE_DEEP_IMPORT)
  },
  {
    // 运行时与工作流不得反向导入窗口组件；窗口只经入口与 app/bootstrap 装配。
    files: ['renderer/app/runtime/**/*.{ts,tsx}', 'renderer/app/workflows/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: restrictImports(APP_MODULE_DEEP_IMPORT, {
      group: ['@/app/windows', '@/app/windows/*', '../windows', '../windows/*'],
      message: '运行时与工作流不得反向导入窗口组件。'
    })
  },
  ...['living', 'sprite', 'workbench'].map(name => ({
    files: [`renderer/app/windows/${name}/**/*.{ts,tsx}`],
    ignores: ['**/node_modules/**'],
    rules: restrictImports(APP_MODULE_DEEP_IMPORT, otherWindowImport(name))
  })),
  {
    // 业务模块互不导入；需要两个模块一起完成的事情进 app/workflows。
    files: ['renderer/modules/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: restrictImports(
      MODULE_DEEP_IMPORT,
      MODULE_APP_IMPORT,
      moduleBarrelImport([], '业务模块互不导入，模块内部用相对路径；跨模块协作由 app 层装配。')
    )
  },
  {
    // 例外：气泡内媒体卡消费 media 的展示原语与媒体源解析（只读 UI 基元）。
    files: ['renderer/modules/conversation/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: restrictImports(
      MODULE_DEEP_IMPORT,
      MODULE_APP_IMPORT,
      moduleBarrelImport(
        ['media'],
        'conversation 只导入 media 展示原语；形象与语音经呈现端口及 voice-link 接缝（client/renderer/README.md「分层与目录」）。'
      )
    )
  },
  {
    // character 渲染域：只经 character 公共 barrel 访问角色能力。
    files: ['renderer/modules/character/rendering/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: restrictImports(
      MODULE_DEEP_IMPORT,
      MODULE_APP_IMPORT,
      moduleBarrelImport(['character'], '渲染域只经 @/modules/character 公共 barrel 访问角色能力，不导入其他业务模块。')
    )
  },
  {
    files: ['renderer/shared/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: restrictImports({
      group: ['@/app', '@/app/*', '@/modules/*'],
      message: 'shared 不得依赖应用层、业务模块与渲染实现。'
    })
  },
  {
    // 生产渲染面（精灵窗 / 工具窗 / 共享层）禁止裸 fetch：后端签名 URL 是相对路径，渲染进程 origin（dev 的 vite / 打包后的 file://）解析不到，请求会打到 vite 拿回 Vite 的 HTML 入口回退。后端数据与字节一律走主进程桥（api / apiAsset / apiAssetBuffer）。确需直连处逐行 eslint-disable 写明 URL 来源。
    files: [
      'renderer/app/**/*.{ts,tsx}',
      'renderer/modules/**/*.{ts,tsx}',
      'renderer/modules/character/rendering/**/*.{ts,tsx}',
      'renderer/shared/**/*.{ts,tsx}'
    ],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "CallExpression[callee.name='fetch'], CallExpression[callee.type='MemberExpression'][callee.property.name='fetch']",
          message:
            '生产渲染面禁裸 fetch——后端相对 URL 在渲染进程 origin 上解析不到。走 window.spiritagent 的 api / apiAsset / apiAssetBuffer 桥；例外逐行 eslint-disable 注明 URL 来源。'
        }
      ]
    }
  },
  {
    files: ['**/*.mjs', '**/vite.config.*', '**/vitest.config.*'],
    languageOptions: {
      ecmaVersion: 'latest',
      globals: { ...globals.node },
      sourceType: 'module'
    }
  },
  {
    files: ['**/*.js', '**/*.cjs'],
    languageOptions: {
      ecmaVersion: 'latest',
      globals: { ...globals.node },
      sourceType: 'commonjs'
    }
  }
]
