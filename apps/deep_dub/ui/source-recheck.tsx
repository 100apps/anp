import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Badge } from '@console/components/ui/badge'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@console/components/ui/dialog'

type Result={stage_attempt_id:string;evidence_artifact_id:string;proof:{measurement:{elapsed_seconds:number;session:{requests:number;operation_stopped:boolean};
  summary:{window_count:number;empty_windows:number};windows:{boundary_index:number;start_sample:number;end_sample:number;preceding_text:string;following_text:string;recognition:{text:string}}[]}}}

export function SourceRecheck({projectId,runId,sourceId,status,canRun,identity}:{projectId:string;runId:string;sourceId:string;status:string;canRun:boolean;identity:string}){
  const [confirm,setConfirm]=useState(false),[limit,setLimit]=useState(3)
  const key=useRef(crypto.randomUUID()),cache=useQueryClient()
  const result=useQuery({queryKey:['deep-dub','source-recheck',identity,projectId,sourceId],
    queryFn:()=>api<Result|null>(`/apps/deep_dub/projects/${projectId}/sources/${sourceId}/boundary-recheck`),staleTime:30000})
  const generate=useMutation({mutationFn:()=>api<{status:string}>(`/apps/deep_dub/runs/${runId}/recheck-source-boundaries`,{
    method:'POST',headers:{'Idempotency-Key':key.current},body:JSON.stringify({source_id:sourceId,timeout_seconds:1800})}),
    onSuccess:async data=>{await cache.invalidateQueries({queryKey:['deep-dub']});setConfirm(false);toast.success(data.status==='succeeded'?'接缝候选已保存，仍需独立复核':'阶段状态：'+data.status)},onError:error=>toast.error(error.message)})
  const measured=result.data?.proof.measurement
  return <section aria-label="来源接缝返修" className="rounded-lg border p-4 space-y-3">
    <div className="flex flex-wrap justify-between gap-2"><h4 className="text-sm font-medium">来源接缝返修</h4><Badge variant="outline">改窗重识别 · 不自动改稿</Badge></div>
    <p className="text-xs text-muted-foreground leading-6">对每个固定分块接缝前后各 6 秒原始 PCM 重新识别，原稿与官方参考不作为模型提示。仍为同一 ASR 模型的额外候选，不能当作独立内容审核。</p>
    {result.error&&<p role="alert" className="text-xs text-destructive">{result.error.message}</p>}
    {measured&&<><p className="text-xs">{measured.summary.window_count} 个窗口 · 空候选 {measured.summary.empty_windows} · {measured.session.requests} 次请求 · 受控模型{measured.session.operation_stopped?'已退出':'未确认退出'}</p>
      <details><summary className="cursor-pointer text-xs">对照原接缝与新的声学候选</summary><div className="space-y-3 pt-3">{measured.windows.slice(0,limit).map(row=><div key={row.boundary_index} className="rounded border p-3 space-y-2 text-xs leading-6">
        <p className="font-medium">接缝 {row.boundary_index} · {(row.start_sample/16000).toFixed(3)}–{(row.end_sample/16000).toFixed(3)} 秒</p>
        <p className="text-muted-foreground">原前块末尾：{row.preceding_text.slice(-240)}</p><p className="text-muted-foreground">原后块开头：{row.following_text.slice(0,240)}</p>
        <p>改窗候选：{row.recognition.text||'（未识别出文本）'}</p></div>)}{limit<measured.windows.length&&<Button variant="ghost" size="sm" onClick={()=>setLimit(v=>v+10)}>再显示 10 个接缝</Button>}</div></details></>}
    <Button variant="outline" size="sm" disabled={!canRun||status!=='waiting_review'||generate.isPending} onClick={()=>setConfirm(true)}>{generate.isPending?'正在重识别接缝…':'重识别全部来源接缝'}</Button>
    <p className="text-xs text-muted-foreground">仅限本轮待审来源，仍需要运行、评分和设备权限。运行取消会通过租约停止本次模型；原计时与待审门禁保持。</p>
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>重识别来源接缝</DialogTitle><DialogDescription>使用本机受控 GPU，在同一租约内处理全部接缝并保存独立证据产物。不会覆盖原稿、裁剪词时间、建立正式分段或自动放行生产。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={()=>setConfirm(false)}>返回</Button><Button disabled={generate.isPending} onClick={()=>generate.mutate()}>开始接缝返修</Button></DialogFooter></DialogContent></Dialog>
  </section>
}
