import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Database, ShieldCheck, RefreshCw, Network } from 'lucide-react'
import { api } from '@/lib/api'
import { Card,CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

type Connection={id:string;name:string;url:string;status:string;description:string}
export default function Connections(){
  const query=useQuery({queryKey:['connections'],queryFn:()=>api<Connection[]>('/connections'),refetchInterval:15000})
  return <div className="space-y-7"><div className="flex justify-between items-start"><div><div className="text-[11px] tracking-[.16em] text-muted-foreground mb-2">PLATFORM CONNECTIONS</div><h1 className="text-[28px] font-semibold">连接你的 AI 平台</h1><p className="mt-2 text-muted-foreground">统一入口，独立服务。数据库和对象存储均可通过配置切换。</p></div><Button variant="outline" size="sm" onClick={()=>query.refetch()}><RefreshCw size={14}/>刷新</Button></div>
    {query.error&&<p role="alert" className="text-destructive">{query.error.message}</p>}
    <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">{query.data?.map(c=><Card key={c.id} className="shadow-none"><CardContent className="pt-6"><div className="flex items-center justify-between"><div className="p-2.5 bg-accent rounded-xl text-primary"><Network size={21}/></div><Badge variant="secondary" className={c.status==='ok'?'text-emerald-700 bg-emerald-50':''}>{c.status==='ok'?'已连接':c.status==='external'?'外部工具':'暂不可用'}</Badge></div><h2 className="font-semibold text-lg mt-5">{c.name}</h2><p className="text-xs text-muted-foreground mt-1">{c.description}</p><div className="mt-5 pt-4 border-t flex items-center justify-between"><span className="text-[11px] text-muted-foreground mono">{c.url.replace('http://','')}</span><a href={c.url} target="_blank" rel="noreferrer" aria-label={'打开 '+c.name} className="flex items-center gap-1 text-xs text-primary font-medium">打开<ArrowUpRight size={14}/></a></div></CardContent></Card>)}</div>
    <Card className="shadow-none"><CardContent className="pt-6"><h2 className="font-semibold flex items-center gap-2"><Database size={18}/>共享基础设施</h2><div className="grid md:grid-cols-4 gap-5 mt-6">{[['PostgreSQL','一个实例，多个数据库','DBX 中按组件连接'],['Redis','一个实例，按用途划分','DBX 中查看与管理'],['ClickHouse','一个实例，分析存储','DBX 中查看与管理'],['RustFS','一个实例，独立 buckets','使用 RustFS 控制台']].map(([title,desc,sub])=><div key={title}><h3 className="text-sm font-medium">{title}</h3><p className="text-xs text-muted-foreground mt-2">{desc}</p><p className="text-[11px] text-muted-foreground mt-1">{sub}</p></div>)}</div></CardContent></Card>
    <div className="flex items-start gap-3 text-xs text-muted-foreground"><ShieldCheck size={17} className="shrink-0"/><p>管理入口仅对平台管理员可见。DBX 和第三方管理后台保留各自的登录验证；凭据不会发送到本页面。</p></div>
  </div>
}
