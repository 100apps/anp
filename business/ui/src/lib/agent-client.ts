import { Client } from "@langchain/langgraph-sdk";

export const client = new Client({
  apiUrl: import.meta.env.VITE_AGENT_URL || (import.meta.env.DEV ? location.origin + "/agent" : location.origin)
});

export async function runDemo(graph: string, text: string, onEvent: (name: string, data: unknown) => void) {
  const thread = await client.threads.create();
  const input = graph === "workflow_demo" ? {text} : {messages: [{role: "user", content: text}]};
  const stream = client.runs.stream(thread.thread_id, graph, {input, streamMode: ["updates", "values"]});
  let result: unknown = null;
  for await (const chunk of stream) {
    onEvent(chunk.event, chunk.data);
    if (chunk.event === "error") throw new Error(JSON.stringify(chunk.data));
    if (chunk.event === "values") result = chunk.data;
  }
  return {thread: thread.thread_id, result};
}
