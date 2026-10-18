import { useRef,useState } from 'react'
import { useMutation,useQuery,useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Badge } from '@console/components/ui/badge'
import { Select,SelectContent,SelectItem,SelectTrigger,SelectValue } from '@console/components/ui/select'
import { Dialog,DialogContent,DialogDescription,DialogFooter,DialogHeader,DialogTitle } from '@console/components/ui/dialog'

type Result={stage_attempt_id:string;proof:{measurement:{model:{model_id:string};session:{requests:number;warm_cache_checks:number;operation_stopped:boolean};
  summary:{chunk_count:number;empty_chunks:number;primary_difference_count:number};chunks:{index:number;start_sample:number;end_sample:number;recognition:{text:string;words?:unknown[]}}[]};reference_comparison:{difference_count:number}|null}}

export function SourceIndependent({projectId,runId,sourceId,status,canRun,identity}:{projectId:string;runId:string;sourceId:string;status:string;canRun:boolean;identity:string}){
  const [confirm,setConfirm]=useState(false),[limit,setLimit]=useState(3),key=useRef(crypto.randomUUID()),cache=useQueryClient()
  const [recognizer,setRecognizer]=useState<'nemotron'|'parakeet'>('nemotron')
  const query=useQuery({queryKey:['deep-dub','source-independent',identity,projectId,sourceId,recognizer],
    queryFn:()=>api<Result|null>(`/apps/deep_dub/projects/${projectId}/sources/${sourceId}/independent-recognition?recognizer=${recognizer}`),staleTime:30000})
  const generate=useMutation({mutationFn:()=>api<{status:string}>(`/apps/deep_dub/runs/${runId}/recognize-source-independent`,{
    method:'POST',headers:{'Idempotency-Key':key.current},body:JSON.stringify({source_id:sourceId,timeout_seconds:3600,recognizer})}),
    onSuccess:async data=>{await cache.invalidateQueries({queryKey:['deep-dub']});setConfirm(false);toast.success(data.status==='succeeded'?'第二套识别证据已保存':'阶段状态：'+data.status)},onError:error=>toast.error(error.message)})
  const measured=query.data?.proof.measurement
  return <section className="rounded-lg border p-4 space-y-3" aria-label="第二套整轨识别">
    <div className="flex flex-wrap justify-between gap-2"><h4 className="text-sm font-medium">第二套整轨识别</h4><Badge variant="outline">不同声学模型 · 内容仍待审</Badge></div>
    <Select value={recognizer} disabled={generate.isPending} onValueChange={v=>{setRecognizer(v as 'nemotron'|'parakeet');setLimit(3);key.current=crypto.randomUUID()}}><SelectTrigger aria-label="补充盲转录模型"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="nemotron">Nemotron · 保留既有识别证据</SelectItem><SelectItem value="parakeet">Parakeet-TDT · 独立转录与原生词时间</SelectItem></SelectContent></Select>
    <p className="text-xs text-muted-foreground leading-6">使用所选声学模型对同一完整 PCM 连续识别，不接收 Qwen 原稿或官方参考提示。各模型证据分别保留；即使一致，也不能直接判事实、角色或字幕通过。</p>
    {query.error&&<p role="alert" className="text-xs text-destructive">{query.error.message}</p>}
    {measured&&<><p className="text-xs">{measured.summary.chunk_count} 个连续块 · 空文本 {measured.summary.empty_chunks} · {measured.session.requests} 次请求 · 受控模型{measured.session.operation_stopped?'已退出':'未确认退出'}</p>
      <p className="text-xs text-muted-foreground">与第一套识别 {measured.summary.primary_difference_count} 处差异 · 与官方参考 {query.data?.proof.reference_comparison?.difference_count??'未比较'} 处差异 · {measured.model.model_id}</p>
      {recognizer==='parakeet'&&<p className="text-xs text-muted-foreground">原生词时间 {measured.chunks.reduce((n,c)=>n+(c.recognition.words?.length||0),0)} 项；只核对文本覆盖与样本边界，实际字幕边界仍待审核。</p>}
      <details><summary className="cursor-pointer text-xs">查看第二套完整候选</summary><div className="space-y-3 pt-3">{measured.chunks.slice(0,limit).map(chunk=><div key={chunk.index} className="rounded border p-3 text-xs leading-6"><p className="font-medium">块 {chunk.index+1} · {(chunk.start_sample/16000).toFixed(3)}–{(chunk.end_sample/16000).toFixed(3)} 秒</p><p className="whitespace-pre-wrap">{chunk.recognition.text||'（无文本候选）'}</p></div>)}{limit<measured.chunks.length&&<Button variant="ghost" size="sm" onClick={()=>setLimit(v=>v+10)}>再显示 10 个识别块</Button>}</div></details></>}
    <Button variant="outline" size="sm" disabled={!canRun||status!=='waiting_review'||generate.isPending} onClick={()=>setConfirm(true)}>{generate.isPending?'第二套模型识别中…':'运行第二套完整识别'}</Button>
    <p className="text-xs text-muted-foreground">仅用于本轮待审来源，同输入复用冻结证据。取消运行或撤权会停止本次租约内的模型；不自动改稿或登记分段。</p>
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>运行第二套整轨识别</DialogTitle><DialogDescription>会在本机受控 GPU 上加载第二套声学模型，识别本来源完整原始 PCM，并保存跨模型/官方参考差异。不会修改第一套文本或原词时间，结果仍需独立审核。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={()=>setConfirm(false)}>返回</Button><Button disabled={generate.isPending} onClick={()=>generate.mutate()}>开始第二套识别</Button></DialogFooter></DialogContent></Dialog>
  </section>
}
