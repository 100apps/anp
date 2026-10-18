import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api,registerSessionCleanup } from '@console/lib/api'
import { useAuth } from '@console/auth/auth-provider'
import { Button } from '@console/components/ui/button'

registerSessionCleanup('deep_dub',async()=>{
  const response=await fetch('/api/apps/deep_dub/media/session',{method:'DELETE',credentials:'same-origin',signal:AbortSignal.timeout(5000)})
  if(!response.ok)throw new Error('播放会话清理失败')
})

type MediaInfo={id:string;project_id:string;media_type:string;bytes:number;sha256:string;duration_ms:number|null;sample_rate:number|null}
export function MediaPlayer({artifactId,label='媒体试听'}:{artifactId:string;label?:string}) {
  const {me}=useAuth()
  const identity=[me?.id,me?.workspace_id]
  const audio=useRef<HTMLAudioElement>(null)
  const video=useRef<HTMLVideoElement>(null)
  const session=useQuery({queryKey:['deep-dub','media-session',...identity],
    queryFn:()=>api<{expires_at:number}>('/apps/deep_dub/media/session',{method:'POST'}),
    enabled:!!me,staleTime:60000,refetchInterval:90000,retry:1})
  const info=useQuery({queryKey:['deep-dub','media-info',...identity,artifactId],
    queryFn:()=>api<MediaInfo>('/apps/deep_dub/media/artifacts/'+artifactId+'/info'),enabled:!!me})
  const ready=!!session.data&&!session.error&&!!info.data&&!info.error
  const src=ready?'/api/apps/deep_dub/media/artifacts/'+artifactId:undefined
  useEffect(()=>{if(!ready){audio.current?.pause();video.current?.pause()}},[ready])
  const position=useRef(0)
  async function reconnect(){
    const element=video.current||audio.current
    position.current=element?.currentTime||0
    const renewed=await session.refetch()
    if(renewed.isSuccess)element?.load()
  }
  function restored(){const element=video.current||audio.current;if(element&&position.current)element.currentTime=position.current}
  return <section className="space-y-3 rounded-lg border p-4" data-testid="private-media-player">
    <div className="flex justify-between items-center gap-3"><h3 className="text-sm font-medium">{label}</h3><Button type="button" variant="ghost" size="sm" onClick={reconnect} disabled={session.isFetching}>重新连接</Button></div>
    {session.error||info.error?<p role="alert" className="text-sm text-destructive">{session.error?.message||info.error?.message}</p>:!ready?<p className="text-xs text-muted-foreground">正在校验媒体访问权限…</p>:info.data?.media_type.startsWith('video/')?
      <video ref={video} key={artifactId} controls preload="metadata" src={src} className="w-full max-h-[400px] rounded bg-black" onLoadedMetadata={restored} onError={()=>{position.current=video.current?.currentTime||0}}/>:
      <audio ref={audio} key={artifactId} controls preload="metadata" src={src} className="w-full" onLoadedMetadata={restored} onError={()=>{position.current=audio.current?.currentTime||0}}/>}
    {info.data&&<p className="text-[11px] text-muted-foreground">{info.data.media_type} · {(info.data.bytes/1024/1024).toFixed(2)} MB{info.data.duration_ms?` · ${(info.data.duration_ms/1000).toFixed(2)} 秒`:''}</p>}
  </section>
}
