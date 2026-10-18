import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Badge } from '@console/components/ui/badge'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@console/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@console/components/ui/table'

type Recognition = {source_id:string;stage_attempt_id:string;evidence_artifact_id:string;pcm_artifact_id:string;
  coverage:{chunk_count:number;empty_chunks:number;sample_coverage:number;scope:string};
  recognition:{language_hint:string;time_origin:string;semantic_review:string;boundary_review:string;
    pcm:{sample_count:number;sample_rate:number};session:{requests:number;warm_cache_checks:number;operation_stopped:boolean};
    chunks:{index:number;start_sample:number;end_sample:number;recognition:{text:string}}[]}}

export function SourceRecognition({projectId,runId,sourceId,status,canRun,identity}:{projectId:string;runId:string;sourceId:string;status:string;canRun:boolean;identity:string}){
  const [confirm,setConfirm]=useState(false),[language,setLanguage]=useState('English'),[expanded,setExpanded]=useState(false),[limit,setLimit]=useState(10)
  const requestKey=useRef(crypto.randomUUID()),queryClient=useQueryClient()
  const result=useQuery({queryKey:['deep-dub','source-asr',identity,projectId,sourceId],
    queryFn:()=>api<Recognition|null>(`/apps/deep_dub/projects/${projectId}/sources/${sourceId}/recognition`),staleTime:30000})
  const execute=useMutation({mutationFn:()=>api<{status:string}>(`/apps/deep_dub/runs/${runId}/recognize-source`,{
    method:'POST',headers:{'Idempotency-Key':requestKey.current},body:JSON.stringify({source_id:sourceId,language,chunk_seconds:60,timeout_seconds:3600})}),
    onSuccess:async data=>{await queryClient.invalidateQueries({queryKey:['deep-dub']});setConfirm(false);toast.success(data.status==='succeeded'?'来源识别证据已保存':'阶段状态：'+data.status)},
    onError:error=>toast.error(error.message)})
  const item=result.data
  return <section className="border-t pt-5 mt-5 space-y-3" aria-label="来源音轨识别">
    <div className="flex flex-wrap gap-2 items-center justify-between"><h3 className="text-sm font-medium">来源音轨识别</h3><Badge variant="outline">ASR 候选 · 待复核</Badge></div>
    <p className="text-xs text-muted-foreground leading-6">连续分块识别完整音轨，同一阶段内复用受控模型。不会自动创建分段、字幕或通过内容审核。</p>
    {result.error?<p role="alert" className="text-sm text-destructive">证据读取失败：{result.error.message}<Button size="sm" variant="ghost" onClick={()=>result.refetch()}>重试读取</Button></p>
      :result.isLoading?<p className="text-xs text-muted-foreground">正在读取来源识别证据…</p>
      :item?<>
        <p className="text-sm">{item.coverage.chunk_count} 个连续分块 · {(item.recognition.pcm.sample_count/item.recognition.pcm.sample_rate).toFixed(3)} 秒音轨 · {item.recognition.language_hint==='English'?'英语':'中文'}</p>
        <p className="text-xs text-muted-foreground">空文本块 {item.coverage.empty_chunks} · {item.recognition.session.warm_cache_checks} 次已加载模型检查 · 受控进程{item.recognition.session.operation_stopped?'已退出':'未确认退出'}</p>
        <p className="text-xs text-muted-foreground leading-6">{item.coverage.scope}。{item.recognition.time_origin}。</p>
        <details onToggle={event=>setExpanded(event.currentTarget.open)}><summary className="cursor-pointer text-xs">识别文本与分块范围</summary>{expanded&&<>
          <Table><TableHeader><TableRow><TableHead>音轨秒数</TableHead><TableHead>实际识别文本</TableHead></TableRow></TableHeader><TableBody>
            {item.recognition.chunks.slice(0,limit).map(chunk=><TableRow key={chunk.index}><TableCell className="text-xs font-mono align-top whitespace-nowrap">{(chunk.start_sample/16000).toFixed(2)}–{(chunk.end_sample/16000).toFixed(2)}</TableCell><TableCell className="text-xs whitespace-pre-wrap">{chunk.recognition.text||'（此块识别为空，不自动判为静音）'}</TableCell></TableRow>)}
          </TableBody></Table>
          {limit<item.recognition.chunks.length&&<Button variant="ghost" size="sm" onClick={()=>setLimit(n=>n+10)}>再显示 10 块</Button>}
          <p className="font-mono text-xs text-muted-foreground break-all mt-3">证据 {item.evidence_artifact_id}</p>
        </>}</details>
      </>:<p className="text-xs text-muted-foreground">尚无成功的来源识别证据；失败和取消尝试保留在运行记录中。</p>}
    <Button size="sm" variant="outline" disabled={!canRun||status!=='running'||execute.isPending} onClick={()=>setConfirm(true)}>识别完整来源音轨</Button>
    {(!canRun||status!=='running')&&<p className="text-xs text-muted-foreground">需要执行、评分和设备权限，以及本来源的执行中运行。</p>}
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>识别完整来源音轨</DialogTitle><DialogDescription>处理全部音频样本，可能耗时数分钟。同输入复用成功回执；识别文本仍需独立复核，分块范围不代表字幕时间。</DialogDescription></DialogHeader>
      <div className="flex gap-2"><Button variant={language==='English'?'default':'outline'} aria-pressed={language==='English'} disabled={execute.isPending} onClick={()=>{setLanguage('English');requestKey.current=crypto.randomUUID()}}>英语</Button><Button variant={language==='Chinese'?'default':'outline'} aria-pressed={language==='Chinese'} disabled={execute.isPending} onClick={()=>{setLanguage('Chinese');requestKey.current=crypto.randomUUID()}}>中文</Button></div>
      <p className="text-xs text-muted-foreground">执行中可在运行记录中取消整个运行；设备释放须等实际进程退出。</p>
      <DialogFooter><Button variant="outline" onClick={()=>setConfirm(false)}>{execute.isPending?'返回运行记录':'返回'}</Button><Button disabled={execute.isPending} onClick={()=>execute.mutate()}>{execute.isPending?'正在识别…':'确认执行'}</Button></DialogFooter>
    </DialogContent></Dialog>
  </section>
}
