import type { SupabaseClient } from '@supabase/supabase-js'
let client: SupabaseClient
let workspace = ''
const appSessionCleanups = new Map<string,()=>Promise<void>>()
export function registerSessionCleanup(appId:string,cleanup:()=>Promise<void>) {appSessionCleanups.set(appId,cleanup)}
export async function clearAppSessions() {await Promise.allSettled([...appSessionCleanups.values()].map(cleanup=>cleanup()))}
export function setAuthClient(value: SupabaseClient) { client = value }
export function setWorkspace(value: string) { workspace = value }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { data } = await client.auth.getSession()
  const headers: Record<string,string> = { 'Content-Type': 'application/json', ...(options.headers as Record<string,string>) }
  if (data.session) headers.Authorization = `Bearer ${data.session.access_token}`
  if (workspace) headers['X-Workspace-ID'] = workspace
  const response = await fetch('/api' + path, { ...options, headers })
  const body = await response.json()
  if (!response.ok) throw new Error(body.error?.message || (typeof body.detail === 'string' ? body.detail : '请求未完成，请检查输入或服务状态'))
  return body
}
export type Me = { id:string;email:string;workspace_id:string;role:string;permissions:string[];workspaces:{id:string;name:string}[];is_platform_admin:boolean }
export type Task = { id:string;app_id:string;title:string;status:string;mode:string|null;created_at:string;input:Record<string,unknown>;error:string|null;result:Record<string,unknown>|null;nodes?:{node:string;output:unknown;created_at:string}[] }
export type TaskSummary = { total:number;pending:number;running:number;succeeded:number;failed:number;cancelled:number }
export type Result = { task_id:string;report:Record<string,unknown>;object_path:string }
