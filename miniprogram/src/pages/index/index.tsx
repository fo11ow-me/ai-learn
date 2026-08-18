import { useCallback, useEffect, useState } from 'react'
import { Button, Text, Textarea, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { createQuizTask, getQuizTask } from '../../api/quiz'
import { getMe } from '../../api/user'
import { usePollingTask } from '../../hooks/usePollingTask'
import { useStatusBarHeight } from '../../hooks/useStatusBarHeight'
import { Quiz } from '../../types/quiz'
import { TaskError } from '../../types/report'
import './index.scss'

/** 兜底主题（未登录 / 无学习历史时展示；有历史时被最近学习主题替换） */
const FALLBACK_TOPICS = [
  { title: '光的波粒二象性', tag: '物理学' },
  { title: '光合作用的全过程', tag: '生物学' },
  { title: '瓦尔登湖与超验主义', tag: '文学' },
]

const MAX_TOPICS = 3  // 与现有布局一致（原型"或沿旧径重游"固定 3 张卡）

/** 生成中轮播文案（原型屏 2，2.8s 间隔） */
const LOADING_LINES = [
  '晨雾散开，题目浮现…',
  '沿湖畔小径，拾取知识的松果…',
  '炉火微温，正在整理你的考题…',
  '听见了吗，那是题目涉水而来的声音…',
]

const MAX_LENGTH = 2000

export default function Index() {
  const [content, setContent] = useState('')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [createError, setCreateError] = useState<TaskError | null>(null)
  // 最近学习主题（有历史时替换兜底主题，点击行为不变：填入输入框）
  const [topics, setTopics] = useState<{ title: string; tag?: string }[]>(FALLBACK_TOPICS)
  const statusBarHeight = useStatusBarHeight()
  const pageStyle = { paddingTop: `${statusBarHeight}px` }

  // 每次进入页面刷新最近学习主题（WHY：闯关后返回首页需展示最新历史；未登录/失败静默回退兜底主题）
  const loadRecentTopics = useCallback(() => {
    getMe()
      .then((d) => {
        const seen = new Set<string>()
        const recent: { title: string }[] = []
        for (const s of d.recent_sessions) {
          if (recent.length >= MAX_TOPICS) break
          if (!s.topic || seen.has(s.topic)) continue
          seen.add(s.topic)
          recent.push({ title: s.topic })
        }
        if (recent.length > 0) setTopics(recent)
      })
      .catch(() => {})  // 游客/网络异常：保持兜底主题，不打扰用户
  }, [])

  useEffect(loadRecentTopics, [loadRecentTopics])  // 首次挂载必拉取（WHY：H5 刷新时 onShow 早于组件挂载，useDidShow 会丢失）
  useDidShow(loadRecentTopics)  // tab 切换/闯关返回时刷新

  const polling = usePollingTask<Quiz>(taskId, async (id) => {
    const resp = await getQuizTask(id)
    return { status: resp.status, error: resp.error, data: resp.quiz }
  })

  // 含 completed（WHY：出题完成瞬间保持加载屏直到 navigateTo 生效，否则会闪回首页表单再跳转）
  const generating = taskId !== null && polling.status !== 'failed'
  const failed = taskId !== null && polling.status === 'failed'

  // 出题完成 → 写入 Storage 并进入闯关页（quiz 数据与作答记录经 Storage 传页）
  useEffect(() => {
    if (polling.status === 'completed' && polling.data) {
      Taro.setStorageSync('quiz_data', polling.data)
      Taro.setStorageSync('quiz_content', content)  // 新增：结算/防刷需要用户输入原文
      Taro.removeStorageSync('quiz_answers')
      Taro.removeStorageSync('quiz_duration')
      // 成功/失败都清空 taskId（WHY：成功不清则返回首页时 taskId 残留，generating 恒真卡在雾散屏；失败清空回表单可重试）
      Taro.navigateTo({ url: '/pages/quiz/index' }).finally(() => setTaskId(null))
    }
  }, [polling.status, polling.data])

  /** 发出发题请求（点击"开始漫步"或失败后重试） */
  const handleStart = async () => {
    if (!content.trim()) {
      Taro.showToast({ title: '先写下想学的知识吧', icon: 'none' })
      return
    }
    setCreateError(null)
    try {
      const resp = await createQuizTask(content.trim())
      setTaskId(resp.task_id)
    } catch (e) {
      setCreateError(e as TaskError)
      setTaskId(null)
    }
  }

  // 生成中 / 失败 均展示屏 2（原型：雾散）
  if (generating || failed) {
    return (
      <View className='page' style={pageStyle}>
        <View className='body'>
          <View className='chapter'>CHAP. 02 · 雾散</View>
          {generating && <LoadingStage />}
          {failed && (
            <View className='loading-center'>
              <View className='error-msg'>{polling.error?.message || createError?.message}</View>
              <Button className='btn-primary retry-btn' onClick={handleStart}>重试</Button>
              <Button
                className='btn-ghost retry-btn'
                onClick={() => {
                  setTaskId(null)
                  setCreateError(null)
                }}
              >
                返回修改内容
              </Button>
            </View>
          )}
          <View className='waves' />
        </View>
      </View>
    )
  }

  return (
    <View className='page' style={pageStyle}>
      <View className='body'>
        <View className='chapter'>CHAP. 01 · 湖畔</View>
        <Text className='home-title'>今日，想探索哪片{'\n'}知识的林间小径？</Text>
        <View className='home-sub'>输入一句话 / 一段文字，AI 为你铺就 5 道闯关之题</View>

        <View className='input-wrap'>
          <Textarea
            className='input-area'
            placeholder='输入想学的知识，如：光的波粒二象性'
            value={content}
            maxlength={MAX_LENGTH}
            onInput={(e) => setContent(e.detail.value)}
          />
          <View className='input-meta'>
            <Text>最多 2000 字</Text>
            <Text>{content.length} / 2000</Text>
          </View>
        </View>

        <Button className='btn-primary start-btn' onClick={handleStart}>
          开始漫步 <Text className='arrow'>→</Text>
        </Button>

        <View className='divider'>或沿旧径重游</View>

        {topics.map((t) => (
          <View key={t.title} className='topic-card card' onClick={() => setContent(t.title)}>
            <Text className='t'>{t.title}</Text>
            {t.tag && <Text className='tag'>{t.tag}</Text>}
          </View>
        ))}

        {createError && <View className='create-error'>{createError.message}</View>}

        <View className='waves' />
      </View>
    </View>
  )
}

/** 生成中：涟漪动画 + 轮播文案（原型屏 2） */
function LoadingStage() {
  const [lineIndex, setLineIndex] = useState(0)
  const [fade, setFade] = useState(false)

  useEffect(() => {
    const timer = setInterval(() => {
      setFade(true)
      setTimeout(() => {
        setLineIndex((i) => (i + 1) % LOADING_LINES.length)
        setFade(false)
      }, 400)
    }, 2800)
    return () => clearInterval(timer)
  }, [])

  return (
    <View className='loading-center'>
      <View className='ripple-stage'>
        <View className='ripple' />
        <View className='ripple' />
        <View className='ripple' />
        <View className='ripple-core' />
      </View>
      <Text className={`loading-text ${fade ? 'fade' : ''}`}>{LOADING_LINES[lineIndex]}</Text>
      <View className='loading-hint'>正在铺就 5 道考题 · 约 15 秒</View>
    </View>
  )
}
