import { useEffect, useRef, useState } from 'react'
import { TaskError, TaskStatus } from '../types/report'

/** 轮询查询函数的返回契约：状态 + 可选错误 + 可选数据 */
export interface TaskPollResult<T> {
  status: TaskStatus
  error?: TaskError
  data?: T
}

export interface PollingResult<T> {
  /** idle = 未开始（taskId 为空） */
  status: TaskStatus | 'idle'
  data: T | null
  error: TaskError | null
}

/**
 * 轮询任务状态（WHY：出题 10~30s，1.5s 轮询延迟可接受，实现最简最稳——方案文档 3.2）
 * taskId 为 null 时不轮询；taskId 变化自动重启轮询；卸载时清理定时器
 * @param getTask 查询函数，返回统一契约（页面负责把 api 响应适配为 {status, error, data}）
 * @param opts.interval 轮询间隔 ms（默认 1500）
 * @param opts.timeout 超时 ms（默认 90000，超时视为失败提示重试）
 */
export function usePollingTask<T>(
  taskId: string | null,
  getTask: (taskId: string) => Promise<TaskPollResult<T>>,
  opts?: { interval?: number; timeout?: number },
): PollingResult<T> {
  const [status, setStatus] = useState<TaskStatus | 'idle'>('idle')
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<TaskError | null>(null)
  const stoppedRef = useRef(false)

  useEffect(() => {
    if (!taskId) return

    stoppedRef.current = false
    setStatus('pending')
    setError(null)
    const interval = opts?.interval ?? 1500
    const timeoutMs = opts?.timeout ?? 90000
    const startAt = Date.now()
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      if (stoppedRef.current) return
      try {
        const resp = await getTask(taskId)
        if (stoppedRef.current) return
        if (resp.status === 'completed') {
          setData((resp.data ?? null) as T | null)
          setStatus('completed')
          return
        }
        if (resp.status === 'failed') {
          setError(resp.error || { code: 'UNKNOWN', message: '任务失败，请重试' })
          setStatus('failed')
          return
        }
        if (Date.now() - startAt > timeoutMs) {
          setError({ code: 'POLL_TIMEOUT', message: '等待超时，请重试' })
          setStatus('failed')
          return
        }
        setStatus(resp.status)
        timer = setTimeout(poll, interval)
      } catch (e) {
        if (stoppedRef.current) return
        setError(e as TaskError)
        setStatus('failed')
      }
    }

    poll()
    return () => {
      stoppedRef.current = true
      if (timer) clearTimeout(timer)
    }
  }, [taskId])

  return { status, data, error }
}
