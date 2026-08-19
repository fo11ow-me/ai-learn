/**
 * 后端地址（开发环境直连本机 FastAPI；开发者工具需勾选"不校验合法域名"）。
 * 可用 TARO_APP_API_BASE 编译期覆盖（WHY：本机 8000 可能落在 Hyper-V 排除端口范围
 * 7963-8062 内无法绑定，如 TARO_APP_API_BASE=http://127.0.0.1:8200 npm run dev:h5）
 */
export const BASE_URL = process.env.TARO_APP_API_BASE || 'http://127.0.0.1:8000'

/**
 * 复习提醒订阅消息模板 ID（微信小程序后台申请；与后端 WECHAT_TMPL_REVIEW 保持一致）。
 * 空值时错题本页不展示「订阅复习提醒」按钮（无法发起授权）。
 * 可用 TARO_APP_REVIEW_TMPL_ID 编译期覆盖。
 */
export const REVIEW_TMPL_ID = process.env.TARO_APP_REVIEW_TMPL_ID || ''
