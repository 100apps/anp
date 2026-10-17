import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { LayoutDashboard, Layers3, FileText, Network, Users, TerminalSquare, LogOut, Sun, Moon, PanelLeftClose, Menu, ChevronRight } from 'lucide-react'
import { useAuth } from '@/auth/auth-provider'
import { applications } from '@/generated/applications'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export default function AdminLayout(){
  const {me,logout,selectWorkspace}=useAuth()
  const [dark,setDark]=useState(localStorage.getItem('anp-theme')==='dark')
  const [open,setOpen]=useState(false)
  const location=useLocation()
  useEffect(()=>{document.documentElement.classList.toggle('dark',dark);localStorage.setItem('anp-theme',String(dark?'dark':'light'))},[dark])
  const links=[{to:'/',label:'工作台概览',icon:LayoutDashboard},...applications.filter(a=>me?.permissions.includes(a.id+'.task.read')).map(a=>({to:'/apps/'+a.id,label:a.name,icon:FileText})),
    ...(me?.is_platform_admin?[{to:'/connections',label:'平台连接',icon:Network}]:[]),
    ...(me?.permissions.includes('workspace.members.manage')?[{to:'/members',label:'成员与权限',icon:Users}]:[])]
  const title=links.find(l=>l.to===location.pathname)?.label||(location.pathname==='/profile'?'个人设置':'应用')
  return <div className="min-h-screen flex">
    <aside className={cn('fixed inset-y-0 left-0 z-40 w-60 bg-[#142f27] text-[#c6d8cc] flex flex-col transition-transform lg:translate-x-0',open?'translate-x-0':'-translate-x-full')}>
      <div className="h-[76px] flex items-center gap-3 px-6 border-b border-white/8"><Layers3 className="text-[#a7d1b2]"/><span className="font-bold text-white text-xl tracking-[.22em]">ANP</span><span className="ml-auto text-[10px] px-2 py-0.5 rounded-full border border-white/15">LOCAL</span></div>
      <div className="p-5"><div className="text-[10px] uppercase tracking-[.18em] text-white/35 mb-2">工作空间</div><select aria-label="工作空间" className="w-full bg-white/5 rounded-lg border border-white/10 px-3 py-2 text-xs text-[#e5eee7]" value={me?.workspace_id} onChange={e=>selectWorkspace(e.target.value)}>{me?.workspaces.map(w=><option className="text-black" key={w.id} value={w.id}>{w.name}</option>)}</select></div>
      <nav className="px-3 space-y-1 flex-1 overflow-auto sidebar-scroll"><div className="px-3 py-3 text-[10px] tracking-[.2em] text-white/30">工作台</div>{links.map(link=><NavLink key={link.to} to={link.to} end={link.to==='/'} onClick={()=>setOpen(false)} className={({isActive})=>cn('flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors',isActive?'bg-[#315344] text-white':'hover:bg-white/5 hover:text-white')}><link.icon size={17}/>{link.label}</NavLink>)}</nav>
      <div className="m-4 rounded-xl border border-white/10 bg-white/3 p-4"><TerminalSquare size={17} className="text-[#a7d1b2] mb-2"/><div className="text-xs text-white/70 mb-2">同一业务，三种入口</div><code className="text-[10px] text-white/40">uv run anp --help</code></div>
      <div className="border-t border-white/10 p-4 flex items-center gap-3"><NavLink to="/profile" className="flex min-w-0 flex-1 items-center gap-3"><div className="h-8 w-8 rounded-full bg-[#42654e] text-white flex items-center justify-center font-semibold">{me?.email[0].toUpperCase()}</div><div className="min-w-0"><div className="text-xs truncate text-white/85">{me?.email}</div><div className="text-[10px] text-white/35">{me?.role==='admin'?'管理员':me?.role==='developer'?'开发者':'查看者'}</div></div></NavLink><button title="退出登录" aria-label="退出登录" onClick={logout} className="text-white/45 hover:text-white"><LogOut size={15}/></button></div>
    </aside>
    {open&&<div className="fixed inset-0 bg-black/40 z-30 lg:hidden" onClick={()=>setOpen(false)}/>}
    <div className="flex-1 min-w-0 lg:ml-60"><header className="h-[76px] px-5 md:px-8 border-b bg-card flex items-center gap-3"><button aria-label="打开菜单" onClick={()=>setOpen(!open)} className="lg:hidden"><Menu size={20}/></button><PanelLeftClose size={16} className="hidden lg:block text-muted-foreground"/><span className="text-muted-foreground text-xs">工作台</span><ChevronRight size={12} className="text-muted-foreground"/><span className="text-xs font-medium">{title}</span><div className="ml-auto flex items-center gap-4"><span className="hidden sm:flex items-center gap-2 text-[11px] text-muted-foreground"><i className="h-1.5 w-1.5 rounded-full bg-emerald-500"/>本地开发环境</span><Button variant="ghost" size="icon" aria-label="切换主题" onClick={()=>setDark(!dark)}>{dark?<Sun size={16}/>:<Moon size={16}/>}</Button></div></header><main className="max-w-[1480px] mx-auto p-5 md:p-8 lg:p-10"><Outlet/></main></div>
  </div>
}
