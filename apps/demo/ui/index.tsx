import { useRef, useState, type FormEvent } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, FileText, ArrowUpRight, Download, Play, Square, Check, Clock, Loader2, Search, TerminalSquare } from 'lucide-react'
import { toast } from 'sonner'
import { api,type Task,type Result } from '@console/lib/api'
import { useAuth } from '@console/auth/auth-provider'
import { Button } from '@console/components/ui/button'
import { Input } from '@console/components/ui/input'
import { Label } from '@console/components/ui/label'
import { Textarea } from '@console/components/ui/textarea'
import { Card,CardContent } from '@console/components/ui/card'
import { Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription,DialogFooter } from '@console/components/ui/dialog'
import { Table,TableBody,TableCell,TableHead,TableHeader,TableRow } from '@console/components/ui/table'
import { TaskStatus } from '@console/components/task-status'

export default function DemoApp(){
  const {appId='demo'}=useParams()
  const {me,client}=useAuth()
  const queryClient=useQueryClient()
  const [search,setSearch]=useState('')
  const [params,setParams]=useSearchParams()
  const selected=params.get('task')
  const [createOpen,setCreateOpen]=useState(false)
  const [title,setTitle]=useState('文本分析任务')
  const [text,setText]=useState('')
  const [execute,setExecute]=useState(true)
  const requestKey=useRef(crypto.randomUUID())
  const base='/apps/'+appId+'/tasks'
  const tasks=useQuery({queryKey:['tasks',appId,me?.workspace_id],queryFn:()=>api<Task[]>(base),refetchInterval:1500})
  const detail=useQuery({queryKey:['task',appId,selected],queryFn:()=>api<Task>(base+'/'+selected),enabled:!!selected,refetchInterval:1200})
  const result=useQuery({queryKey:['result',appId,selected],queryFn:()=>api<Result>(base+'/'+selected+'/result'),enabled:detail.data?.status==='succeeded'})
  const can=(action:string)=>me?.permissions.includes(appId+'.task.'+action)
  const mutate=useMutation({mutationFn:({id,action}:{id:string;action:string})=>api<Task>(base+'/'+id+'/'+action,{method:'POST'}),onSuccess:()=>{queryClient.invalidateQueries({queryKey:['tasks']});queryClient.invalidateQueries({queryKey:['task']})},onError:e=>toast.error(e.message)})
  const create=useMutation({mutationFn:async()=>{
    const task=await api<Task>(base,{method:'POST',headers:{'Idempotency-Key':requestKey.current},body:JSON.stringify({title,text})})
    requestKey.current=crypto.randomUUID();setParams({task:task.id})
    if(execute&&can('run'))await api<Task>(base+'/'+task.id+'/run',{method:'POST'})
    return task
  },onSuccess:()=>{setCreateOpen(false);setText('');queryClient.invalidateQueries({queryKey:['tasks']});toast.success('任务已创建')},onError:e=>toast.error(e.message)})
  function submit(event:FormEvent){
    event.preventDefault()
    create.mutate()
  }
  async function download(){
    if(!result.data||!client)return
    const path=result.data.object_path.replace(/^artifacts\//,'')
    const {data,error}=await client.storage.from('artifacts').download(path)
    if(error){toast.error(error.message);return}
    const url=URL.createObjectURL(data);const link=document.createElement('a');link.href=url;link.download=title+'.json';link.click();URL.revokeObjectURL(url)
  }
  const rows=(tasks.data||[]).filter(t=>t.title.toLowerCase().includes(search.toLowerCase()))
  const task=detail.data
  return <div className="space-y-6"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="text-[11px] text-muted-foreground tracking-[.16em] mb-2">APPLICATION / {appId.toUpperCase()}</div><h1 className="text-[28px] font-semibold tracking-tight">文本分析</h1><p className="text-muted-foreground mt-2">从一段文本开始，验证节点执行与结果持久化。</p></div>{can('create')&&<Button onClick={()=>setCreateOpen(true)} data-testid="create-task"><Plus size={16}/>新建任务</Button>}</div>
    <div className="flex flex-wrap gap-5 text-xs text-muted-foreground"><span>规范化 → 统计分析 → 保存结果</span><span className="ml-auto flex items-center gap-1"><TerminalSquare size={13}/>CLI / HTTP / Web</span></div>
    <Card className="shadow-none"><CardContent className="pt-5"><div className="flex items-center gap-3 mb-4"><div className="relative max-w-xs w-full"><Search size={15} className="absolute left-3 top-2.5 text-muted-foreground"/><Input aria-label="搜索任务" className="pl-9 h-9" placeholder="搜索任务名称…" value={search} onChange={e=>setSearch(e.target.value)}/></div><span className="ml-auto text-xs text-muted-foreground">{rows.length} 条任务</span></div>
      {tasks.error?<p role="alert" className="text-destructive">{tasks.error.message}</p>:<Table><TableHeader><TableRow><TableHead>任务</TableHead><TableHead>状态</TableHead><TableHead>执行方式</TableHead><TableHead>创建时间</TableHead><TableHead/></TableRow></TableHeader><TableBody>{tasks.isLoading?<TableRow><TableCell colSpan={5} className="py-10 text-center text-muted-foreground">加载中…</TableCell></TableRow>:rows.length?rows.map(t=><TableRow key={t.id} className="cursor-pointer" data-testid="task-row" onClick={()=>setParams({task:t.id})}><TableCell><div className="flex items-center gap-3"><FileText size={17} className="text-primary"/><div><div className="font-medium">{t.title}</div><div className="text-[10px] text-muted-foreground mono">{t.id.slice(0,8)}</div></div></div></TableCell><TableCell><TaskStatus status={t.status}/></TableCell><TableCell className="text-xs text-muted-foreground">{t.mode==='local'?'本地 CLI':t.mode==='http'?'平台引擎':'—'}</TableCell><TableCell className="text-xs text-muted-foreground whitespace-nowrap">{new Date(t.created_at).toLocaleString('zh-CN')}</TableCell><TableCell><ArrowUpRight size={15} className="text-muted-foreground"/></TableCell></TableRow>):<TableRow><TableCell colSpan={5} className="text-center py-14 text-muted-foreground">没有任务。创建第一条任务开始。</TableCell></TableRow>}</TableBody></Table>}
    </CardContent></Card>
    {selected&&<Card className="shadow-none"><CardContent className="pt-6">{detail.error?<p role="alert" className="text-destructive">{detail.error.message}</p>:!task?<p>正在加载任务…</p>:<><div className="flex flex-wrap justify-between items-center gap-4"><div><h2 className="font-semibold text-lg">{task.title}</h2><p className="text-[11px] text-muted-foreground mono mt-1">{task.id}</p></div><div className="flex gap-3 items-center"><TaskStatus status={task.status}/>{can('run')&&['pending','failed','cancelled'].includes(task.status)&&<Button size="sm" disabled={mutate.isPending} onClick={()=>mutate.mutate({id:task.id,action:'run'})}><Play size={14}/>执行任务</Button>}{can('cancel')&&['pending','running'].includes(task.status)&&<Button size="sm" variant="outline" onClick={()=>mutate.mutate({id:task.id,action:'cancel'})}><Square size={13}/>取消任务</Button>}</div></div>
      <div className="grid sm:grid-cols-3 gap-3 my-6">{[['normalize','规范化文本'],['analyze','统计分析'],['save','保存结果']].map(([name,label],i)=>{const done=task.nodes?.some(n=>n.node===name);return <div key={name} className={'rounded-lg border p-4 flex gap-3 items-center '+(done?'bg-accent/60 border-primary/15':'bg-muted/20')}><div className={'rounded-full p-1.5 '+(done?'bg-primary/10 text-primary':'text-muted-foreground')}>{done?<Check size={14}/>:<Clock size={14}/>}</div><div><p className="text-xs font-medium">0{i+1} · {label}</p><p className="text-[10px] mt-1 text-muted-foreground">{done?'已完成':'等待执行'}</p></div></div>})}</div>
      {task.error&&<div role="alert" className="rounded-lg bg-red-50 text-red-700 text-xs p-4 mb-4">{task.error}</div>}
      <div className="grid lg:grid-cols-2 gap-5"><div><h3 className="text-xs font-medium mb-3">输入内容</h3><div className="bg-muted/50 rounded-lg p-4 text-xs leading-7 whitespace-pre-wrap">{String(task.input.text)}</div></div><div><div className="flex justify-between items-center mb-3"><h3 className="text-xs font-medium">执行结果</h3>{result.data&&<Button variant="ghost" size="sm" className="h-6 text-xs" onClick={download}><Download size={13}/>下载报告</Button>}</div><pre data-testid="task-result" className="bg-muted/50 rounded-lg p-4 text-[11px] leading-6 max-h-72 overflow-auto mono whitespace-pre-wrap">{result.data?JSON.stringify(result.data.report,null,2):task.status==='running'?'节点执行中…':'任务完成后将显示报告。'}</pre></div></div></> }</CardContent></Card>}
    <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent><DialogHeader><DialogTitle>新建文本分析任务</DialogTitle><DialogDescription>结果保存在工作空间中，只有授权成员可以访问。</DialogDescription></DialogHeader><form onSubmit={submit} className="space-y-5"><div className="space-y-2"><Label htmlFor="task-title">任务名称</Label><Input id="task-title" value={title} onChange={e=>{setTitle(e.target.value);requestKey.current=crypto.randomUUID()}} maxLength={120} required/></div><div className="space-y-2"><Label htmlFor="task-text">待分析文本</Label><Textarea id="task-text" value={text} onChange={e=>{setText(e.target.value);requestKey.current=crypto.randomUUID()}} placeholder="输入需要分析的文本…" className="min-h-36" maxLength={20000} required/></div>{can('run')&&<label className="flex gap-2 items-center text-xs text-muted-foreground"><input type="checkbox" checked={execute} onChange={e=>setExecute(e.target.checked)}/>创建后立即执行</label>}<DialogFooter><Button variant="outline" type="button" onClick={()=>setCreateOpen(false)}>取消</Button><Button type="submit" disabled={create.isPending}>{create.isPending&&<Loader2 className="animate-spin" size={15}/>}创建任务</Button></DialogFooter></form></DialogContent></Dialog>
  </div>
}
