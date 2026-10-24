/**
 * Studio 总览路由。
 *
 * 与首页同样的模式：页面本身是服务端壳，真正的内容组件用 dynamic 关闭 SSR，
 * 因为它依赖登录态和浏览器端的轮询。
 */

import dynamic from 'next/dynamic'

const StudioOverview = dynamic(() => import('./StudioOverview'), {
  ssr: false,
})

export default function StudioPage() {
  return <StudioOverview />
}
