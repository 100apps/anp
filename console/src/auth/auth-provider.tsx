import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, setAuthClient, setWorkspace, type Me } from '@/lib/api'

type Auth = { client:SupabaseClient|null;session:Session|null;me:Me|undefined;loading:boolean;error:string;logout:()=>Promise<void>;selectWorkspace:(id:string)=>void }
const Context = createContext<Auth>(null!)
export function AuthProvider({ children }: { children:ReactNode }) {
  const [client,setClient] = useState<SupabaseClient|null>(null)
  const [session,setSession] = useState<Session|null>(null)
  const [loading,setLoading] = useState(true)
  const [error,setError] = useState('')
  const [workspace,chooseWorkspace] = useState('')
  const queryClient = useQueryClient()
  useEffect(() => {
    let unsubscribe:(()=>void)|undefined
    async function configuration(){
      for(let attempt=0;attempt<60;attempt++){
        try{const response=await fetch('/api/public/config');if(response.ok)return await response.json()}catch{/* F5 正在启动 API */}
        await new Promise(resolve=>setTimeout(resolve,500))
      }
      throw Error('连接工作台 API 失败，请检查 F5 的 API 调试进程')
    }
    configuration().then(config=>{
      const supabase=createClient(config.supabase_url,config.supabase_anon_key)
      setAuthClient(supabase);setClient(supabase)
      supabase.auth.getSession().then(({data})=>{setSession(data.session);setLoading(false)})
      const {data}=supabase.auth.onAuthStateChange((_event,newSession)=>{setSession(newSession);setLoading(false)})
      unsubscribe=()=>data.subscription.unsubscribe()
    }).catch(e=>{setError(e.message);setLoading(false)})
    return ()=>unsubscribe?.()
  },[])
  const profile=useQuery({queryKey:['me',session?.user.id,workspace],queryFn:()=>api<Me>('/me'),enabled:!!session})
  useEffect(()=>{if(profile.data)setWorkspace(profile.data.workspace_id)},[profile.data])
  async function logout(){await client?.auth.signOut();setWorkspace('');chooseWorkspace('');queryClient.clear()}
  function selectWorkspace(id:string){setWorkspace(id);chooseWorkspace(id);queryClient.clear()}
  return <Context.Provider value={{client,session,me:profile.data,loading:loading||(!!session&&profile.isLoading),error:error||(profile.error?.message||''),logout,selectWorkspace}}>{children}</Context.Provider>
}
export const useAuth=()=>useContext(Context)
