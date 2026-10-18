import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import "@console/styles/globals.css";
import "./studio.css";
import { TaskStudio } from "./task-studio";
import { localReviewAdapter } from "./model";

const params = new URLSearchParams(location.search);
const task = params.get("task") || "task-a0d1ec97dbd1";
const client = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <TaskStudio key={task} adapter={localReviewAdapter(task)} taskId={task} />
      <Toaster theme="dark" richColors closeButton />
    </QueryClientProvider>
  </React.StrictMode>,
);
