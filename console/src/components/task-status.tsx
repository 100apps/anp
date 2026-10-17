import { Badge } from '@/components/ui/badge'
const names:Record<string,string>={pending:'待执行',running:'执行中',succeeded:'已完成',failed:'失败',cancelled:'已取消'}
export function TaskStatus({status}:{status:string}){return <Badge variant="secondary" className={status==='succeeded'?'bg-emerald-50 text-emerald-700 border-emerald-100':status==='failed'?'bg-red-50 text-red-700':status==='running'?'bg-blue-50 text-blue-700':''}>{names[status]||status}</Badge>}
