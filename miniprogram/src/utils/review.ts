/** 错题重练日期工具（错题本列表页与宝藏关卡共用；到期判定契约见方案设计文档-用户系统 5.2） */

/** 到期判定：next_review_at 当天 <= 今日即到期（可进宝藏关卡） */
export function isDue(iso: string): boolean {
  const d = new Date(iso)
  const today = new Date()
  d.setHours(0, 0, 0, 0)
  today.setHours(0, 0, 0, 0)
  return d.getTime() <= today.getTime()
}

/** 下次复习相对文案（原型「明日/后天/3 天后」；跨月显示 M/D） */
export function nextLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  d.setHours(0, 0, 0, 0)
  today.setHours(0, 0, 0, 0)
  const diff = Math.round((d.getTime() - today.getTime()) / 86400000)
  if (diff <= 0) return '今日'
  if (diff === 1) return '明日'
  if (diff === 2) return '后天'
  if (diff < 30) return `${diff} 天后`
  return `${d.getMonth() + 1}/${d.getDate()}`
}

/** 复习安排日期文案（明日起未来 7 天，跨周带星期） */
export function dayLabel(dateStr: string): string {
  const d = new Date(`${dateStr}T00:00:00`)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  d.setHours(0, 0, 0, 0)
  const diff = Math.round((d.getTime() - today.getTime()) / 86400000)
  if (diff === 1) return '明日'
  if (diff === 2) return '后天'
  return `${d.getMonth() + 1}/${d.getDate()}（周${'日一二三四五六'[d.getDay()]}）`
}
