import { useEffect, useMemo, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useShareAppMessage } from '@tarojs/taro'
import { createReportTask, getReportTask } from '../../api/report'
import { getSession, SessionDetail, SessionSubmitResult } from '../../api/user'
import { usePollingTask } from '../../hooks/usePollingTask'
import { Quiz } from '../../types/quiz'
import { AnswerRecord, Report, TaskError } from '../../types/report'
import './index.scss'

/** 建议序号（原型"壹/贰/叁"） */
const NUM = ['壹', '贰', '叁', '肆']
/** 环形图主色（与 scss $lake 一致，内联 style 使用） */
const LAKE = '#3A6B5C'

export default function ReportPage() {
  // 路由参数：mode=history&id={session_id} 为历史报告查看；否则为新报告流程
  const params = useMemo(() => Taro.getCurrentInstance().router?.params ?? {}, [])
  const historyId = params.mode === 'history' && params.id ? Number(params.id) : null

  const [quiz] = useState<Quiz | null>(() => (Taro.getStorageSync('quiz_data') as Quiz) || null)
  const [answers] = useState<AnswerRecord[]>(() => (Taro.getStorageSync('quiz_answers') as AnswerRecord[]) || [])
  const [duration] = useState<number>(() => Number(Taro.getStorageSync('quiz_duration')) || 0)
  const [sessionResult] = useState<SessionSubmitResult | null>(
    () => (Taro.getStorageSync('session_result') as SessionSubmitResult) || null,
  )
  const [taskId, setTaskId] = useState<string | null>(null)
  const [createError, setCreateError] = useState<TaskError | null>(null)
  const [history, setHistory] = useState<SessionDetail | null>(null)
  const [historyError, setHistoryError] = useState<TaskError | null>(null)

  const polling = usePollingTask<Report>(taskId, async (id) => {
    const resp = await getReportTask(id)
    return { status: resp.status, error: resp.error, data: resp.report }
  })

  // 历史模式：读库渲染（WHY：不再调 AI，零成本）；新报告模式：创建任务并关联 session_id
  useEffect(() => {
    if (historyId) {
      getSession(historyId)
        .then(setHistory)
        .catch((e) => setHistoryError(e as TaskError))
      return
    }
    if (!quiz) return
    createReportTask({ quiz, answers, session_id: sessionResult?.session_id })
      .then((r) => setTaskId(r.task_id))
      .catch((e) => setCreateError(e as TaskError))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyId])

  // 报告生成完成 → 写入 Storage（海报页读取）
  useEffect(() => {
    if (polling.status === 'completed' && polling.data) {
      Taro.setStorageSync('report_data', polling.data)
    }
  }, [polling.status, polling.data])

  useShareAppMessage(() => ({
    title: report ? `「${reportTopic}」${report.quote}` : 'AI 闯关学习',
    path: '/pages/index/index',
  }))

  const report: Report | null = historyId ? (history?.report ?? null) : polling.data
  const reportTopic: string = historyId ? (history?.topic ?? '') : (quiz?.topic ?? '')
  const loading = historyId
    ? history === null && historyError === null
    : taskId !== null && (polling.status === 'pending' || polling.status === 'running')
  const failed = historyId
    ? historyError !== null || (history !== null && history.report === null)
    : polling.status === 'failed' || createError !== null
  const failedMessage = historyId
    ? (historyError?.message ?? (history !== null && history.report === null ? '该次报告生成失败，仅保留题目与判分记录' : ''))
    : (polling.error?.message || createError?.message || '')
  const coinsDelta = historyId ? (history?.coins_delta ?? 0) : (sessionResult?.coins_delta ?? 0)
  const durationText = `${Math.floor(duration / 60)} 分 ${duration % 60} 秒`

  /** 重试（新报告模式）：重新创建报告任务 */
  const handleRetry = () => {
    if (!quiz) return
    setTaskId(null)
    setCreateError(null)
    createReportTask({ quiz, answers, session_id: sessionResult?.session_id })
      .then((r) => setTaskId(r.task_id))
      .catch((e) => setCreateError(e as TaskError))
  }

  /** 再闯一关（新报告模式）：清 Storage 回主页 */
  const handleAgain = () => {
    Taro.removeStorageSync('quiz_data')
    Taro.removeStorageSync('quiz_answers')
    Taro.removeStorageSync('quiz_duration')
    Taro.removeStorageSync('session_result')
    Taro.reLaunch({ url: '/pages/index/index' })
  }

  if (!historyId && !quiz) {
    return (
      <View className='page'>
        <View className='empty'>
          <View className='empty-msg'>报告数据丢失了，请回主页重新闯关</View>
          <Button className='btn-primary' onClick={() => Taro.reLaunch({ url: '/pages/index/index' })}>
            回到主页
          </Button>
        </View>
      </View>
    )
  }

  if (loading) {
    return (
      <View className='page'>
        <View className='body'>
          <View className='chapter'>CHAP. 03 · 归程</View>
          <View className='loading-center'>
            <View className='ripple-stage'>
              <View className='ripple' />
              <View className='ripple' />
              <View className='ripple' />
              <View className='ripple-core' />
            </View>
            <Text className='loading-text'>炉火微温，正在整理你的复盘…</Text>
          </View>
        </View>
      </View>
    )
  }

  if (failed || createError) {
    return (
      <View className='page'>
        <View className='body'>
          <View className='chapter'>CHAP. 03 · 归程</View>
          <View className='loading-center'>
            <View className='error-msg'>{failedMessage}</View>
            {!historyId && <Button className='btn-primary retry-btn' onClick={handleRetry}>重试</Button>}
            {!historyId && <Button className='btn-ghost retry-btn' onClick={() => Taro.navigateBack()}>返回闯关</Button>}
            {historyId && <Button className='btn-ghost retry-btn' onClick={() => Taro.navigateBack()}>返回我的</Button>}
          </View>
        </View>
      </View>
    )
  }

  if (!report) return null

  return (
    <View className='page'>
      <View className='body'>
        <View className='chapter'>CHAP. 03 · 归程</View>

        <View className='report-head'>
          <View
            className='ring'
            style={{ background: `conic-gradient(${LAKE} ${report.correct_rate}%, rgba(139, 111, 71, 0.15) 0)` }}
          >
            <View className='inner'>
              <Text className='num'>{report.correct_rate}%</Text>
              <Text className='lbl'>正确率</Text>
            </View>
          </View>
          <View className='meta'>
            <View className='stamp'>{historyId ? '林间回望' : '湖畔漫步完成'}</View>
            <View className='sub'>
              答对 {report.correct_count} / {report.total_questions} 题
              {historyId ? '' : ` · 用时 ${durationText}`}
            </View>
            {coinsDelta !== 0 && (
              <View className={`coin-line ${coinsDelta > 0 ? 'gain' : 'loss'}`}>
                {historyId ? '该关金币' : '本关金币'} {coinsDelta > 0 ? `+${coinsDelta}` : coinsDelta}
              </View>
            )}
          </View>
        </View>

        <View className='sec-h'>知识总结</View>
        <View className='summary card'>{report.summary}</View>

        <View className='sec-h'>掌握度</View>
        <View className='mastery'>
          {report.mastery.map((m) => (
            <View key={m.knowledge_point} className='row'>
              <Text className='kp'>{m.knowledge_point}</Text>
              <View className='bar'>
                <View className='fill' style={{ width: `${m.level}%` }} />
              </View>
              <Text className='pct'>{m.level}%</Text>
            </View>
          ))}
        </View>

        <View className='sec-h'>下一步</View>
        <View className='suggest card'>
          {report.suggestions.map((s, i) => (
            <View key={i} className='s-row'>
              <Text className='n'>{NUM[i]}</Text>
              <Text className='s-txt'>{s}</Text>
            </View>
          ))}
        </View>

        <View className='quote-card'>
          <Text className='q'>“{report.quote}”</Text>
        </View>

        {historyId ? (
          <View className='btn-row'>
            <Button className='btn-primary' onClick={() => Taro.navigateBack()}>返回我的</Button>
          </View>
        ) : (
          <View className='btn-row'>
            <Button className='btn-primary' onClick={() => Taro.navigateTo({ url: '/pages/poster/index' })}>
              生成分享海报
            </Button>
            <Button className='btn-ghost' onClick={handleAgain}>再闯一关</Button>
          </View>
        )}
      </View>
    </View>
  )
}
