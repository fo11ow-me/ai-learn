// 自绘导航栏（WHY：返回键需固定回首页，微信原生返回键仅返回上一页且页面栈单层时不可见，无法满足；返回键 switchTab 回首页）
export default definePageConfig({
  navigationStyle: 'custom'
})
