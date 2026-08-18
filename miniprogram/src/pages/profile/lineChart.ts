/**
 * 折线图布局计算（WHY：坐标/线段/数字标注全部为纯函数计算，视觉一致且可单测）
 * 坐标系为 rpx 设计稿坐标（750rpx 恒等于屏宽；图表区宽 = 750 − body padding 44×2 − chart padding 24×2 − 图表 margin 8×2 = 598rpx）
 */

// 图表区宽 = 750 - body padding 44×2 - chart padding 24×2 - 图表 margin 8×2（WHY：rpx 绝对坐标必须与真实布局一致，否则点会偏移出卡片）
export const CHART_W = 598
export const CHART_H = 168 // 图表区高（rpx，与原柱状图一致）
export const PAD_X = 30 // 左右边距（rpx，容纳点与数字标注；两位数字居中后边缘 ≥ 15rpx）
export const TOP = 48 // 顶部数字预留区（rpx）= 数字行高28 + 点半径3.5 + 间隙12 + 余量4.5：最高点 y，数字顶边恰在框内

export interface ChartPoint {
  x: number // 点中心 x（rpx）
  y: number // 点中心 y（rpx，0 = 图表区顶部）
  value: number // 当日答题数
  digitBottom: number // 数字标注底边距图表区底部的距离（rpx，自底起算，与 CSS bottom 一致）
}

export interface ChartSegment {
  x: number // 线段起点 x（= 前一点中心）
  y: number // 线段起点 y
  length: number // 两点距离（rpx）
  angleDeg: number // 倾角（度，atan2 结果）
}

export interface ChartLayout {
  points: ChartPoint[]
  segments: ChartSegment[]
}

export function computeLineChart(counts: number[]): ChartLayout {
  const max = Math.max(...counts, 1) // 全 0 兜底（WHY：max=0 时归一化除零产生 NaN）
  const n = counts.length
  const span = n > 1 ? n - 1 : 1 // 单点防御（WHY：除零）
  const points: ChartPoint[] = counts.map((value, i) => {
    const x = PAD_X + ((CHART_W - PAD_X * 2) * i) / span
    const y = TOP + (1 - value / max) * (CHART_H - TOP - 8) // 底部留 8rpx（星期标签在图表区外独立一行）
    // 数字底边（自底起算，与 CSS bottom 一致）= CHART_H - (点顶上方 12rpx 间隙 + 点半径 3.5)（WHY：TOP=48 已保证峰值数字顶边 ≥ 0，无需 clamp）
    const digitBottom = CHART_H - (y - 15.5)
    return { x, y, value, digitBottom }
  })
  const segments: ChartSegment[] = []
  for (let i = 0; i < n - 1; i++) {
    const a = points[i]
    const b = points[i + 1]
    const dx = b.x - a.x
    const dy = b.y - a.y
    segments.push({ x: a.x, y: a.y, length: Math.hypot(dx, dy), angleDeg: (Math.atan2(dy, dx) * 180) / Math.PI })
  }
  return { points, segments }
}
