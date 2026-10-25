import { useEffect, useState } from "react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

export type User = {id: string; email: string; role: "admin" | "user"};

export function Login({onLogin}: {onLogin: () => Promise<void>}) {
  const [providers,setProviders] = useState<{label:string;path:string}[]>([]);
  useEffect(()=>{fetch("/api/auth/oauth/providers").then(r=>r.json()).then(setProviders).catch(()=>setProviders([]));},[]);
  const [signup, setSignup] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      const response = await fetch("/api/auth/" + (signup ? "signup" : "login"), {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({email, password})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "认证失败");
      if (result.confirmation_required) setMessage("注册成功，请按邮件提示确认邮箱后登录。");
      else {setPassword(""); await onLogin();}
    } catch (error) {setMessage(error instanceof Error ? error.message : "认证服务暂不可用");}
    finally {setBusy(false);}
  }
  return <div className="login-layout">
    <section className="login-intro"><div className="eyebrow">ANP BUSINESS</div><h1>业务工作台</h1><p>从一个工作流开始，逐步构建你的 Agent 应用。</p>
      <div className="login-feature"><strong>Workflow</strong><span>确定性图编排、流式步骤与结果。</span></div>
      <div className="login-feature"><strong>Deep Agent</strong><span>工具调用、资料读取与研究报告。</span></div>
      <div className="login-feature"><strong>平台管理</strong><span>管理员可进入 DBX、Supabase、Langfuse 和 LiteLLM。</span></div>
    </section>
    <section className="login-form-panel"><h2>{signup ? "创建业务账户" : "登录业务账户"}</h2><p>用户身份由 Supabase 管理。</p>
      {providers.map(p=><a className="oauth-login" key={p.path} href={p.path}>{p.label} →</a>)}
      <form onSubmit={submit}><label htmlFor="login-email">邮箱</label><Input id="login-email" type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required disabled={busy}/>
        <label htmlFor="login-password">密码</label><Input id="login-password" type="password" autoComplete={signup ? "new-password" : "current-password"} minLength={8} value={password} onChange={e => setPassword(e.target.value)} required disabled={busy}/>
        {message && <p role="status" className="login-message">{message}</p>}
        <Button type="submit" disabled={busy}>{busy ? "处理中…" : signup ? "注册" : "登录"}</Button>
        <button type="button" className="login-switch" onClick={() => {setSignup(!signup); setMessage("");}} disabled={busy}>{signup ? "已有账户，返回登录" : "没有账户？注册"}</button>
      </form>
    </section>
  </div>;
}
