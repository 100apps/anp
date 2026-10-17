import { useState,type FormEvent } from 'react'
import { useAuth } from '@/auth/auth-provider'
import { Card,CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { toast } from 'sonner'

export default function Profile(){
  const {me,client}=useAuth();const [password,setPassword]=useState('');const [busy,setBusy]=useState(false)
  async function submit(e:FormEvent){e.preventDefault();setBusy(true);const {error}=await client!.auth.updateUser({password});setBusy(false);if(error)toast.error(error.message);else{toast.success('密码已更新');setPassword('')}}
  return <div className="max-w-2xl space-y-6"><h1 className="text-[28px] font-semibold">个人设置</h1><Card className="shadow-none"><CardContent className="pt-6 space-y-3"><h2 className="font-semibold">账号信息</h2><p className="text-sm">{me?.email}</p><p className="mono text-xs text-muted-foreground">{me?.id}</p></CardContent></Card><Card className="shadow-none"><CardContent className="pt-6"><form onSubmit={submit} className="space-y-4"><h2 className="font-semibold">修改密码</h2><div className="space-y-2"><Label htmlFor="new-password">新密码</Label><Input id="new-password" type="password" autoComplete="new-password" minLength={12} value={password} onChange={e=>setPassword(e.target.value)} required/><p className="text-xs text-muted-foreground">至少 12 个字符，由 Supabase Auth 更新。</p></div><Button disabled={busy}>更新密码</Button></form></CardContent></Card></div>
}
