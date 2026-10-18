import {useMemo} from 'react'
import {useQuery} from '@tanstack/react-query'
import {useAuth} from '@console/auth/auth-provider'
import {api} from '@console/lib/api'
import {Button} from '@console/components/ui/button'
import {TaskStudio} from './task-studio'
import {platformAdapter} from './platform-adapter'
import './studio.css'

export function PlatformStudio({projectId,backHref}:{projectId:string;backHref:string}) {
  const {me}=useAuth(),identity=me?.id+':'+me?.workspace_id,permissionKey=me?.permissions.join(',')||''
  const adapter=useMemo(()=>platformAdapter(projectId,identity,permissionKey.split(',')),[projectId,identity,permissionKey])
  const media=useQuery({queryKey:['deep-dub','media-session',me?.id,me?.workspace_id],
    queryFn:()=>api<{expires_at:number}>('/apps/deep_dub/media/session',{method:'POST'}),enabled:!!me,
    staleTime:60000,refetchInterval:90000,refetchIntervalInBackground:true,retry:1})
  if(!me)return null
  return <div className="dd-platform-overlay">
    {!media.data?<div className="dd-studio dd-loading"><p role={media.error?'alert':undefined}>{media.error?.message||'正在建立当前用户的私有媒体会话…'}</p>{media.error&&<Button onClick={()=>media.refetch()}>重新连接</Button>}</div>:<>
      {media.error&&<div className="dd-session-error" role="alert">媒体会话续期失败，下一次播放可能需要重新连接。<Button variant="ghost" size="sm" onClick={()=>media.refetch()}>重新连接</Button></div>}
      <TaskStudio key={identity+':'+projectId} adapter={adapter} taskId={projectId} backHref={backHref}/>
    </>}
  </div>
}
