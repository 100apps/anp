"""只读成片检查与已完成运行重放，不创建新的付费译配。"""
import argparse
import asyncio
import json
from uuid import UUID

import httpx
from langgraph_sdk import get_client


async def check(base, run_id):
    async with httpx.AsyncClient(base_url=base, timeout=60, trust_env=False) as http:
        reply = await http.get(f"/api/dubbing/runs/{run_id}")
        reply.raise_for_status()
        before = reply.json()
        assert before["run"]["status"] == "ready_for_review", "只重放已经完成的运行"
        client = get_client(url=base)
        thread = await client.threads.create()
        result = await client.runs.wait(thread["thread_id"], "video_dubbing", input={"run_id": run_id})
        assert result["status"] == "ready_for_review"
        assert set(result["deliverable_ids"]) == {row["id"] for row in before["deliverables"]}
        response = await http.post(f"/api/dubbing/runs/{run_id}/start")
        assert response.status_code == 202
        await asyncio.sleep(.5)
        after = (await http.get(f"/api/dubbing/runs/{run_id}")).json()
        assert len(before["attempts"]) == len(after["attempts"]), "已完成运行不能重新付费生成"
        asset_id = before["deliverables"][-1]["asset_id"]
        media = await http.get(f"/api/dubbing/assets/{asset_id}/content", headers={"Range": "bytes=0-127"})
        assert media.status_code == 206 and len(media.content) == 128
        assert "video/mp4" in media.headers["content-type"]
        print(json.dumps({"运行": run_id, "原生图重放": "通过", "阶段尝试数保持": len(after["attempts"]), "媒体Range": 206}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--base-url", default="http://127.0.0.1:20260")
    options = parser.parse_args()
    asyncio.run(check(options.base_url, str(UUID(options.run))))
