import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Boxes, CheckCircle2, Activity, TerminalSquare, GitBranch } from 'lucide-react'
import { applications } from '@/generated/applications'
import { useAuth } from '@/auth/auth-provider'
import { api,type Task } from '@/lib/api'
import { Card,CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { TaskStatus } from '@/components/task-status'

export default function Overview(){
  const {me}=useAuth()
  const apps=applications.filter(a=>me?.permissions.includes(a.id+'.task.read'))
  const query=useQuery({queryKey:['overview',me?.workspace_id],queryFn:async()=>
    (await Promise.all(apps.map(a=>api<Task[]>('/apps/'+a.id+'/tasks')))).flat(),refetchInterval:3000})
  const tasks=query.data||[]
  const recent=[...tasks].sort((a,b)=>b.created_at.localeCompare(a.created_at)).slice(0,6)
  const cards=[{title:'可用应用',value:apps.length,icon:Boxes,sub:'按当前角色授权'},
    {title:'任务总数',value:tasks.length,icon:GitBranch,sub:'当前工作空间最近任务'},
    {title:'正在执行',value:tasks.filter(t=>t.status==='running').length,icon:Activity,sub:'本地或平台运行'},
    {title:'已完成',value:tasks.filter(t=>t.status==='succeeded').length,icon:CheckCircle2,sub:'结果已持久化保存'}]
  return <div className="space-y-8"><div><div className="text-[11px] tracking-[.18em] text-muted-foreground mb-2">WORKSPACE OVERVIEW</div><h1 className="text-[28px] font-semibold tracking-tight">你的应用，从这里开始。</h1><p className="text-muted-foreground mt-2">管理任务、运行流程，连接 AI 与业务。</p></div>
    <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4">{cards.map(c=><Card key={c.title} className="shadow-none"><CardContent className="pt-6"><div className="flex items-center justify-between text-muted-foreground text-xs">{c.title}<c.icon size={17}/></div><div className="text-3xl font-semibold mt-3 mb-2">{query.isLoading?'—':c.value}</div><p className="text-[11px] text-muted-foreground">{c.sub}</p></CardContent></Card>)}</div>
    <div className="grid xl:grid-cols-[1fr_330px] gap-6"><Card className="shadow-none"><CardContent className="pt-6"><div className="flex justify-between items-center mb-5"><h2 className="font-semibold">最近任务</h2><span className="text-[11px] text-muted-foreground">自动刷新</span></div>{query.error?<p role="alert" className="text-destructive">{query.error.message}</p>:recent.length?recent.map(t=><Link to={'/apps/'+t.app_id+'?task='+t.id} key={t.id} className="flex items-center gap-4 py-4 border-t hover:bg-muted/40"><div className="rounded-lg bg-muted p-2 text-muted-foreground"><GitBranch size={17}/></div><div className="min-w-0 flex-1"><p className="text-sm truncate">{t.title}</p><p className="text-[11px] text-muted-foreground mt-1">{new Date(t.created_at).toLocaleString('zh-CN')} · {t.mode==='local'?'本地执行':t.mode==='http'?'平台执行':t.mode==='studio'?'Studio 调试':'尚未运行'}</p></div><TaskStatus status={t.status}/><ArrowUpRight size={14} className="text-muted-foreground"/></Link>):<div className="text-center py-12 text-muted-foreground">还没有任务，打开一个应用开始。</div>}</CardContent></Card>
    <Card className="shadow-none bg-[#e9f1e9] dark:bg-[#253d30] border-[#d8e5d9]"><CardContent className="pt-7"><TerminalSquare className="text-primary mb-5" size={26}/><h2 className="font-semibold text-lg">CLI 是一等入口</h2><p className="text-xs text-muted-foreground leading-6 mt-3">默认直接执行本机业务代码，也可以切换 HTTP 调用。两种模式共享登录身份与业务权限。</p><pre className="text-[11px] bg-white/60 dark:bg-black/15 rounded-lg p-4 mt-5 overflow-auto mono">uv run anp auth login{'\n'}uv run anp demo tasks list</pre><p className="text-[11px] mt-5 text-muted-foreground">本地执行无需启动业务 Web 服务。</p></CardContent></Card></div>
    <section><h2 className="font-semibold mb-4">应用入口</h2><div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">{apps.map(a=><Link to={'/apps/'+a.id} key={a.id}><Card className="shadow-none hover:border-primary/40 transition-colors"><CardContent className="pt-6 flex items-center gap-4"><div className="bg-accent text-primary rounded-xl p-3"><Boxes size={21}/></div><div className="flex-1"><h3 className="font-medium">{a.name}</h3><p className="text-xs text-muted-foreground mt-1">打开应用工作区</p></div><ArrowUpRight size={17}/></CardContent></Card></Link>)}</div></section>
  </div>
}
