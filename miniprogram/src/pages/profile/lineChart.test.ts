import { describe, expect, it } from 'vitest'
import { CHART_H, CHART_W, PAD_X, TOP, computeLineChart } from './lineChart'

const COUNTS = [2, 0, 5, 3, 4, 1, 3]

describe('computeLineChart 折线图布局', () => {
  it('7 个点 x 均匀分布且首尾对称', () => {
    const { points } = computeLineChart(COUNTS)
    expect(points.length).toBe(7)
    expect(points[0].x).toBe(PAD_X)
    expect(points[6].x).toBe(CHART_W - PAD_X)
    // 相邻间距相等
    const gaps = points.slice(1).map((p, i) => p.x - points[i].x)
    expect(gaps.every((g) => Math.abs(g - gaps[0]) < 1e-9)).toBe(true)
  })

  it('值映射：峰值贴 TOP（数字预留区），0 值贴底', () => {
    const { points } = computeLineChart(COUNTS)
    expect(points[2].value).toBe(5) // 最大值
    expect(points[2].y).toBe(TOP)
    expect(points[1].value).toBe(0)
    expect(points[1].y).toBe(CHART_H - 8) // BOTTOM=8 底部留白
  })

  it('数字不越上边界：峰值数字顶边在框内（digitBottom 自底起算）', () => {
    const { points } = computeLineChart(COUNTS)
    const peak = points[2] // 最大值 5
    expect(peak.y).toBe(TOP)
    // 自底起算（与 CSS bottom 一致）：168 - (48 - 15.5) = 135.5；数字底边 = 点顶上方 12rpx 间隙 + 点半径 3.5
    expect(peak.digitBottom).toBeCloseTo(CHART_H - TOP + 15.5)
    // 数字顶边（自顶起算）= CHART_H - digitBottom - 行高28 = 4.5 ≥ 0：完整在图表区内，余量由 TOP=48 预留
    expect(CHART_H - peak.digitBottom - 28).toBeGreaterThanOrEqual(0)
  })

  it('6 条线段首尾相连，长度等于两点欧氏距离，倾角正确', () => {
    const { points, segments } = computeLineChart(COUNTS)
    expect(segments.length).toBe(6)
    segments.forEach((s, i) => {
      const a = points[i]
      const b = points[i + 1]
      expect(s.x).toBe(a.x)
      expect(s.y).toBe(a.y)
      expect(s.length).toBeCloseTo(Math.hypot(b.x - a.x, b.y - a.y))
      expect(s.angleDeg).toBeCloseTo((Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI)
    })
  })

  it('全 0 数据兜底（max 取 1，不除零、无 NaN）', () => {
    const { points, segments } = computeLineChart([0, 0, 0, 0, 0, 0, 0])
    points.forEach((p) => {
      expect(Number.isFinite(p.x)).toBe(true)
      expect(Number.isFinite(p.y)).toBe(true)
    })
    expect(segments.length).toBe(6)
  })
})
