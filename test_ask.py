import asyncio
import sys
import os
sys.path.insert(0, ".")
from ima_server_simple import _rank_knowledge_base_candidates, config_manager

async def test():
    config_manager.load_config()
    candidates = await _rank_knowledge_base_candidates("耳鸣是什么原因引起的", max_candidates=10)
    for c, score in candidates:
        print(f"Candidate: {c.name} - {c.id}, score: {score}")

asyncio.run(test())

