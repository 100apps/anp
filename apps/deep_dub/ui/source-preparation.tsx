import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Badge } from '@console/components/ui/badge'
import { Input } from '@console/components/ui/input'
import { Label } from '@console/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@console/components/ui/dialog'

type Preparation={procedure_id:string;thread_id:string;business_status:string;engine_status:string;request_key:string;
  parameters:{reference_url?:string|null};source_steps:Record<string,{status:string;attempt_id?:string}>;technical_verified_at:string|null}
const steps=[['acquire','获取原片'],['decode','完整解码'],['recognize','整轨识别'],['align','词时间'],['reference','官方参考']]

export function SourcePreparation({runId,status,sourceId,canRun,identity}:{runId:string;status:string;sourceId:string|null;canRun:boolean;identity:string}){
  const [confirm,setConfirm]=useState(false),[reference,setReference]=useState(''),key=useRef(crypto.randomUUID()),query=useQueryClient()
  const result=useQuery({queryKey:['deep-dub','source-preparation',identity,runId,status],
    queryFn:()=>api<Preparation|null>(`/apps/deep_dub/runs/${runId}/source-preparation`),
    refetchInterval:state=>['pending','running'].includes(state.state.data?.engine_status||'')?2000:false})
  const item=result.data,waiting=item?.business_status==='waiting_review',retry=!!item&&['error','timeout'].includes(item.engine_status)
  const launch=useMutation({mutationFn:()=>api(`/apps/deep_dub/runs/${runId}/source-preparation`,{method:'POST',
    headers:{'Idempotency-Key':item?.request_key||key.current},body:JSON.stringify({reference_url:item?.parameters.reference_url??(reference.trim()||null),ttl_seconds:600})}),
    onSuccess:async()=>{await query.invalidateQueries({queryKey:['deep-dub']});setConfirm(false);toast.success('来源准备已交给平台编排')},onError:error=>toast.error(error.message)})
  return <section className="mt-5 border-t pt-5 space-y-3" aria-label="来源准备编排">
    <div className="flex justify-between items-center gap-2"><h3 className="text-sm font-medium">来源准备编排</h3><Badge variant="outline">{status==='cancelled'?'已取消':waiting?'待独立复核':item?.engine_status==='interrupted'?'已中断等待':item?'平台持久化线程':'尚未启动'}</Badge></div>
    <p className="text-xs text-muted-foreground leading-6">串行获取原片、完整解码、识别、词时间与可选官方参考。准备完成会停在复核门禁，不会自动把候选文本变成字幕或整片通过。</p>
    {result.error?<p role="alert" className="text-xs text-destructive">{result.error.message}<Button size="sm" variant="ghost" onClick={()=>result.refetch()}>重试读取</Button></p>:item?<>
      <ol className="grid grid-cols-2 sm:grid-cols-5 gap-2">{steps.map(([name,title])=><li key={name} className="border rounded-md p-2 text-xs"><p>{title}</p><p className="mt-2 text-muted-foreground">{item.source_steps[name]?.status==='succeeded'?'已完成':item.source_steps[name]?.status==='skipped'?'未配置，已跳过':'尚未取得回执'}</p></li>)}</ol>
      {waiting&&<p className="text-xs text-amber-700 leading-6">来源证据已准备，正文、角色与词边界仍需复核。此运行没有整片技术通过时间；客户端不能用“继续”代替质量审核。</p>}
      <details><summary className="text-xs cursor-pointer">线程与流程标识</summary><p className="text-xs font-mono break-all leading-6 mt-2">线程 {item.thread_id}<br/>流程 {item.procedure_id}<br/>引擎 {item.engine_status}</p></details>
    </>:<p className="text-xs text-muted-foreground">本轮尚无平台来源准备线程。默认CLI可在本机直接执行相同业务节点。</p>}
    <Button size="sm" variant="outline" disabled={!canRun||status!=='running'||launch.isPending||!!item&&!retry||!!sourceId&&!retry} onClick={()=>setConfirm(true)}>{retry?'重新授权并接续':'平台编排准备来源'}</Button>
    <p className="text-xs text-muted-foreground">无需业务Web的本机入口：<code>uv run anp deep_dub runs prepare-source {runId} --request-key 我的稳定请求键</code></p>
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>{retry?'重新授权并接续来源准备':'平台编排准备来源'}</DialogTitle><DialogDescription>使用当前用户的限时授权。已提交阶段按原输入复用，失败和排队时间保留；可从运行记录取消。</DialogDescription></DialogHeader>
      {!retry&&<div className="space-y-2"><Label htmlFor={'source-flow-reference-'+runId}>耶鲁官方课程页（可选）</Label><Input id={'source-flow-reference-'+runId} type="url" value={reference} onChange={event=>setReference(event.target.value)} placeholder="https://oyc.yale.edu/history/hist-119/lecture-1"/></div>}
      <p className="text-xs text-muted-foreground">需要平台Agent、业务Worker和已配置的来源/模型执行器。到期须由原用户重新授权；不把用户登录令牌写入图状态。</p>
      <DialogFooter><Button variant="outline" onClick={()=>setConfirm(false)}>返回</Button><Button disabled={launch.isPending} onClick={()=>launch.mutate()}>确认{retry?'接续':'启动'}</Button></DialogFooter>
    </DialogContent></Dialog>
  </section>
}
