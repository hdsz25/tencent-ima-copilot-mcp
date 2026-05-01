import asyncio
import sys
import os
from pathlib import Path
sys.path.insert(0, ".")
from ima_server_simple import sync_knowledge_bases, config_manager

async def test():
    await sync_knowledge_bases()
    catalog = config_manager.load_knowledge_base_catalog()
    for e in catalog.entries:
        if e.embedding:
            print(f"KB {e.name} has embedding of length {len(e.embedding)}")
        else:
            print(f"KB {e.name} has NO embedding!")

asyncio.run(test())
