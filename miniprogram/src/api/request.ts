import Taro from '@tarojs/taro'
import { TaskError } from '../types/report'

/** 后端地址（开发环境直连本机 FastAPI；开发者工具需勾选"不校验合法域名"） */
export const BASE_URL = 'http://127.0.0.1:8000'

/** 错误码 → 用户文案映射（方案文档 4.4 错误码约定） */
const ERROR_MESSAGES: Record<string, string> = {
  LLM_TIMEOUT: '生成超时，请重试',
  LLM_PARSE_FAILED: '生成结果解析失败，请重试',
  LLM_UNAVAILABLE: 'AI 服务暂时不可用，请稍后再试',
  TASK_TIMEOUT: '任务超时，请重试',
  SENSITIVE_CONTENT: '内容包含敏感信息，请更换内容',
}

/**
 * 统一请求封装（WHY：集中 baseUrl / 超时 / 错误码→文案映射，页面不感知网络细节）
 * @param options Taro.request 参数，url 为相对路径（如 /quiz）
 * @returns 成功时返回响应体；失败 reject TaskError（message 为用户可读文案）
 */
export function request<T>(
  options: Omit<Taro.request.Option, 'url' | 'success' | 'fail'> & { url: string },
): Promise<T> {
  return new Promise((resolve, reject) => {
    Taro.request({
      ...options,
      url: `${BASE_URL}${options.url}`,
      timeout: 60000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data as T)
          return
        }
        // 业务错误（如 422）：detail 为 {code, message} 结构
        const detail = (res.data as { detail?: unknown })?.detail
        const code = typeof detail === 'object' && detail !== null ? (detail as TaskError).code : undefined
        const serverMessage =
          typeof detail === 'object' && detail !== null ? (detail as TaskError).message : undefined
        const error: TaskError = {
          code: code || 'HTTP_ERROR',
          message: (code && ERROR_MESSAGES[code]) || serverMessage || `请求失败（${res.statusCode}）`,
        }
        reject(error)
      },
      fail() {
        reject({ code: 'NETWORK_ERROR', message: '网络连接失败，请检查后端服务是否已启动' } as TaskError)
      },
    })
  })
}
