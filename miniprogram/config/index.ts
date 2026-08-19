import { defineConfig, type UserConfigExport } from '@tarojs/cli'
import TsconfigPathsPlugin from 'tsconfig-paths-webpack-plugin'
import devConfig from './dev'
import prodConfig from './prod'

// https://taro-docs.jd.com/docs/next/config#defineconfig-辅助函数
export default defineConfig<'webpack5'>(async (merge) => {
  const baseConfig: UserConfigExport<'webpack5'> = {
    projectName: 'miniprogram',
    date: '2026-08-16',
    designWidth: 750,
    deviceRatio: {
      640: 2.34 / 2,
      750: 1,
      375: 2,
      828: 1.81 / 2
    },
    sourceRoot: 'src',
    // 按平台分目录输出（WHY：weapp 与 h5 共用 dist 会互相覆盖，导致开发者工具加载到 H5 产物白屏）
    outputRoot: `dist/${process.env.TARO_ENV}`,
    plugins: [
      "@tarojs/plugin-generator"
    ],
    defineConstants: {
      // 编译期注入后端地址覆盖（WHY：webpack H5/weapp 无 process 全局，process.env 直读会 ReferenceError；
      // DefinePlugin 把字面量替换为字符串后两端安全。未设置 → 空串 → config.ts 回落默认 8000）
      'process.env.TARO_APP_API_BASE': JSON.stringify(process.env.TARO_APP_API_BASE || ''),
      // 订阅消息模板 ID（同上：未被 DefinePlugin 替换的 process.env.X 会在小程序运行时 ReferenceError；
      // 空串 → 错题本页不展示订阅按钮）
      'process.env.TARO_APP_REVIEW_TMPL_ID': JSON.stringify(process.env.TARO_APP_REVIEW_TMPL_ID || ''),
    },
    copy: {
      patterns: [
        // tabBar 图标等静态资源：app.json 的 iconPath 是字符串引用，不经 webpack，需直接拷贝到产物
        { from: 'src/assets/icons', to: 'dist/assets/icons' },
      ],
      options: {
      }
    },
    framework: 'react',
    compiler: 'webpack5',
    cache: {
      enable: false // Webpack 持久化缓存配置，建议开启。默认配置请参考：https://docs.taro.zone/docs/config-detail#cache
    },
    mini: {
      postcss: {
        pxtransform: {
          enable: true,
          config: {

          }
        },
        cssModules: {
          enable: false, // 默认为 false，如需使用 css modules 功能，则设为 true
          config: {
            namingPattern: 'module', // 转换模式，取值为 global/module
            generateScopedName: '[name]__[local]___[hash:base64:5]'
          }
        }
      },
      webpackChain(chain) {
        chain.resolve.plugin('tsconfig-paths').use(TsconfigPathsPlugin)
      }
    },
    h5: {
      publicPath: '/',
      staticDirectory: 'static',
      output: {
        filename: 'js/[name].[hash:8].js',
        chunkFilename: 'js/[name].[chunkhash:8].js'
      },
      miniCssExtractPluginOption: {
        ignoreOrder: true,
        filename: 'css/[name].[hash].css',
        chunkFilename: 'css/[name].[chunkhash].css'
      },
      postcss: {
        autoprefixer: {
          enable: true,
          config: {}
        },
        cssModules: {
          enable: false, // 默认为 false，如需使用 css modules 功能，则设为 true
          config: {
            namingPattern: 'module', // 转换模式，取值为 global/module
            generateScopedName: '[name]__[local]___[hash:base64:5]'
          }
        }
      },
      webpackChain(chain) {
        chain.resolve.plugin('tsconfig-paths').use(TsconfigPathsPlugin)
      }
    },
    rn: {
      appName: 'taroDemo',
      postcss: {
        cssModules: {
          enable: false, // 默认为 false，如需使用 css modules 功能，则设为 true
        }
      }
    }
  }

  if (process.env.NODE_ENV === 'development') {
    // 本地开发构建配置（不混淆压缩）
    return merge({}, baseConfig, devConfig)
  }
  // 生产构建配置（默认开启压缩混淆等）
  return merge({}, baseConfig, prodConfig)
})
