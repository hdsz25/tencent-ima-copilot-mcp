import asyncio
import sys
import os
from pathlib import Path
sys.path.insert(0, ".")
from ima_server_simple import _get_knowledge_base_entries, _score_knowledge_base_match, _get_ollama_embedding, _cosine_similarity

async def test():
    entries = _get_knowledge_base_entries()
    query = "基金"
    q_emb = await _get_ollama_embedding(query)
    print(f"Query: {query}")
    results = []
    for e in entries:
        combined_target = f"{e.name} {e.description or ''} {e.introduction or ''}".strip()
        # add context prefix
        q_emb = await _get_ollama_embedding("查询：" + query)
        text_for_emb = "知识库名称：" + e.name + "。描述：" + (e.description or e.introduction or "无")
        t_emb = await _get_ollama_embedding(text_for_emb)
        sim = _cosine_similarity(q_emb, t_emb)
        score = await _score_knowledge_base_match(query, e, q_emb)
        results.append((sim, score, e.name, combined_target[:30]))
    results.sort(key=lambda x: x[0], reverse=True)
    for sim, score, name, desc in results:
        print(f"CosSim: {sim:.4f} | Total: {score:.2f} | KB: {name} | Text: {desc}")

asyncio.run(test())
