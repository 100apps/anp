import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";

// 只读实机回归；唯一有效来源的 POST 使用不存在的说话人，必须拒绝且任务文件不变。
const origin = "http://127.0.0.1:8766";
const taskId = "task-a0d1ec97dbd1";
const base = `${origin}/__deep_dub_review/task/${taskId}`;
const path = `D:/github/dub-bench-zh/dubbing-studio-an/work/tasks/${taskId}/task.json`;
const sha = async () =>
  createHash("sha256")
    .update(await readFile(path))
    .digest("hex");
const before = await sha(),
  results = [];
const get = async (path, init) => fetch(base + path, init);
const read = await get("/api/task");
assert.equal(read.status, 200);
const task = await read.json();
assert.equal(task.taskId, taskId);
assert.equal(task.segments.length, 106);
results.push({ name: "真实任务与106段载入", status: read.status });
const segment = task.segments[0];
for (const kind of ["source", "tts"]) {
  const response = await get(
    `/api/waveform?segment=${segment.segmentId}&kind=${kind}`,
  );
  const wave = await response.json();
  assert.equal(response.status, 200);
  assert.ok(wave.peaks.length > 0);
  assert.ok(wave.durationSec > 0);
  results.push({
    name: `实际${kind}波形`,
    status: response.status,
    peaks: wave.peaks.length,
    duration: wave.durationSec,
  });
}
const media = await get(
  `/media/tts?segment=${segment.segmentId}&attempt=${segment.tts.attempt}`,
  { headers: { Range: "bytes=0-255" } },
);
assert.equal(media.status, 206);
assert.equal((await media.arrayBuffer()).byteLength, 256);
assert.match(media.headers.get("content-range"), /^bytes 0-255\//);
results.push({ name: "音频Range流", status: media.status, bytes: 256 });
const body = JSON.stringify({
  speakerId: "__deep_dub_review_missing_speaker__",
  name: "拒绝写入验收",
});
for (const [label, headers, expected] of [
  ["缺少来源", { "Content-Type": "application/json" }, 403],
  [
    "跨站来源",
    {
      "Content-Type": "application/json",
      Origin: "https://example.com",
      "X-Deep-Dub-UI": "local-review",
    },
    403,
  ],
  [
    "原工作台业务校验",
    {
      "Content-Type": "application/json",
      Origin: origin,
      "X-Deep-Dub-UI": "local-review",
    },
    400,
  ],
]) {
  const response = await get("/api/speaker-name", {
    method: "POST",
    headers,
    body,
  });
  assert.equal(response.status, expected, label);
  await response.arrayBuffer();
  results.push({ name: label, status: response.status });
}
const after = await sha();
assert.equal(after, before, "正式任务文件必须不变");
const html = await (await fetch(origin + "/deep-dub.html")).text();
assert.ok(!html.includes("X-Workbench-Token"));
assert.ok(!html.includes("const config="));
results.push({
  name: "正式任务文件未修改，入口未含旧工作台会话",
  passed: true,
});
console.log(
  JSON.stringify(
    {
      checked_at: new Date().toISOString(),
      task: taskId,
      results,
      task_sha256_before: before,
      task_sha256_after: after,
    },
    null,
    2,
  ),
);
