import { useState } from 'react'
import { Button, Canvas, View } from '@tarojs/components'
import Taro, { useReady, useShareAppMessage } from '@tarojs/taro'
import { BASE_URL } from '../../config'
import { Quiz } from '../../types/quiz'
import { Report } from '../../types/report'
import './index.scss'

/** 海报画布尺寸（原型 .poster 宽 262px，等比绘制坐标） */
const POSTER_W = 262
const POSTER_H = 340
/** 左右内边距（原型 padding 24px） */
const PADDING_X = 24
/** 内容最大宽度 */
const CONTENT_W = POSTER_W - PADDING_X * 2
/** 品牌字距（原型 .brand letter-spacing .4em ≈ 4px） */
const BRAND_SPACING = 4
/** 金句字号/行高（原型 19px 衬线，行高 2） */
const QUOTE_FONT_SIZE = 19
const QUOTE_LINE_HEIGHT = 38

export default function PosterPage() {
  const [quiz] = useState<Quiz | null>(() => (Taro.getStorageSync('quiz_data') as Quiz) || null)
  const [report] = useState<Report | null>(() => (Taro.getStorageSync('report_data') as Report) || null)

  useShareAppMessage(() => ({
    title: report?.quote ? `「${quiz?.topic}」${report.quote}` : 'AI 闯关学习',
    path: '/pages/index/index',
  }))

  // canvas 在页面 ready 后可用：先下载后端生成的真二维码，再按画布实际尺寸等比绘制
  useReady(() => {
    if (!quiz || !report) return
    setTimeout(() => {
      Taro.downloadFile({
        url: `${BASE_URL}/qrcode`,
        success: (res) => {
          renderPoster(res.statusCode === 200 ? res.tempFilePath : null)
        },
        fail: () => renderPoster(null),
      })
    }, 100)
  })

  /** 测量画布实际尺寸并绘制（WHY：两端 canvas 实际像素尺寸不同，按 boundingClientRect 等比缩放绘制坐标） */
  const renderPoster = (qrPath: string | null) => {
    Taro.createSelectorQuery()
      .select('#poster')
      .boundingClientRect((rect) => {
        if (!rect || Array.isArray(rect)) return
        const ctx = Taro.createCanvasContext('poster')
        ctx.scale(rect.width / POSTER_W, rect.height / POSTER_H)
        drawPoster(ctx, quiz!.topic, report!.quote, report!.correct_rate, qrPath)
        ctx.draw()
      })
      .exec()
  }

  /** 保存海报到相册（授权拒绝时引导去设置开启） */
  const handleSave = () => {
    Taro.canvasToTempFilePath({
      canvasId: 'poster',
      success: (res) => {
        Taro.saveImageToPhotosAlbum({
          filePath: res.tempFilePath,
          success: () => Taro.showToast({ title: '已保存到相册', icon: 'success' }),
          fail: (err) => {
            if (err.errMsg.includes('auth') || err.errMsg.includes('authorize')) {
              Taro.showModal({
                title: '需要相册权限',
                content: '请在设置中开启"保存到相册"权限',
                confirmText: '去设置',
                success: (m) => {
                  if (m.confirm) Taro.openSetting()
                },
              })
            }
          },
        })
      },
    })
  }

  if (!quiz || !report) {
    return (
      <View className='page'>
        <View className='empty'>
          <View className='empty-msg'>海报数据丢失了，请先完成一次闯关</View>
          <Button className='btn-primary' onClick={() => Taro.reLaunch({ url: '/pages/index/index' })}>
            回到主页
          </Button>
        </View>
      </View>
    )
  }

  return (
    <View className='page'>
      <View className='body'>
        <View className='chapter'>CHAP. 04 · 分享</View>
        <View className='poster-stage'>
          <View className='poster-frame'>
            <Canvas id='poster' canvasId='poster' className='poster-canvas' />
          </View>
        </View>
        <View className='btn-row'>
          <Button className='btn-primary' onClick={handleSave}>保存到相册</Button>
          <Button className='btn-ghost' openType='share'>转发给好友</Button>
        </View>
      </View>
    </View>
  )
}

/**
 * 绘制海报（原型屏 7：深湖绿渐变底 + 品牌 + 水波纹 + 金句 + 二维码 + 扫码文案）
 * 注：旧版 canvas API 无圆角矩形，导出图为直角（预览经 CSS 圆角裁剪）；
 * 二维码由后端 /qrcode 生成（上线阶段替换为微信 getUnlimited 小程序码），下载失败时回退格纹占位
 */
function drawPoster(
  ctx: Taro.CanvasContext,
  topic: string,
  quote: string,
  rate: number,
  qrPath: string | null,
) {
  // 背景渐变（#2E4B3F → #23402F 70% → #1B3328）；H5 端 CanvasContext 不支持渐变，降级为中间色纯色
  let bg: string | Taro.CanvasGradient = '#23402F'
  try {
    const grad = ctx.createLinearGradient(0, 0, 0, POSTER_H)
    grad.addColorStop(0, '#2E4B3F')
    grad.addColorStop(0.7, '#23402F')
    grad.addColorStop(1, '#1B3328')
    bg = grad
  } catch (e) {
    // 降级纯色（H5 验证环境）
  }
  ctx.setFillStyle(bg)
  ctx.fillRect(0, 0, POSTER_W, POSTER_H)

  // 品牌（逐字绘制实现字距）
  ctx.setFillStyle('#A8C4B8')
  ctx.setFontSize(10)
  drawSpacedText(ctx, 'AI 闯关学习', PADDING_X, 26, BRAND_SPACING)

  // 水波纹装饰线
  ctx.setStrokeStyle('rgba(168, 196, 184, 0.6)')
  ctx.setLineWidth(1)
  ctx.beginPath()
  for (let x = 0; x <= POSTER_W; x += 20) {
    const y = 44 + Math.sin(x / 20) * 2
    if (x === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  }
  ctx.stroke()

  // 金句（衬线 19px，按实际测量宽度动态换行，行高 2）
  ctx.setFillStyle('#F2EDDD')
  const lines = wrapTextByWidth(ctx, quote, CONTENT_W, QUOTE_FONT_SIZE)
  lines.forEach((line, i) => {
    ctx.fillText(line, PADDING_X, 84 + i * QUOTE_LINE_HEIGHT)
  })

  // 来自「主题」闯关 · 正确率（超长主题按宽度截断加省略号，保证不溢出卡片）
  ctx.setFillStyle('#B9C9BF')
  ctx.setFontSize(11)
  const fromText = truncateByWidth(
    ctx,
    `—— 来自「${topic}」闯关 · 正确率 ${rate}%`,
    CONTENT_W,
  )
  ctx.fillText(fromText, PADDING_X, 84 + lines.length * QUOTE_LINE_HEIGHT + 14)

  // 二维码：优先真码（后端生成），失败回退格纹占位
  const qrX = (POSTER_W - 74) / 2
  const qrY = POSTER_H - 74 - 46
  ctx.setFillStyle('#F2EDDD')
  ctx.fillRect(qrX - 6, qrY - 6, 86, 86)
  if (qrPath) {
    ctx.drawImage(qrPath, qrX, qrY, 74, 74)
  } else {
    ctx.setFillStyle('#1B3328')
    ctx.fillRect(qrX, qrY, 74, 74)
    const cell = 74 / 5
    for (let row = 0; row < 5; row++) {
      for (let col = 0; col < 5; col++) {
        if ((row + col) % 2 === 0) {
          ctx.setFillStyle('#F2EDDD')
          ctx.fillRect(qrX + col * cell, qrY + row * cell, cell, cell)
        }
      }
    }
  }

  // 扫码文案（逐字绘制实现字距）
  ctx.setFillStyle('#B9C9BF')
  ctx.setFontSize(9)
  drawSpacedText(ctx, '扫码与我一起漫步湖畔', PADDING_X, POSTER_H - 26, 2.2)
}

/** 逐字绘制（旧版 canvas 无 letter-spacing 支持） */
function drawSpacedText(ctx: Taro.CanvasContext, text: string, x: number, y: number, spacing: number) {
  let cx = x
  for (const ch of text) {
    ctx.fillText(ch, cx, y)
    cx += ctx.measureText(ch).width + spacing
  }
}

/** 按实际测量宽度换行（WHY：canvas 字体回退后字宽与估算值有偏差，逐字测量保证不溢出） */
function wrapTextByWidth(
  ctx: Taro.CanvasContext,
  text: string,
  maxWidth: number,
  fontSize: number,
): string[] {
  ctx.setFontSize(fontSize)
  const lines: string[] = []
  let line = ''
  for (const ch of text) {
    if (ctx.measureText(line + ch).width > maxWidth) {
      lines.push(line)
      line = ch
    } else {
      line += ch
    }
  }
  if (line) lines.push(line)
  return lines
}

/** 按实际测量宽度截断并加省略号 */
function truncateByWidth(ctx: Taro.CanvasContext, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text
  let t = text
  while (t.length > 1 && ctx.measureText(t + '…').width > maxWidth) {
    t = t.slice(0, -1)
  }
  return t + '…'
}
