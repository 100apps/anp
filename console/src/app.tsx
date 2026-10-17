import { BrowserRouter,Routes,Route,Navigate,useParams } from 'react-router-dom'
import { useAuth } from '@/auth/auth-provider'
import { Loader2 } from 'lucide-react'
import Login from '@/pages/login'
import Overview from '@/pages/overview'
import Connections from '@/pages/connections'
import Members from '@/pages/members'
import Profile from '@/pages/profile'
import AdminLayout from '@/layouts/admin-layout'
import { applications } from '@/generated/applications'

function Application(){
  const {appId}=useParams()
  const {me}=useAuth()
  const app=applications.find(a=>a.id===appId)
  if(!app)return <p>应用不存在。</p>
  if(!me?.permissions.includes(app.id+'.task.read'))return <p role="alert">没有访问该应用的权限。</p>
  const Component=app.component
  return <Component/>
}
export default function App(){
  const {session,loading,error,logout}=useAuth()
  if(loading)return <div className="h-screen flex items-center justify-center gap-3 text-muted-foreground"><Loader2 className="animate-spin" size={20}/>正在连接工作空间…</div>
  if(error)return <div className="h-screen flex flex-col items-center justify-center gap-4"><h1 className="font-semibold text-xl">暂时无法进入工作台</h1><p role="alert" className="text-muted-foreground">{error}</p><button className="text-primary underline" onClick={logout}>退出并重新登录</button></div>
  if(!session)return <Login/>
  return <BrowserRouter><Routes><Route element={<AdminLayout/>}><Route index element={<Overview/>}/><Route path="/apps/:appId" element={<Application/>}/><Route path="/connections" element={<Connections/>}/><Route path="/members" element={<Members/>}/><Route path="/profile" element={<Profile/>}/><Route path="*" element={<Navigate to="/" replace/>}/></Route></Routes></BrowserRouter>
}
