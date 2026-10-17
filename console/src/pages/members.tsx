import { useMutation,useQuery,useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Card,CardContent } from '@/components/ui/card'
import { Table,TableHeader,TableHead,TableBody,TableRow,TableCell } from '@/components/ui/table'
import { useAuth } from '@/auth/auth-provider'

type Member={user_id:string;email:string;role:string}
export default function Members(){
  const {me}=useAuth();const queryClient=useQueryClient()
  const query=useQuery({queryKey:['members',me?.workspace_id],queryFn:()=>api<Member[]>('/members')})
  const update=useMutation({mutationFn:({user_id,role}:Member)=>api('/members/'+user_id,{method:'PATCH',body:JSON.stringify({role})}),onSuccess:()=>{queryClient.invalidateQueries({queryKey:['members']});toast.success('角色已更新')},onError:e=>toast.error(e.message)})
  return <div className="space-y-7"><div><div className="text-[11px] text-muted-foreground tracking-[.16em] mb-2">WORKSPACE ACCESS</div><h1 className="text-[28px] font-semibold">成员与权限</h1><p className="mt-2 text-muted-foreground">角色控制操作权限，数据访问同时受 Supabase RLS 保护。</p></div><Card className="shadow-none"><CardContent className="pt-6">{query.error?<p role="alert" className="text-destructive">{query.error.message}</p>:<Table><TableHeader><TableRow><TableHead>成员邮箱</TableHead><TableHead>角色</TableHead><TableHead>成员 ID</TableHead></TableRow></TableHeader><TableBody>{query.data?.map(m=><TableRow key={m.user_id}><TableCell className="font-medium">{m.email}{m.user_id===me?.id&&<span className="ml-2 text-xs text-muted-foreground">你</span>}</TableCell><TableCell><select aria-label={m.email+' 的角色'} value={m.role} disabled={m.user_id===me?.id||update.isPending} onChange={e=>update.mutate({...m,role:e.target.value})} className="border rounded-md bg-card p-2 text-xs"><option value="admin">管理员</option><option value="developer">开发者</option><option value="viewer">查看者</option></select></TableCell><TableCell className="mono text-xs text-muted-foreground">{m.user_id.slice(0,8)}</TableCell></TableRow>)}</TableBody></Table>}</CardContent></Card><div className="grid md:grid-cols-3 gap-4">{[['管理员','管理工作空间成员，并执行应用任务。'],['开发者','创建、执行、取消任务，并查看授权结果。'],['查看者','只读访问工作空间内已授权的任务和结果。']].map(([name,desc])=><Card key={name} className="shadow-none"><CardContent className="pt-5"><h3 className="font-medium">{name}</h3><p className="text-xs text-muted-foreground mt-2 leading-6">{desc}</p></CardContent></Card>)}</div><p className="text-xs text-muted-foreground">新增账号可由平台管理员使用 CLI：<code className="mono">uv run anp users create --email 用户邮箱</code>。</p></div>
}
