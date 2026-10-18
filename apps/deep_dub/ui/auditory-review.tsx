import { useMemo,useRef,useState } from 'react'
import { useMutation,useQuery,useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Badge } from '@console/components/ui/badge'
import { Select,SelectContent,SelectItem,SelectTrigger,SelectValue } from '@console/components/ui/select'
import { Dialog,DialogContent,DialogDescription,DialogFooter,DialogHeader,DialogTitle } from '@console/components/ui/dialog'
import { MediaPlayer } from './media-player'

const labels:Record<string,string>={all:'全部窗口',matched:'模型未报差异',mismatch:'模型报差异',uncertain:'模型不确定',ungrounded:'引文待核实'}
type Verdict={status:string;reason:string;issues:{kind:string;candidate_excerpt:string;heard_excerpt:string;reason:string}[]}
type Result={evidence_artifact_id:string;consistency:{identical_excerpt_issues:number;unsubstantiated_window_count:number;unsubstantiated_window_indexes:number[];issue_locations:{window_index:number;issue_index:number}[]};proof:{input:{pcm_artifact_id:string};control_artifact_ids:Record<string,string>;
  measurement:{counts:Record<string,number>;model:{model_id:string};session:{requests:number;operation_stopped:boolean};calibration_passed:boolean;
    calibration_summary:{decision_correct:number;decision_total:number;critical_excerpt_correct:number;critical_excerpt_total:number;taxonomy_correct:number;taxonomy_total:number};
    controls:{control_index:number;spoken_text:string;candidate:string;result:{verdict:Verdict}}[];
    windows:{index:number;start_sample:number;end_sample:number;candidate_text:string;result:{verdict:Verdict}}[]}}}

export function AuditoryReview({projectId,sourceId,runId,status,canRun,identity}:{projectId:string;sourceId:string;runId:string;status:string;canRun:boolean;identity:string}){
  const [open,setOpen]=useState(false),[confirm,setConfirm]=useState(false),[filter,setFilter]=useState('all'),[offset,setOffset]=useState(0),[selected,setSelected]=useState<number|null>(null),[control,setControl]=useState<number|null>(null)
  const key=useRef(crypto.randomUUID()),cache=useQueryClient()
  const query=useQuery({queryKey:['deep-dub','auditory-review',identity,projectId,sourceId],queryFn:()=>api<Result|null>(`/apps/deep_dub/projects/${projectId}/sources/${sourceId}/auditory-review`),staleTime:30000})
  const generate=useMutation({mutationFn:()=>api<{status:string}>(`/apps/deep_dub/runs/${runId}/review-source-audio`,{method:'POST',headers:{'Idempotency-Key':key.current},body:JSON.stringify({source_id:sourceId,timeout_seconds:3600})}),
    onSuccess:async result=>{await cache.invalidateQueries({queryKey:['deep-dub']});setConfirm(false);toast.success('听音复核阶段：'+result.status)},onError:error=>toast.error(error.message)})
  const measured=query.data?.proof.measurement
  const grounding=query.data?.consistency
  const rows=useMemo(()=>measured?.windows.filter(w=>filter==='all'||w.result.verdict.status===filter||(filter==='ungrounded'&&grounding?.unsubstantiated_window_indexes.includes(w.index)))||[],[measured,filter,grounding])
  const page=rows.slice(offset,offset+10),current=page.find(w=>w.index===selected)||page[0],test=measured?.controls.find(c=>c.control_index===control)
  return <section className="space-y-3 rounded-lg border p-4" aria-label="独立听音复核">
    <div className="flex flex-wrap justify-between gap-2"><h4 className="text-sm font-medium">独立听音复核</h4><Badge variant={measured?.counts.mismatch?'destructive':'outline'}>正式转录仍待审</Badge></div>
    <p className="text-xs text-muted-foreground leading-6">直接听原音，核对原 ASR 候选。先检验同一文本在不同数字/否定/专名原音下能否得到相反判定。模型意见保留原值，不自动改稿、登记分段或签发通过。</p>
    {query.error&&<p role="alert" className="text-xs text-destructive">{query.error.message}</p>}
    {measured&&<><p className="text-sm">{measured.windows.length} 个来源窗口 · 报差异 {measured.counts.mismatch} · 不确定 {measured.counts.uncertain} · 未报差异 {measured.counts.matched}</p>
      <p className="text-xs text-muted-foreground">反事实校准 {measured.calibration_passed?`${measured.controls.length}/${measured.controls.length} 完成`:'未满足'} · {measured.session.requests} 次请求 · 模型{measured.session.operation_stopped?'已退出':'未确认退出'}。校准不等于整片正确率。</p>
      <p className="text-xs text-muted-foreground">合成校准：对照判定 {measured.calibration_summary.decision_correct}/{measured.calibration_summary.decision_total} · 关键内容 {measured.calibration_summary.critical_excerpt_correct}/{measured.calibration_summary.critical_excerpt_total} · 标签细分 {measured.calibration_summary.taxonomy_correct}/{measured.calibration_summary.taxonomy_total}（保留模型原标签，不自动修正）。</p>
      {!!grounding?.identical_excerpt_issues&&<p role="alert" className="rounded border border-amber-500/40 bg-amber-500/5 p-3 text-xs leading-6">{grounding.unsubstantiated_window_count} 个窗口、{grounding.identical_excerpt_issues} 条意见的前后引文一致，不能单凭它们裁决正文差异。原模型意见保留，当前来源质量仍未测；可筛选「引文待核实」后查听。</p>}
      <Button variant="outline" size="sm" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>{open?'收起听音意见':'查听独立复核意见'}</Button>
      {open&&<div className="space-y-3"><Select value={filter} onValueChange={value=>{setFilter(value);setOffset(0);setSelected(null);setControl(null)}}><SelectTrigger className="w-48" aria-label="听音判定筛选"><SelectValue/></SelectTrigger><SelectContent>{Object.entries(labels).map(([id,label])=><SelectItem key={id} value={id}>{label}</SelectItem>)}</SelectContent></Select>
        <div className="grid gap-4 xl:grid-cols-[220px_minmax(0,1fr)]"><nav className="space-y-1" aria-label="独立听音窗口列表">{page.map(w=><button type="button" key={w.index} aria-pressed={w.index===current?.index} onClick={()=>{setSelected(w.index);setControl(null)}} className={'w-full rounded border p-3 text-left text-xs '+(w.index===current?.index?'border-primary bg-accent':'hover:bg-muted/50')}><span className="block">窗口 {w.index+1} · {labels[w.result.verdict.status]}</span><span className="block text-muted-foreground">{(w.start_sample/16000).toFixed(3)}–{(w.end_sample/16000).toFixed(3)} 秒</span></button>)}</nav>
          <div className="min-w-0 space-y-3">{current?<><p className="text-xs leading-6">{current.result.verdict.reason}</p>{current.result.verdict.issues.map((issue,i)=><div key={i} className="rounded border p-3 text-xs leading-6"><p className="font-medium">{issue.kind} · {issue.reason}</p><p>原候选：{issue.candidate_excerpt||'（无对应文字）'}</p><p>审核器听到：{issue.heard_excerpt||'（无对应文字）'}</p></div>)}<details><summary className="cursor-pointer text-xs">查看本窗口原 ASR 候选</summary><p className="text-xs leading-6 whitespace-pre-wrap pt-2">{current.candidate_text}</p></details></>:<p className="text-xs">此分类没有窗口，不表示正文已通过。</p>}
            {query.data&&(test?<><p className="text-xs">校准原音：{test.spoken_text}<br/>提交候选：{test.candidate} · {labels[test.result.verdict.status]}</p><MediaPlayer key={'control-'+control} artifactId={query.data.proof.control_artifact_ids[String(control)]} label="查听校准原音"/><Button variant="ghost" size="sm" onClick={()=>setControl(null)}>返回来源原音</Button></>:current?<MediaPlayer key={'window-'+current.index} artifactId={query.data.proof.input.pcm_artifact_id} label="查听独立复核原音" window={{start_ms:current.start_sample/16,end_ms:current.end_sample/16}}/>:null)}
          </div></div>
        <div className="flex justify-between items-center"><Button variant="outline" size="sm" disabled={!offset} onClick={()=>{setOffset(v=>Math.max(0,v-10));setSelected(null);setControl(null)}}>上一页复核</Button><span className="text-xs">{rows.length?offset+1:0}–{Math.min(offset+10,rows.length)} / {rows.length}</span><Button variant="outline" size="sm" disabled={offset+10>=rows.length} onClick={()=>{setOffset(v=>v+10);setSelected(null);setControl(null)}}>下一页复核</Button></div>
        <details><summary className="cursor-pointer text-xs">查看全部校准原音与结果</summary><div className="flex flex-wrap gap-2 pt-2">{measured.controls.map(c=><Button key={c.control_index} variant="outline" size="sm" onClick={()=>setControl(c.control_index)}>校准 {c.control_index+1} · {labels[c.result.verdict.status]}</Button>)}</div></details>
      </div>}
      <p className="text-[11px] font-mono text-muted-foreground break-all">{measured.model.model_id} · 证据 {query.data?.evidence_artifact_id}</p></>}
    <Button variant="outline" size="sm" disabled={!canRun||!['running','waiting_review'].includes(status)||generate.isPending} onClick={()=>setConfirm(true)}>{generate.isPending?'完整听音复核中…':'独立复核完整来源原音'}</Button>
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>独立复核完整来源原音</DialogTitle><DialogDescription>将加载本机配置的听音模型，先做数字、否定与专名反事实校准，再逐窗复核完整 PCM 与原候选。报差异会留下失败审核证据；不会自动替换原稿或通过正式转录。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={()=>setConfirm(false)}>返回</Button><Button disabled={generate.isPending} onClick={()=>generate.mutate()}>开始完整听音复核</Button></DialogFooter></DialogContent></Dialog>
  </section>
}
