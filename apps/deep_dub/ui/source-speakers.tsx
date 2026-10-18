import { useMemo,useRef,useState } from 'react'
import { useMutation,useQuery,useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Badge } from '@console/components/ui/badge'
import { Select,SelectContent,SelectItem,SelectTrigger,SelectValue } from '@console/components/ui/select'
import { Dialog,DialogContent,DialogDescription,DialogFooter,DialogHeader,DialogTitle } from '@console/components/ui/dialog'
import { MediaPlayer } from './media-player'

type Turn={start_sample:number;end_sample:number;speaker_id:string;confidence:number}
type Result={proof:{input:{pcm_artifact_id:string};measurement:{pcm:{sample_count:number;sample_rate:number};turns:Turn[];
  session:{operation_stopped:boolean};summary:{turn_count:number;speaker_count:number;speakers:string[];active_fraction:number;overlap_fraction:number;invalid_turn_indexes:number[]}}}}

export function SourceSpeakers({projectId,sourceId,runId,status,canRun,identity}:{projectId:string;sourceId:string;runId:string;status:string;canRun:boolean;identity:string}){
  const [open,setOpen]=useState(false),[confirm,setConfirm]=useState(false),[speaker,setSpeaker]=useState('all'),[offset,setOffset]=useState(0),[selected,setSelected]=useState<number|null>(null)
  const key=useRef(crypto.randomUUID()),cache=useQueryClient()
  const query=useQuery({queryKey:['deep-dub','source-speakers',identity,projectId,sourceId],
    queryFn:()=>api<Result|null>(`/apps/deep_dub/projects/${projectId}/sources/${sourceId}/speaker-timing`),staleTime:30000})
  const generate=useMutation({mutationFn:()=>api<{status:string}>(`/apps/deep_dub/runs/${runId}/analyze-source-speakers`,{
    method:'POST',headers:{'Idempotency-Key':key.current},body:JSON.stringify({source_id:sourceId,timeout_seconds:3600})}),
    onSuccess:async result=>{await cache.invalidateQueries({queryKey:['deep-dub']});setConfirm(false);toast.success('角色分析阶段：'+result.status)},onError:error=>toast.error(error.message)})
  const measured=query.data?.proof.measurement,rate=measured?.pcm.sample_rate||16000
  const rows=useMemo(()=>measured?.turns.map((turn,index)=>({...turn,index})).filter(turn=>speaker==='all'||turn.speaker_id===speaker)||[],[measured,speaker])
  const page=rows.slice(offset,offset+10),current=page.find(row=>row.index===selected)||page[0]
  const valid=!!current&&current.start_sample>=0&&current.end_sample>current.start_sample&&current.end_sample<=(measured?.pcm.sample_count||0)
  return <section className="space-y-3 rounded-lg border p-4" aria-label="来源角色时间">
    <div className="flex flex-wrap justify-between gap-2"><h4 className="text-sm font-medium">来源角色时间</h4><Badge variant="outline">声学角色候选 · 身份未审</Badge></div>
    <p className="text-xs text-muted-foreground leading-6">同一次模型会话分析完整原始 PCM，保留重叠与异常边界。speaker 标签只在本次证据内有效，不自动命名人物、登记角色或生成字幕。</p>
    {query.error&&<p role="alert" className="text-xs text-destructive">{query.error.message}</p>}
    {measured&&<><p className="text-sm">{measured.summary.speaker_count} 个角色标签 · {measured.summary.turn_count} 个区间 · 原始异常 {measured.summary.invalid_turn_indexes.length}</p>
      <p className="text-xs text-muted-foreground">有效活动并集 {(measured.summary.active_fraction*100).toFixed(2)}% · 多角色重叠 {(measured.summary.overlap_fraction*100).toFixed(2)}% · 模型{measured.session.operation_stopped?'已退出':'未确认退出'}。这些比例不是准确率。</p>
      <Button variant="outline" size="sm" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>{open?'收起角色区间':'查听角色时间候选'}</Button>
      {open&&<div className="space-y-3"><Select value={speaker} onValueChange={value=>{setSpeaker(value);setOffset(0);setSelected(null)}}><SelectTrigger className="w-48" aria-label="角色标签筛选"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">全部角色标签</SelectItem>{measured.summary.speakers.map(id=><SelectItem key={id} value={id}>{id}</SelectItem>)}</SelectContent></Select>
        <div className="grid gap-4 xl:grid-cols-[220px_minmax(0,1fr)]"><nav aria-label="角色时间候选列表" className="space-y-1">{page.map(turn=><button key={turn.index} type="button" aria-pressed={turn.index===current?.index} onClick={()=>setSelected(turn.index)} className={'w-full rounded border p-3 text-left text-xs '+(turn.index===current?.index?'border-primary bg-accent':'hover:bg-muted/50')}><span className="block">区间 {turn.index+1} · {turn.speaker_id}</span><span className="block text-muted-foreground">{(turn.start_sample/rate).toFixed(3)}–{(turn.end_sample/rate).toFixed(3)} 秒</span></button>)}</nav>
          {current?<div className="space-y-3 min-w-0"><p className="text-xs leading-6">原始样本 {current.start_sample}–{current.end_sample} · {current.speaker_id} · 模型原始置信值 {current.confidence}（未校准，不证明身份或边界正确）</p>
            {valid&&query.data?<MediaPlayer key={current.index} artifactId={query.data.proof.input.pcm_artifact_id} label="查听角色候选原音" window={{start_ms:current.start_sample*1000/rate,end_ms:current.end_sample*1000/rate}}/>:<p role="alert" className="text-xs text-destructive">原始角色时间异常，保留原值，不裁剪后播放或视为通过。</p>}</div>:<p className="text-xs text-muted-foreground">没有角色时间候选；不代表没有声音或已经审核。</p>}</div>
        <div className="flex justify-between items-center"><Button variant="outline" size="sm" disabled={!offset} onClick={()=>{setOffset(v=>Math.max(0,v-10));setSelected(null)}}>上一页区间</Button><span className="text-xs">{rows.length?offset+1:0}–{Math.min(offset+10,rows.length)} / {rows.length}</span><Button variant="outline" size="sm" disabled={offset+10>=rows.length} onClick={()=>{setOffset(v=>v+10);setSelected(null)}}>下一页区间</Button></div>
      </div>}</>}
    <Button variant="outline" size="sm" disabled={!canRun||!['running','waiting_review'].includes(status)||generate.isPending} onClick={()=>setConfirm(true)}>{generate.isPending?'角色模型计算中…':'分析完整来源角色'}</Button>
    <p className="text-xs text-muted-foreground">同输入复用证据；运行取消或撤权会停止本次租约内模型，不操作其他作业。原始词时间、待审状态与排队起点保持。</p>
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>分析完整来源角色</DialogTitle><DialogDescription>将占用本机受控 GPU，使用 Nemotron 分析完整 PCM 的角色时间区间。模型标签不是人物身份，本操作不登记正式说话人或通过任何质量门禁。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={()=>setConfirm(false)}>返回</Button><Button disabled={generate.isPending} onClick={()=>generate.mutate()}>开始角色分析</Button></DialogFooter></DialogContent></Dialog>
  </section>
}
