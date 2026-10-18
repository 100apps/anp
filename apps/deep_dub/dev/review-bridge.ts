import type { Plugin } from "../../../console/node_modules/vite";
import { Readable } from "node:stream";

// 仅用于本机旧任务的界面改版验收，不注册到平台路由，不代理任意 URL。
export function reviewBridge(): Plugin {
  return {
    name: "deep-dub-local-review",
    configureServer(server) {
      server.middlewares.use("/__deep_dub_review", async (req, res) => {
        try {
          const incoming = new URL(req.url || "/", "http://127.0.0.1");
          const match = incoming.pathname.match(
            /^\/task\/(task-[a-z0-9-]+)\/(api\/[a-z-]+(?:\/[a-z0-9-]+)?|media\/[a-zA-Z-]+)$/,
          );
          const list =
            incoming.pathname === "/api/tasks" && req.method === "GET";
          if (!match && !list) {
            res.writeHead(404).end();
            return;
          }
          const method = req.method || "GET";
          if (!["GET", "POST"].includes(method)) {
            res.writeHead(405).end();
            return;
          }
          const headers: Record<string, string> = {};
          if (req.headers.range) headers.Range = req.headers.range;
          let body: string | undefined;
          if (method === "POST") {
            const allowed = new Set([
              "api/translation",
              "api/playback",
              "api/select-version",
              "api/speaker-name",
              "api/quality-profile",
              "api/synthesize",
              "api/validate",
              "api/voice-candidates",
              "api/register-voice",
            ]);
            if (!match || !allowed.has(match[2])) {
              res.writeHead(405).end();
              return;
            }
            const origin = req.headers.origin;
            if (
              origin !== `http://${req.headers.host}` ||
              req.headers["x-deep-dub-ui"] !== "local-review"
            ) {
              res.writeHead(403).end();
              return;
            }
            const chunks: Buffer[] = [];
            let size = 0;
            for await (const chunk of req) {
              size += chunk.length;
              if (size > 32768) {
                res.writeHead(413).end();
                return;
              }
              chunks.push(chunk);
            }
            body = Buffer.concat(chunks).toString("utf8");
            // 原工作台会话只在服务端短暂读取，不返回浏览器、不写文件或日志。
            const html = await (
              await fetch(`http://127.0.0.1:8765/task/${match[1]}`)
            ).text();
            const config = html.match(/;const config=(\{[^\n]+?\});<\/script>/);
            if (!config) throw Error("旧工作台会话未就绪，请刷新原工作台");
            headers["X-Workbench-Token"] = JSON.parse(config[1]).token;
            headers["Content-Type"] = "application/json";
            headers.Origin = "http://127.0.0.1:8765";
          }
          const abort = new AbortController();
          res.on("close", () => abort.abort());
          const response = await fetch(
            `http://127.0.0.1:8765${incoming.pathname}${incoming.search}`,
            { method, headers, body, signal: abort.signal },
          );
          res.statusCode = response.status;
          for (const key of [
            "content-type",
            "content-length",
            "content-range",
            "accept-ranges",
          ]) {
            const value = response.headers.get(key);
            if (value) res.setHeader(key, value);
          }
          res.setHeader("Cache-Control", "no-store");
          res.setHeader("X-Content-Type-Options", "nosniff");
          if (response.body)
            Readable.fromWeb(response.body as never)
              .on("error", () => res.destroy())
              .pipe(res);
          else res.end();
        } catch {
          if (!res.headersSent)
            res.writeHead(502, { "Content-Type": "application/json" }).end(
              JSON.stringify({
                error: "本机旧工作台连接失败，请确认 127.0.0.1:8765 正在运行",
              }),
            );
          else res.destroy();
        }
      });
    },
  };
}
