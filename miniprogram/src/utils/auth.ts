import Taro from '@tarojs/taro'
import { BASE_URL } from '../config'

/** 用户摘要（与后端 /auth/login 响应 user 字段对齐，契约单一来源） */
export interface UserBrief {
  id: number
  nickname: string
  avatar_text: string
  coins: number
}

/** 登录接口响应 */
export interface LoginResponse {
  token: string
  user: UserBrief
}

const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'

export function getToken(): string {
  return (Taro.getStorageSync(TOKEN_KEY) as string) || ''
}

export function getUser(): UserBrief | null {
  return (Taro.getStorageSync(USER_KEY) as UserBrief) || null
}

export function setAuth(token: string, user: UserBrief): void {
  Taro.setStorageSync(TOKEN_KEY, token)
  Taro.setStorageSync(USER_KEY, user)
}

export function clearAuth(): void {
  Taro.removeStorageSync(TOKEN_KEY)
  Taro.removeStorageSync(USER_KEY)
}

/**
 * 静默登录（WHY：wx.login 无感换 code → 后端换 openid + JWT；失败时调用方降级为游客体验，不阻断闯关）
 * 已有 token 直接复用；无 token 才发起登录。登录请求不经 request 封装（WHY：避免 request→auth 循环依赖）
 * @returns 登录用户摘要；token 在而用户缓存缺失时返回 null（本地缓存不一致，非致命）
 */
export async function ensureLogin(): Promise<UserBrief | null> {
  if (getToken()) {
    return getUser()
  }
  const { code } = await Taro.login()
  const resp = await new Promise<LoginResponse>((resolve, reject) => {
    Taro.request({
      url: `${BASE_URL}/auth/login`,
      method: 'POST',
      data: { code },
      timeout: 15000,
      success: (res) => {
        if (res.statusCode === 200) {
          resolve(res.data as LoginResponse)
        } else {
          reject(new Error(`login failed: ${res.statusCode}`))
        }
      },
      fail: reject,
    })
  })
  setAuth(resp.token, resp.user)
  return resp.user
}
