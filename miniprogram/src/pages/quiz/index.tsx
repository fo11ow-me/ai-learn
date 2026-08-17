import { useMemo, useRef, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useStatusBarHeight } from '../../hooks/useStatusBarHeight'
import { Question, Quiz } from '../../types/quiz'
import { AnswerRecord } from '../../types/report'
import './index.scss'

/** 题型显示名（原型题型标签） */
const TYPE_LABEL: Record<Question['type'], string> = {
  single: '单选题',
  multiple: '多选题',
  judge: '判断题',
}

/** 选项序号（所有题型统一 A-D，判断题 A/B，对齐原型屏 5） */
const OPTION_KEYS = ['A', 'B', 'C', 'D']

export default function QuizPage() {
  // quiz 数据经 Storage 从主页传入（方案文档 3.5 页面数据流）
  const [quiz] = useState<Quiz | null>(() => (Taro.getStorageSync('quiz_data') as Quiz) || null)
  const [current, setCurrent] = useState(0)
  const [selected, setSelected] = useState<number[]>([])
  const [answered, setAnswered] = useState(false)
  const [answers, setAnswers] = useState<AnswerRecord[]>([])
  const startTimeRef = useRef(Date.now()) // 进入页面计时，报告页展示用时
  const statusBarHeight = useStatusBarHeight()
  const pageStyle = { paddingTop: `${statusBarHeight}px` }

  const question: Question | undefined = quiz?.questions[current]
  const isLast = quiz !== null && current === quiz.questions.length - 1

  // 判分（纯前端）：全选正确才判对，与后端 report 计算规则一致（sorted 集合相等）
  const isCorrect = useMemo(() => {
    if (!answered || !question) return false
    return [...selected].sort().join(',') === [...question.answer].sort().join(',')
  }, [answered, selected, question])

  /** 选项点击：单选/判断点击即判分；多选先勾选再点"提交答案" */
  const handleOptionClick = (index: number) => {
    if (answered || !question) return
    if (question.type === 'multiple') {
      setSelected((prev) =>
        prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index],
      )
      return
    }
    submit([index])
  }

  /** 判分并记录作答 */
  const submit = (finalSelected: number[]) => {
    if (!question || answered) return
    if (finalSelected.length === 0) {
      Taro.showToast({ title: '请选择答案', icon: 'none' })
      return
    }
    setSelected(finalSelected)
    setAnswered(true)
    setAnswers((prev) => [...prev, { question_id: question.id, selected: finalSelected }])
  }

  /** 下一题 / 查看报告 */
  const handleNext = () => {
    if (!isLast) {
      setCurrent((c) => c + 1)
      setSelected([])
      setAnswered(false)
      return
    }
    Taro.setStorageSync('quiz_answers', answers)
    Taro.setStorageSync('quiz_duration', Math.round((Date.now() - startTimeRef.current) / 1000))
    Taro.navigateTo({ url: '/pages/report/index' })
  }

  // 无数据（异常入口）→ 引导回主页
  if (!quiz || !question) {
    return (
      <View className='page' style={pageStyle}>
        <View className='empty'>
          <View className='empty-msg'>题目数据丢失了，请回主页重新生成</View>
          <Button className='btn-primary' onClick={() => Taro.reLaunch({ url: '/pages/index/index' })}>
            回到主页
          </Button>
        </View>
      </View>
    )
  }

  return (
    <View className='page' style={pageStyle}>
      <View className='body'>
        <View className='quiz-top'>
          <Text className='count'>第 {current + 1} 题 / 共 {quiz.questions.length} 题</Text>
          <Text className='type-tag'>{TYPE_LABEL[question.type]}</Text>
        </View>

        {/* 湖畔小径进度：5 个石子节点 + 胶囊轨道 */}
        <View className='trail'>
          {quiz.questions.map((q, i) => (
            <View key={q.id} className='trail-seg'>
              <View
                className={`stone ${i < current ? 'done' : i === current ? 'current' : ''}`}
              />
              {i < quiz.questions.length - 1 && (
                <View
                  className={`node ${i < current ? 'done' : i === current ? 'current' : ''}`}
                />
              )}
            </View>
          ))}
        </View>

        <Text className='question'>{question.question}</Text>

        {question.options.map((opt, index) => (
          <OptionRow
            key={index}
            letter={OPTION_KEYS[index]}
            text={opt}
            state={optionState(question, index, answered, selected)}
            picked={!answered && question.type === 'multiple' && selected.includes(index)}
            onClick={() => handleOptionClick(index)}
          />
        ))}

        {answered && (
          <>
            <View className={`verdict ${isCorrect ? 'right' : 'wrong'}`}>
              <View className='dot' />
              <Text className='t'>{isCorrect ? '答对了 · 涟漪泛起' : '这条小径，值得再走一遍'}</Text>
            </View>
            <View className='explain'>
              <View className='h'>知识讲解</View>
              <Text className='p'>{question.explanation}</Text>
              <Text className='kp'>知识点 · {question.knowledge_point}</Text>
            </View>
          </>
        )}

        {(!answered && question.type === 'multiple') && (
          <Button className='btn-primary next-btn' onClick={() => submit(selected)}>提交答案</Button>
        )}
        {answered && (
          <Button className='btn-primary next-btn' onClick={handleNext}>
            {isLast ? '查看报告' : '下一题'} <Text className='arrow'>→</Text>
          </Button>
        )}
      </View>
    </View>
  )
}

type OptionState = 'normal' | 'right' | 'wrong' | 'dim'

/** 选项展示状态：作答后正确选项高亮、错选标红、其余变淡 */
function optionState(
  question: Question,
  index: number,
  answered: boolean,
  selected: number[],
): OptionState {
  if (!answered) {
    // 多选未提交时的勾选态由 normal + 勾选样式体现，由调用方另行处理
    return 'normal'
  }
  const isAnswer = question.answer.includes(index)
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

/** 选项行（原型 .option：key 方块 + 文本；作答后 right/wrong/dim 态） */
function OptionRow({ letter, text, state, picked = false, onClick }: OptionRowProps) {
  return (
    <View className={`option ${state} ${picked ? 'picked' : ''}`} onClick={onClick}>
      <View className='key'>{letter}</View>
      <Text className='txt'>{text}</Text>
    </View>
  )
}
