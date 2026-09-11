import js from '@eslint/js'
import typescriptEslint from '@typescript-eslint/eslint-plugin'
import typescriptParser from '@typescript-eslint/parser'
import perfectionist from 'eslint-plugin-perfectionist'
import reactPlugin from 'eslint-plugin-react'
import reactCompiler from 'eslint-plugin-react-compiler'
import hooksPlugin from 'eslint-plugin-react-hooks'
import unusedImports from 'eslint-plugin-unused-imports'
import globals from 'globals'

export default [
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      '**/dist-electron/**',
      'assets/**',
      'public/**',
      'src/**/*.js',
      'renderer/modules/character/rendering/2d/puppet/vendor/**',
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
  // —— 渲染层模块边界：跨模块只走公共 barrel，业务模块互不导入，shared 不反向依赖 ——

  {
    files: ['renderer/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                '@/modules/conversation/*',
                '@/modules/character/*',
                '@/modules/speech/*',
                '@/modules/media/*',
                '@/modules/memory/*',
                '@/modules/room/*',
                '@/modules/character/rendering/*'
              ],
              message: '跨模块只能经目标模块的公共 barrel（@/modules/*；character 渲染域为 @/modules/character/rendering/2d、/3d）；模块内部用相对路径。'
            }
          ]
        }
      ]
    }
  },
  {
    // app 组合层：character 深路径禁止，但渲染域的两个公共入口（rendering/2d、rendering/3d）放行。
    files: ['renderer/app/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                '@/modules/conversation/*',
                '@/modules/speech/*',
                '@/modules/media/*',
                '@/modules/memory/*',
                '@/modules/room/*'
              ],
              message: '跨模块只能经目标模块的公共 barrel（@/modules/*）。'
            },
            {
              regex: '^@/modules/character/(?!rendering/(2d|3d)$).+',
              message: 'character 只经公共 barrel（@/modules/character）与渲染域入口（rendering/2d、/3d）访问。'
            }
          ]
        }
      ]
    }
  },
  {
    // 业务模块互不导入；需要两个模块一起完成的事情进 app/workflows（client/README.md §3）。
    files: ['renderer/modules/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app', '@/app/*', '../app', '../app/*'],
              message: '业务模块不得反向依赖应用层。'
            },
            {
              group: ['@/modules/conversation', '@/modules/character', '@/modules/speech', '@/modules/media'],
              message: '业务模块互不导入；跨模块协作由 app 层装配。'
            }
          ]
        }
      ]
    }
  },
  {
    // 例外：气泡内媒体卡消费 media 的展示原语与媒体源解析（只读 UI 基元）。
    files: ['renderer/modules/conversation/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app', '@/app/*', '../app', '../app/*'],
              message: '业务模块不得反向依赖应用层。'
            },
            {
              group: ['@/modules/character', '@/modules/speech'],
              message: 'conversation 不导入形象/语音模块：形象与语音经呈现端口及 voice-link 接缝（client/README.md §3）。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['renderer/modules/character/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**', '**/character/rendering/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app', '@/app/*', '../app', '../app/*'],
              message: '业务模块不得反向依赖应用层。'
            },
            {
              group: ['@/modules/conversation', '@/modules/speech', '@/modules/media'],
              message: '业务模块互不导入；跨模块协作由 app 层装配。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['renderer/modules/speech/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app', '@/app/*', '../app', '../app/*'],
              message: '业务模块不得反向依赖应用层。'
            },
            {
              group: ['@/modules/conversation', '@/modules/character', '@/modules/media'],
              message: '业务模块互不导入；跨模块协作由 app 层装配。'
            }
          ]
        }
      ]
    }
  },
  {
    // character 渲染域（2d/3d）：可消费 character barrel 与 speech 口型振幅，禁入应用层与会话。
    files: ['renderer/modules/character/rendering/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app', '@/app/*', '../app', '../app/*'],
              message: '渲染层不得依赖应用层。'
            },
            {
              group: ['@/modules/conversation', '@/modules/media'],
              message: '渲染域只消费 @/modules/character 与 @/modules/speech。'
            },
            {
              group: ['@/modules/character/*'],
              message: '渲染域访问 character 只经其公共 barrel。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['renderer/modules/media/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app', '@/app/*', '../app', '../app/*'],
              message: '业务模块不得反向依赖应用层。'
            },
            {
              group: ['@/modules/conversation', '@/modules/character', '@/modules/speech'],
              message: '业务模块互不导入；跨模块协作由 app 层装配。'
            }
          ]
        }
      ]
    }
  },
  {
    // 运行时与工作流不得反向导入窗口组件；窗口只经入口与 app/bootstrap 装配。
    files: ['renderer/app/runtime/**/*.{ts,tsx}', 'renderer/app/workflows/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                '@/app/windows',
                '@/app/windows/*',
                '../windows',
                '../windows/*',
                '@/modules/conversation/*',
                '@/modules/speech/*',
                '@/modules/media/*',
                '@/modules/memory/*',
                '@/modules/room/*'
              ],
              message: '运行时与工作流不得反向导入窗口组件。'
            },
            {
              regex: '^@/modules/character/(?!rendering/(2d|3d)$).+',
              message: 'character 只经公共 barrel 与渲染域入口访问。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['renderer/app/windows/living/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app/windows/sprite', '@/app/windows/sprite/*', '@/app/windows/workbench', '@/app/windows/workbench/*'],
              message: '窗口体验互不依赖；共享能力下沉 modules 或经 app/workflows。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['renderer/app/windows/workbench/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app/windows/sprite', '@/app/windows/sprite/*', '@/app/windows/living', '@/app/windows/living/*'],
              message: '窗口体验互不依赖；共享能力下沉 modules 或经 app/workflows。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['renderer/app/windows/sprite/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app/windows/living', '@/app/windows/living/*', '@/app/windows/workbench', '@/app/windows/workbench/*'],
              message: '窗口体验互不依赖；共享能力下沉 modules 或经 app/workflows。'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['renderer/shared/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/app', '@/app/*', '@/modules/*'],
              message: 'shared 不得依赖应用层、业务模块与渲染实现。'
            }
          ]
        }
      ]
    }
  },
  {
    // 生产渲染面（精灵窗 / 工具窗 / 共享层）禁止裸 fetch：后端签名 URL 是相对路径，
    // 渲染进程 origin（dev 的 vite / 打包后的 file://）解析不到，请求会打到 vite 拿回
    // SPA 回退的 index.html。后端数据与字节一律走主进程桥（api / apiAsset /
    // apiAssetBuffer / apiAssetModelUrl）。确需直连处逐行 eslint-disable 写明 URL 来源。
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
            '生产渲染面禁裸 fetch——后端相对 URL 在渲染进程 origin 上解析不到。走 window.spiritagent 的 api / apiAsset / apiAssetBuffer / apiAssetModelUrl 桥；例外逐行 eslint-disable 注明 URL 来源。'
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
