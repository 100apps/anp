import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Badge } from '@console/components/ui/badge'
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '@console/components/ui/select'
import { MediaPlayer } from './media-player'

const labels:Record<string,string>={all:'全部问题',numbers:'数字差异',reference_difference:'参考差异',invalid_word_time:'词时间异常',word_coverage:'严格覆盖差异',chunk_boundary:'音轨块接缝',empty_asr:'空白识别',cross_model_difference:'跨模型差异'}
type Issue={primary_text?:string;secondary_text?:string;primary_context?:string;secondary_context?:string;id:string;kind:string;reason:string;contains_number?:boolean;reference_text?:string;asr_text?:string;reference_context?:string;asr_context?:string;
  preceding_text?:string;following_text?:string;raw_outliers?:unknown[];supplied_normalized?:string;aligned_normalized?:string;
  audition:{start_ms:number;end_ms:number;precision:string}}
type Queue={packet_sha256:string;binding:{pcm_artifact_id:string};items:Issue[];total:number;matched:number;has_more:boolean;counts:Record<string,number>;numeric_differences:number}

export function SourceReview({projectId,sourceId,identity}:{projectId:string;sourceId:string;identity:string}){
  const [secondary,setSecondary]=useState(false)
  const [open,setOpen]=useState(false),[kind,setKind]=useState('all'),[offset,setOffset]=useState(0),[hash,setHash]=useState<string|null>(null),[selected,setSelected]=useState<string|null>(null)
  const query=useQuery({queryKey:['deep-dub','source-review',identity,projectId,sourceId,kind,offset,hash,secondary],enabled:open,
    queryFn:()=>api<Queue>(`/apps/deep_dub/projects/${projectId}/sources/${sourceId}/review-queue?`+new URLSearchParams({kind,offset:String(offset),limit:'10',include_secondary:String(secondary),...(hash?{expected_hash:hash}:{})})),staleTime:30000})
  const data=query.data,item=data?.items.find(i=>i.id===selected)||data?.items[0]
  function page(next:number){if(data)setHash(data.packet_sha256);setSelected(null);setOffset(next)}
  return <section className="rounded-lg border p-4 space-y-3" aria-label="源稿复核队列">
    <div className="flex flex-wrap gap-2 justify-between items-center"><h4 className="text-sm font-medium">源稿复核队列</h4><Button variant="outline" size="sm" onClick={()=>setOpen(v=>!v)} aria-expanded={open}>{open?'收起复核队列':'打开复核队列'}</Button></div>
    <p className="text-xs text-muted-foreground leading-6">并排核对参考与识别，直接定位本来源私有 PCM 原音。词时间候选不完整时退回音轨块定位；这不是内容、角色或字幕审核通过。</p>
    {open&&<>
      <div className="flex flex-wrap items-center gap-3"><Select value={kind} onValueChange={value=>{setKind(value);setOffset(0);setSelected(null)}}><SelectTrigger className="w-48" aria-label="源稿问题分类"><SelectValue/></SelectTrigger><SelectContent>{Object.entries(labels).filter(([key])=>secondary||key!=='cross_model_difference').map(([key,label])=><SelectItem key={key} value={key}>{label}</SelectItem>)}</SelectContent></Select>
        <Button variant={secondary?'secondary':'outline'} size="sm" aria-pressed={secondary} onClick={()=>{setSecondary(v=>!v);setHash(null);setOffset(0);setSelected(null);setKind('all')}}>{secondary?'已加入第二套识别':'加入第二套识别差异'}</Button>
        {data&&<span className="text-xs text-muted-foreground">{data.total} 条定位项 · {data.numeric_differences} 处数字差异 · 当前 {data.matched} 项</span>}</div>
      {query.isFetching&&<p role="status" className="text-xs text-muted-foreground">正在校验来源证据并定位…</p>}
      {query.error&&<div role="alert" className="text-xs text-destructive">{query.error.message}<Button variant="ghost" size="sm" onClick={()=>{setHash(null);setOffset(0);setSelected(null);query.refetch()}}>重新读取最新证据</Button></div>}
      {data&&!query.error&&<>
        <p className="text-xs text-muted-foreground">参考差异 {data.counts.reference_difference||0} · 词时间异常 {data.counts.invalid_word_time||0} · 严格覆盖差异 {data.counts.word_coverage||0} · 接缝 {data.counts.chunk_boundary||0} · 跨模型 {data.counts.cross_model_difference||0}。分类可重叠，不作为通过率。</p>
        <div className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)]">
          <nav className="space-y-1" aria-label="源稿复核问题列表">{data.items.map((issue,index)=><button type="button" key={issue.id} aria-pressed={issue.id===item?.id} onClick={()=>setSelected(issue.id)} className={'w-full text-left rounded-md border p-3 text-xs space-y-1 '+(issue.id===item?.id?'border-primary bg-accent':'hover:bg-muted/50')}>
            <span className="block font-medium">{offset+index+1}. {labels[issue.kind]}{issue.contains_number?' · 数字':''}</span><span className="block text-muted-foreground">{(issue.audition.start_ms/1000).toFixed(1)}–{(issue.audition.end_ms/1000).toFixed(1)} 秒</span><span className="block truncate">{issue.primary_text||issue.secondary_text||issue.reference_text||issue.asr_text||issue.reason}</span></button>)}</nav>
          {item?<div className="min-w-0 space-y-3 rounded-lg bg-muted/30 p-4" aria-label="当前源稿复核问题"><div className="flex flex-wrap gap-2"><Badge variant="outline">待独立复核</Badge><Badge variant="outline">{item.audition.precision==='word-candidate'?'词时间候选定位':item.audition.precision==='chunk-boundary'?'接缝定位':'音轨块粗定位'}</Badge></div><p className="text-xs leading-6">{item.reason}</p>
            {item.kind==='reference_difference'&&<div className="grid gap-3 md:grid-cols-2"><div className="rounded-md border p-3"><p className="text-xs text-muted-foreground mb-2">官方参考 · 未判定正确</p><p className="text-sm whitespace-pre-wrap">{item.reference_text||'（无对应词）'}</p></div><div className="rounded-md border p-3"><p className="text-xs text-muted-foreground mb-2">ASR 候选 · 未判定正确</p><p className="text-sm whitespace-pre-wrap">{item.asr_text||'（无对应词）'}</p></div></div>}
            {item.kind==='cross_model_difference'&&<div className="grid gap-3 md:grid-cols-2"><div className="rounded-md border p-3"><p className="text-xs text-muted-foreground mb-2">第一套 Qwen · 未判定正确</p><p className="text-sm whitespace-pre-wrap">{item.primary_text||'（无对应词）'}</p></div><div className="rounded-md border p-3"><p className="text-xs text-muted-foreground mb-2">第二套 Nemotron · 未判定正确</p><p className="text-sm whitespace-pre-wrap">{item.secondary_text||'（无对应词）'}</p></div></div>}
            {item.preceding_text&&<div className="space-y-3 text-xs leading-6"><p><strong>接缝之前：</strong>{item.preceding_text}</p><p><strong>接缝之后：</strong>{item.following_text}</p></div>}
            <MediaPlayer key={item.id} artifactId={data.binding.pcm_artifact_id} label="查听本来源原音" window={item.audition}/>
            <details><summary className="cursor-pointer text-xs">展开上下文与原始异常</summary><div className="text-xs leading-6 whitespace-pre-wrap space-y-3 pt-3">{item.primary_context&&<p><strong>第一套上下文：</strong>{item.primary_context}</p>}{item.secondary_context&&<p><strong>第二套上下文：</strong>{item.secondary_context}</p>}{item.reference_context&&<p><strong>参考上下文：</strong>{item.reference_context}</p>}{item.asr_context&&<p><strong>ASR 上下文：</strong>{item.asr_context}</p>}{item.raw_outliers&&<pre className="overflow-auto">{JSON.stringify(item.raw_outliers,null,2)}</pre>}{item.supplied_normalized&&<><p>严格原文：{item.supplied_normalized}</p><p>严格对齐：{item.aligned_normalized}</p></>}</div></details>
            <p className="text-[11px] font-mono text-muted-foreground break-all">输入证据摘要 {data.packet_sha256} · 项目 {item.id}</p>
          </div>:<p className="text-xs text-muted-foreground">此分类没有定位项；不表示整体内容已通过。</p>}
        </div>
        <div className="flex items-center justify-between"><Button variant="outline" size="sm" disabled={!offset||query.isFetching} onClick={()=>page(Math.max(0,offset-10))}>上一页问题</Button><span className="text-xs text-muted-foreground">{data.matched?offset+1:0}–{Math.min(offset+10,data.matched)} / {data.matched}</span><Button variant="outline" size="sm" disabled={!data.has_more||query.isFetching} onClick={()=>page(offset+10)}>下一页问题</Button></div>
      </>}
    </>}
  </section>
}
