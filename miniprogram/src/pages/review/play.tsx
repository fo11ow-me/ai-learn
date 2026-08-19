import { useCallback, useEffect, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { getReview, submitReview } from '../../api/review'
import { useStatusBarHeight } from '../../hooks/useStatusBarHeight'
import { ReviewItem } from '../../types/review'
import { TaskError } from '../../types/report'
import { isDue, nextLabel } from '../../utils/review'
import './play.scss'

/** 题型显示名（与闯关页一致） */
const TYPE_LABEL: Record<string, string> = { single: '单选题', multiple: '多选题', judge: '判断题' }

/** 选项序号（所有题型统一 A-D，判断题 A/B，对齐闯关页） */
const OPTION_KEYS = ['A', 'B', 'C', 'D']

/** 本轮作答（提交契约：item_id + 已选索引；服务端按快照重判分，不信任前端） */
interface Attempt {
  item_id: number
  selected: number[]
}

/** 结果态摘要 */
interface PlayResult {
  correct: number
  mastered: number
  /** 下次复习最早时刻（ISO） */
  nextReviewAt: string
}

/** 自绘导航条（返回错题本） */
function NavBar() {
  return (
    <View className='nav-bar'>
      <View className='nav-back' onClick={() => Taro.navigateBack({ fail: () => Taro.switchTab({ url: '/pages/index/index' }) })}>
        <View className='nav-back-icon' />
      </View>
      <Text className='nav-title'>宝藏关卡</Text>
      <View className='nav-side' />
    </View>
  )
}

export default function ReviewPlayPage() {
  // null=加载中；[]=无到期题或加载失败（error 区分）
  const [items, setItems] = useState<ReviewItem[] | null>(null)
  const [error, setError] = useState('')
  const [current, setCurrent] = useState(0)
  const [selected, setSelected] = useState<number[]>([])
  const [answered, setAnswered] = useState(false)
  const [attempts, setAttempts] = useState<Attempt[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<PlayResult | null>(null)
  const statusBarHeight = useStatusBarHeight()
  const pageStyle = { paddingTop: `${statusBarHeight}px` }

  // 进入关卡时自行拉取并过滤到期错题（WHY：不依赖列表页 Storage 传参——关卡自包含，直接 URL 进入也可用）
  const load = useCallback(() => {
    getReview()
      .then((board) => {
        setItems(board.items.filter((i) => isDue(i.next_review_at)))
        setError('')
      })
      .catch((e) => {
        setError((e as TaskError).message)
        setItems([])
      })
  }, [])

  useEffect(load, [load])

  const item: ReviewItem | undefined = items?.[current]
  const isLast = items !== null && current === items.length - 1

  /** 判分（纯前端：sorted 集合相等，多选全中才判对；与后端重判分规则一致） */
  const isCorrect =
    answered && item !== undefined &&
    [...selected].sort().join(',') === [...item.question.answer].sort().join(',')

  /** 选项点击：单选/判断点击即判分；多选先勾选再点「提交答案」（与闯关页交互一致） */
  const handleOptionClick = (index: number) => {
    if (answered || !item) return
    if (item.question.type === 'multiple') {
      setSelected((prev) =>
        prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index],
      )
      return
    }
    submit([index])
  }

  /** 判分并记录作答（本地即时反馈；提交才落库——中途退出不留任何状态） */
  const submit = (finalSelected: number[]) => {
    if (!item || answered) return
    if (finalSelected.length === 0) {
      Taro.showToast({ title: '请选择答案', icon: 'none' })
      return
    }
    setSelected(finalSelected)
    setAnswered(true)
    setAttempts((prev) => [...prev, { item_id: item.id, selected: finalSelected }])
  }

  /** 下一题；最后一题提交本轮（全部作答一次性提交，服务端单事务更新调度） */
  const handleNext = async () => {
    if (!isLast) {
      setCurrent((c) => c + 1)
      setSelected([])
      setAnswered(false)
      return
    }
    setSubmitting(true)
    try {
      const res = await submitReview({ attempts })
      const earliest = res.updated.reduce<string | null>(
        (acc, u) => (acc === null || u.next_review_at < acc ? u.next_review_at : acc),
        null,
      )
      setResult({
        correct: res.updated.filter((u) => u.correct).length,
        mastered: res.updated.filter((u) => u.mastered).length,
        nextReviewAt: earliest ?? '',
      })
    } catch (e) {
      Taro.showToast({ title: (e as TaskError).message, icon: 'none' })
    } finally {
      setSubmitting(false)
    }
  }

  /** 返回错题本（列表页 useDidShow 自动刷新最新调度） */
  const backToList = () =>
    Taro.navigateBack({ fail: () => Taro.reLaunch({ url: '/pages/review/index' }) })

  // 结果态
  if (result) {
    return (
      <View className='page' style={pageStyle}>
        <NavBar />
        <View className='body'>
          <View className='result-card card'>
            <View className='result-icon'>✦</View>
            <Text className='result-title'>本轮重练完成</Text>
            <Text className='result-main'>答对 {result.correct} / {attempts.length} 题</Text>
            {result.mastered > 0 && (
              <Text className='result-mastered'>✦ 掌握 {result.mastered} 道错题，不再重复安排</Text>
            )}
            {result.nextReviewAt && (
              <Text className='result-next'>下次复习：{nextLabel(result.nextReviewAt)}</Text>
            )}
          </View>
          <Button className='btn-primary next-btn' onClick={backToList}>
            返回错题本 <Text className='arrow'>→</Text>
          </Button>
        </View>
      </View>
    )
  }

  // 无到期题（直接 URL 进入防御；列表页按钮已禁用+提示）
  if (items && items.length === 0) {
    return (
      <View className='page' style={pageStyle}>
        <NavBar />
        <View className='body'>
          <View className='empty'>
            <View className='empty-msg'>
              {error ? `加载失败：${error}` : '今日无待重温错题'}
            </View>
            {error ? (
              <Button className='btn-primary' onClick={load}>重新加载</Button>
            ) : (
              <Button className='btn-primary' onClick={backToList}>返回错题本</Button>
            )}
          </View>
        </View>
      </View>
    )
  }

  // 加载中
  if (!items || !item) {
    return (
      <View className='page' style={pageStyle}>
        <NavBar />
        <View className='body'>
          <View className='empty'><View className='empty-msg'>加载中…</View></View>
        </View>
      </View>
    )
  }

  return (
    <View className='page' style={pageStyle}>
      <NavBar />
      <View className='body'>
        <View className='quiz-top'>
          <Text className='count'>第 {current + 1} 题 / 共 {items.length} 题</Text>
          <Text className='type-tag'>{TYPE_LABEL[item.question.type]}</Text>
        </View>

        {/* 湖畔小径进度（石子数 = 本轮错题数） */}
        <View className='trail'>
          {items.map((it, i) => (
            <View key={it.id} className='trail-seg'>
              <View className={`stone ${i < current ? 'done' : i === current ? 'current' : ''}`} />
              {i < items.length - 1 && (
                <View className={`node ${i < current ? 'done' : i === current ? 'current' : ''}`} />
              )}
            </View>
          ))}
        </View>

        <Text className='question'>{item.question.question}</Text>

        {item.question.options.map((opt, index) => (
          <OptionRow
            key={index}
            letter={OPTION_KEYS[index]}
            text={opt}
            state={optionState(item, index, answered, selected)}
            picked={!answered && item.question.type === 'multiple' && selected.includes(index)}
            onClick={() => handleOptionClick(index)}
          />
        ))}

        {answered && (
          <>
            <View className={`verdict ${isCorrect ? 'right' : 'wrong'}`}>
              <View className='dot' />
              <Text className='t'>{isCorrect ? '答对了 · 记忆加固了一分' : '又错过了 · 按新节奏重逢'}</Text>
            </View>
            <View className='explain'>
              <View className='h'>知识讲解</View>
              <Text className='p'>{item.question.explanation}</Text>
              <Text className='kp'>知识点 · {item.question.knowledge_point}</Text>
            </View>
          </>
        )}

        {!answered && item.question.type === 'multiple' && (
          <Button className='btn-primary next-btn' onClick={() => submit(selected)}>提交答案</Button>
        )}
        {answered && (
          <Button className='btn-primary next-btn' onClick={handleNext} loading={submitting} disabled={submitting}>
            {submitting ? '提交中…' : (isLast ? '完成本轮' : '下一题')} <Text className='arrow'>→</Text>
          </Button>
        )}
      </View>
    </View>
  )
}

type OptionState = 'normal' | 'right' | 'wrong' | 'dim'

/** 选项展示状态：作答后正确选项高亮、错选标红、其余变淡（与闯关页一致） */
function optionState(
  item: ReviewItem,
  index: number,
  answered: boolean,
  selected: number[],
): OptionState {
  if (!answered) return 'normal'
  const isAnswer = item.question.answer.includes(index)
  const isSelected = selected.includes(index)
  if (isAnswer) return 'right'
  if (isSelected) return 'wrong'
  return 'dim'
}

interface OptionRowProps {
  letter: string
  text: string
  state: OptionState
  /** 多选提交前的勾选态 */
  picked?: boolean
  onClick: () => void
}

/** 选项行（与闯关页同构） */
function OptionRow({ letter, text, state, picked = false, onClick }: OptionRowProps) {
  return (
    <View className={`option ${state} ${picked ? 'picked' : ''}`} onClick={onClick}>
      <View className='key'>{letter}</View>
      <Text className='txt'>{text}</Text>
    </View>
  )
}
