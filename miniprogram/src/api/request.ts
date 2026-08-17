import Taro from '@tarojs/taro'
import { BASE_URL } from '../config'
import { TaskError } from '../types/report'
import { clearAuth, ensureLogin, getToken } from '../utils/auth'

/** 错误码 → 用户文案映射（方案文档 4.4 + 用户系统扩展） */
const ERROR_MESSAGES: Record<string, string> = {
  LLM_TIMEOUT: '生成超时，请重试',
  LLM_PARSE_FAILED: '生成结果解析失败，请重试',
  LLM_UNAVAILABLE: 'AI 服务暂时不可用，请稍后再试',
  TASK_TIMEOUT: '任务超时，请重试',
  SENSITIVE_CONTENT: '内容包含敏感信息，请更换内容',
  UNAUTHORIZED: '登录已失效，请重试',
  TOKEN_EXPIRED: '登录已过期，请重试',
  INVALID_NICKNAME: '昵称无效，请更换',
  NOT_FOUND: '记录不存在',
}

interface RequestOptions extends Omit<Taro.request.Option, 'url' | 'success' | 'fail'> {
  url: string
  /** 是否自动注入 token 并在 401 时重登重试（登录类请求传 false；默认 true） */
  needAuth?: boolean
}

/** 非 2xx 响应 → TaskError（detail 为 {code, message} 结构时优先映射文案） */
function buildError(res: Taro.request.SuccessCallbackResult): TaskError {
  const detail = (res.data as { detail?: unknown })?.detail
  const code = typeof detail === 'object' && detail !== null ? (detail as TaskError).code : undefined
  const serverMessage =
    typeof detail === 'object' && detail !== null ? (detail as TaskError).message : undefined
  return {
    code: code || 'HTTP_ERROR',
    message: (code && ERROR_MESSAGES[code]) || serverMessage || `请求失败（${res.statusCode}）`,
  }
}

/**
 * 统一请求封装（WHY：集中 baseUrl / 超时 / token 注入 / 401 自动重登 / 错误码→文案映射）
 * @param options url 为相对路径（如 /quiz）；needAuth=false 时不带 token 且 401 不重试
 * @returns 成功返回响应体；失败 reject TaskError（message 为用户可读文案）
 */
export function request<T>(options: RequestOptions): Promise<T> {
  const needAuth = options.needAuth !== false

  const doRequest = (attempt: number): Promise<T> => {
    const token = getToken()
    const header = {
      ...(options.header || {}),
      ...(needAuth && token ? { Authorization: `Bearer ${token}` } : {}),
    }
    return new Promise<T>((resolve, reject) => {
      Taro.request({
        ...options,
        header,
        url: `${BASE_URL}${options.url}`,
        timeout: 60000,
        success: async (res) => {
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve(res.data as T)
            return
          }
          // 401 → 清 token → 重新静默登录 → 重试一次（WHY：token 过期/换设备后自愈，用户无感）
          if (res.statusCode === 401 && needAuth && attempt < 1) {
            clearAuth()
            try {
              await ensureLogin()
            } catch {
              reject(buildError(res))
              return
            }
            doRequest(attempt + 1).then(resolve).catch(reject)
            return
          }
          reject(buildError(res))
        },
        fail: () => reject({ code: 'NETWORK_ERROR', message: '网络连接失败，请检查后端服务是否已启动' } as TaskError),
      })
    })
  }
  return doRequest(0)
}
