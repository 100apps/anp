import { useState, type FormEvent } from 'react'
import { ArrowUpRight, Layers3, GitBranch, TerminalSquare, ShieldCheck, Loader2 } from 'lucide-react'
import { useAuth } from '@/auth/auth-provider'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export default function Login(){
  const {client}=useAuth()
  const [email,setEmail]=useState('')
  const [password,setPassword]=useState('')
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  async function submit(event:FormEvent){
    event.preventDefault();setBusy(true);setError('')
    const result=await client!.auth.signInWithPassword({email,password})
    if(result.error)setError('邮箱或密码不正确，请重新输入。')
    setBusy(false)
  }
  return <div className="min-h-screen grid lg:grid-cols-2 bg-card">
    <section className="hidden lg:flex flex-col justify-between bg-[#142f27] p-14 text-[#e9f0e8] relative overflow-hidden">
      <div className="absolute h-[650px] w-[650px] rounded-full border border-white/5 -left-60 top-40"/><div className="absolute h-[450px] w-[450px] rounded-full border border-white/5 -right-60 -bottom-30"/>
      <div className="flex items-center gap-3 relative"><div className="rounded-xl bg-[#98c5a4] p-2 text-[#173a2b]"><Layers3 size={24}/></div><strong className="tracking-[.2em] text-xl">ANP</strong><span className="text-white/45 text-sm">应用工作台</span></div>
      <div className="relative max-w-lg"><div className="text-xs text-[#a7c5af] tracking-[.25em] mb-5">AI NATIVE · LOCAL FIRST</div><h1 className="text-5xl font-semibold leading-tight tracking-tight">让想法进入流程，<br/>让执行留下结果。</h1><p className="mt-7 text-white/55 leading-8">从本地命令到图执行，从节点开发到任务追踪。<br/>用一个工作空间，连接你的应用与 AI 基础设施。</p>
      <div className="mt-12 grid grid-cols-3 gap-5 border-t border-white/10 pt-8">{[[TerminalSquare,'CLI 优先'],[GitBranch,'可编排执行'],[ShieldCheck,'统一权限']].map(([Icon,label])=><div key={String(label)} className="text-sm text-white/70"><Icon className="mb-3 text-[#9cc9a9]" size={20}/>{String(label)}</div>)}</div></div>
      <p className="text-xs text-white/35 relative">为开发者构建，也为 AI Agent 准备。</p>
    </section>
    <section className="flex items-center justify-center p-8"><div className="w-full max-w-sm">
      <div className="lg:hidden flex gap-2 items-center mb-12 font-bold tracking-widest"><Layers3/>ANP</div>
      <div className="text-xs uppercase tracking-widest text-muted-foreground mb-3">欢迎回来</div><h2 className="text-3xl font-semibold tracking-tight">登录工作台</h2><p className="text-muted-foreground mt-3 mb-9">使用工作空间账号继续。</p>
      <form onSubmit={submit} className="space-y-5"><div className="space-y-2"><Label htmlFor="email">邮箱</Label><Input id="email" type="email" autoComplete="username" placeholder="you@example.com" value={email} onChange={e=>setEmail(e.target.value)} required className="h-11"/></div><div className="space-y-2"><Label htmlFor="password">密码</Label><Input id="password" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required className="h-11"/></div>
      {error&&<p role="alert" className="text-destructive text-sm">{error}</p>}<Button type="submit" className="w-full h-11" disabled={busy||!client}>{busy?<Loader2 className="animate-spin"/>:<>登录<ArrowUpRight size={16}/></>}</Button></form>
      <div className="mt-8 border-t pt-5 text-xs text-muted-foreground leading-6">账号由工作空间管理员分配。<br/>首次部署的管理员登录信息位于本机配置文件。</div>
    </div></section>
  </div>
}
