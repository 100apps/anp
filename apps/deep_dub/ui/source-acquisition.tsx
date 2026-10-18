import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@console/lib/api'
import { Button } from '@console/components/ui/button'
import { Badge } from '@console/components/ui/badge'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@console/components/ui/dialog'

type Acquisition={source_id:string;evidence_artifact_id:string;proof:{video_artifact_id:string;download:{
  online:{title:string;video_id:string;selected_formats:{format_id:string;width?:number;height?:number;fps?:number}[]};
  media:{duration_ms:number};file:{bytes:number;sha256:string};new_download:boolean;identity_scope:string;
  process:{operation_stopped:boolean;job:{total_processes:number;active_processes:number}}}}}

export function SourceAcquisition({runId,status,sourceId,canRun,identity}:{runId:string;status:string;sourceId:string|null;canRun:boolean;identity:string}){
  const [confirm,setConfirm]=useState(false),key=useRef(crypto.randomUUID()),query=useQueryClient()
  const result=useQuery({queryKey:['deep-dub','source-acquisition',identity,runId],
    queryFn:()=>api<Acquisition|null>(`/apps/deep_dub/runs/${runId}/source-acquisition`),staleTime:30000})
  const execute=useMutation({mutationFn:()=>api<{status:string}>(`/apps/deep_dub/runs/${runId}/acquire-source`,{
    method:'POST',headers:{'Idempotency-Key':key.current},body:JSON.stringify({timeout_seconds:3600})}),
    onSuccess:async response=>{await query.invalidateQueries({queryKey:['deep-dub']});setConfirm(false);toast.success(response.status==='succeeded'?'新原片与来源证据已保存':'阶段状态：'+response.status)},
    onError:error=>toast.error(error.message)})
  const download=result.data?.proof.download
  return <section className="mt-5 border-t pt-5 space-y-3" aria-label="来源获取">
    <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-medium">来源获取</h3><Badge variant="outline">原片 · 不可变版本</Badge></div>
    <p className="text-xs text-muted-foreground leading-6">使用运行排队时冻结的 YouTube 单视频地址，下载当前最高可用画质与音轨。原片和证据保存到私有对象存储，不复用历史成片。</p>
    {result.error?<p role="alert" className="text-sm text-destructive">证据读取失败：{result.error.message}<Button size="sm" variant="ghost" onClick={()=>result.refetch()}>重试读取</Button></p>
      :result.isLoading?<p className="text-xs text-muted-foreground">正在读取来源获取记录…</p>
      :download?<><p className="text-sm">{download.online.title}</p>
        <p className="text-xs text-muted-foreground">{(download.media.duration_ms/1000).toFixed(3)} 秒 · {(download.file.bytes/1024**2).toFixed(1)} MiB · 格式 {download.online.selected_formats.map(f=>f.format_id).join(' + ')}</p>
        <p className="text-xs text-muted-foreground leading-6">{download.identity_scope}。内容、声音与字幕仍需独立验收。</p>
        <details><summary className="text-xs cursor-pointer">来源与停止回执</summary><div className="text-xs font-mono leading-6 break-all mt-2">视频 {download.online.video_id}<br/>SHA256 {download.file.sha256}<br/>证据 {result.data?.evidence_artifact_id}<br/>进程树累计 {download.process.job.total_processes} 个进程，剩余 {download.process.job.active_processes} 个</div></details></>
      :<p className="text-xs text-muted-foreground">{sourceId?'本轮来源来自其他登记流程；不会替换它。':'尚未获取来源。失败、取消和等待身份验证均保留阶段记录。'}</p>}
    <Button size="sm" variant="outline" disabled={!canRun||status!=='running'||!!sourceId||execute.isPending} onClick={()=>setConfirm(true)}>获取新原片</Button>
    <Dialog open={confirm} onOpenChange={setConfirm}><DialogContent><DialogHeader><DialogTitle>获取新原片</DialogTitle><DialogDescription>下载完整单视频，可能需要数分钟并占用网络和本机媒体CPU租约。不会自动改变质量档位、切换下载账号或绕过站点验证。</DialogDescription></DialogHeader>
      <p className="text-xs text-muted-foreground leading-6">可返回运行记录取消整个运行；只有本次下载器及其 Node/FFmpeg 子孙全部退出后才释放租约。开始后完整墙钟继续累计。</p>
      <DialogFooter><Button variant="outline" onClick={()=>setConfirm(false)}>{execute.isPending?'返回运行记录':'返回'}</Button><Button disabled={execute.isPending} onClick={()=>execute.mutate()}>{execute.isPending?'正在获取…':'确认获取'}</Button></DialogFooter>
    </DialogContent></Dialog>
  </section>
}
