#!/usr/bin/env python3
"""
IMA Copilot MCP 服务器 - 基于环境变量的简化版本
专注于 MCP 协议实现，配置通过环境变量管理
"""

import sys
import asyncio
import re

from llama_index.core import QueryBundle, Document, VectorStoreIndex, StorageContext, Settings, SimpleKeywordTableIndex
from llama_index.core.schema import NodeWithScore
from llama_index.core.retrievers import BaseRetriever, VectorIndexRetriever, KeywordTableSimpleRetriever
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
import chromadb
from typing import List
import os

class HybridRetriever(BaseRetriever):
    """支持AND/OR模式的混合检索器，实现语义与关键词检索结果的集合运算"""
    def __init__(
        self,
        vector_retriever: VectorIndexRetriever,
        keyword_retriever: KeywordTableSimpleRetriever,
        mode: str = "AND"  # 默认使用交集模式，精确性优先
    ) -> None:
        self._vector_retriever = vector_retriever
        self._keyword_retriever = keyword_retriever
        if mode not in ("AND", "OR"):
            raise ValueError("仅支持AND/OR检索模式，当前模式无效")
        self._mode = mode
        super().__init__()
    
    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        """核心检索逻辑：执行双检索器查询并按模式合并结果"""
        # 1. 分别执行向量检索和关键词检索
        vector_nodes = self._vector_retriever.retrieve(query_bundle)
        keyword_nodes = self._keyword_retriever.retrieve(query_bundle)
        
        # 2. 提取节点ID用于集合运算
        vector_ids = {n.node.node_id for n in vector_nodes}
        keyword_ids = {n.node.node_id for n in keyword_nodes}
        
        # 3. 合并节点到字典，便于通过ID快速查找
        combined_dict = {n.node.node_id: n for n in vector_nodes}
        combined_dict.update({n.node.node_id: n for n in keyword_nodes})
        
        # 4. 根据模式执行集合运算（交集或并集）
        if self._mode == "AND":
            # 仅返回同时满足向量相似和关键词匹配的节点
            retrieve_ids = vector_ids.intersection(keyword_ids)
        else:
            # 返回满足任意条件的节点
            retrieve_ids = vector_ids.union(keyword_ids)
        
        # 5. 根据最终ID集合获取检索结果
        return sorted([combined_dict[rid] for rid in retrieve_ids], key=lambda x: x.score or 0.0, reverse=True)

# 配置 LlamaIndex 默认 embedding (Ollama)
from llama_index.core.llms import MockLLM
import os

def _get_default_ollama_host():
    if os.path.exists('/.dockerenv'):
        return "http://host.docker.internal:11434"
    return "http://127.0.0.1:11434"

ollama_host = os.environ.get("OLLAMA_HOST") or _get_default_ollama_host()
Settings.embed_model = OllamaEmbedding(model_name="embeddinggemma:latest", base_url=ollama_host)
Settings.llm = MockLLM(max_tokens=256)

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from fastmcp import FastMCP
from mcp.types import TextContent
from loguru import logger

# 导入我们的模块
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import config_manager, get_config, get_app_config
from ima_client import IMAAPIClient
from models import KnowledgeBaseCatalogEntry

# 配置详细的调试日志
app_config = get_app_config()

# 创建日志目录
log_dir = Path("logs/debug")
log_dir.mkdir(parents=True, exist_ok=True)

# 生成带时间戳的日志文件
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = log_dir / f"ima_server_{timestamp}.log"

# 配置 loguru
logger.remove()  # 移除默认的 sink
logger.add(
    sys.stderr,
    level=app_config.log_level,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> | <magenta>{extra}</magenta>"
)
logger.add(
    log_file,
    level=app_config.log_level,
    rotation="10 MB",
    retention="1 week",
    encoding="utf-8",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message} | {extra}"
)

logger.info(f"调试日志已启用，日志文件: {log_file}")

# 创建 FastMCP 实例
mcp = FastMCP("IMA Copilot")

# 全局变量
ima_client: IMAAPIClient = None
_token_refreshed: bool = False  # 标记 token 是否已刷新
_client_init_lock = asyncio.Lock()


@dataclass
class KnowledgeBaseQueryResult:
    entry: KnowledgeBaseCatalogEntry
    answer_text: str
    response_blocks: list[TextContent]
    reference_items: list[dict[str, Any]]
    is_error: bool


@dataclass
class KnowledgeBaseCandidateResult:
    query_result: KnowledgeBaseQueryResult
    match_score: float
    response_score: float


def _validate_startup_config() -> tuple[bool, str]:
    """启动配置校验：缺少必需环境变量时阻止服务运行"""
    is_valid, error_message = config_manager.validate_config()
    if is_valid:
        return True, ""

    return False, error_message or "环境变量配置不完整"


_startup_ok, _startup_error = _validate_startup_config()
if not _startup_ok:
    logger.error(f"❌ 启动配置校验失败: {_startup_error}")
    raise SystemExit(1)


def _get_knowledge_base_ids() -> list[str]:
    """获取当前配置中的知识库 ID 列表"""
    config = get_config()
    kb_ids: list[str] = []
    seen_ids: set[str] = set()

    if config:
        for kb_id in (config.knowledge_base_ids or []):
            if kb_id and kb_id not in seen_ids:
                seen_ids.add(kb_id)
                kb_ids.append(kb_id)

    for entry in config_manager.get_knowledge_base_catalog_entries():
        if entry.id and entry.id not in seen_ids:
            seen_ids.add(entry.id)
            kb_ids.append(entry.id)

    return kb_ids


def _get_knowledge_base_entries() -> list[KnowledgeBaseCatalogEntry]:
    catalog_entries = config_manager.get_knowledge_base_catalog_entries()
    if catalog_entries:
        return catalog_entries

    return [
        KnowledgeBaseCatalogEntry(id=kb_id, name=kb_id, category="configured")
        for kb_id in _get_knowledge_base_ids()
    ]


def _normalize_match_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", (value or "").lower()).strip()


def _tokenize_match_text(value: str) -> list[str]:
    normalized_value = _normalize_match_text(value)
    if not normalized_value:
        return []
    return re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", normalized_value)





async def _rank_knowledge_base_candidates(
    question: str,
    *,
    max_candidates: int = 3,
) -> list[tuple[KnowledgeBaseCatalogEntry, float]]:
    entries = _get_knowledge_base_entries()
    if not entries:
        return []

    # 尝试从 ChromaDB 调用 HybridRetriever 进行检索
    db_path = str(Path(config_manager._workspace_root) / "chromadb")
    ranked_entries = []
    
    if os.path.exists(db_path):
        from llama_index.core import load_index_from_storage
        try:
            chroma_client = chromadb.PersistentClient(path=db_path)
            chroma_collection = chroma_client.get_or_create_collection("kb_collection")
            vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
            
            # 从存储加载索引
            vector_index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
            
            storage_context_kw = StorageContext.from_defaults(persist_dir=str(Path(db_path) / "keyword_index"))
            kw_index = load_index_from_storage(storage_context_kw)
            
            # 也可以直接用 hybrid_query_engine.query ，但提取节点并打分更符合当前架构
            # 因为我们需要 entry metadata
            retriever = HybridRetriever(
                vector_retriever=vector_index.as_retriever(similarity_top_k=max_candidates * 5),
                keyword_retriever=kw_index.as_retriever(similarity_top_k=max_candidates * 5),
                mode="OR"  # 因为搜索短句容易没有共现词汇，采用OR更宽松
            )
            
            query_bundle = QueryBundle(f"查询问题：{question}")
            nodes = retriever.retrieve(query_bundle)
            
            entry_dict = {e.id: e for e in entries}
            seen_kbs = set()
            for n in nodes:
                kb_id = n.node.metadata.get("entry_id") or n.node.ref_doc_id
                if kb_id in entry_dict and kb_id not in seen_kbs:
                    score = n.score or 0.0
                    ranked_entries.append((entry_dict[kb_id], score))
                    seen_kbs.add(kb_id)
                    
            if ranked_entries:
                return ranked_entries[:max_candidates]
                
        except Exception as e:
            logger.warning(f"HybridRetriever / ChromaDB 检索异常: {e}")

    # Fallback 如果混合检索失败且只有一个库时
    if len(entries) == 1:
        return [(entries[0], 0.0)]
    return []





def _get_knowledge_base_entry_by_id(knowledge_base_id: str) -> KnowledgeBaseCatalogEntry:
    for entry in _get_knowledge_base_entries():
        if entry.id == knowledge_base_id:
            return entry

    return KnowledgeBaseCatalogEntry(id=knowledge_base_id, name=knowledge_base_id, category="configured")


def _build_reference_block(
    reference_items: list[dict[str, Any]],
    *,
    title: str = "### 📚 参考资料",
) -> TextContent | None:
    if not reference_items:
        return None

    grouped_items: dict[str, list[dict[str, Any]]] = {}
    for item in reference_items:
        source_name = str(item.get("knowledge_base") or "").strip() or "未标注知识库"
        grouped_items.setdefault(source_name, []).append(item)

    reference_lines = [title, ""]
    for source_name, source_items in grouped_items.items():
        reference_lines.append(f"#### 来源知识库：{source_name}")
        reference_lines.append("")
        for index, item in enumerate(source_items, 1):
            item_title = str(item.get("title") or "未知标题").strip()
            item_intro = str(item.get("introduction") or "").strip()

            reference_lines.append(f"{index}. **{item_title}**")
            if item_intro:
                if len(item_intro) > 150:
                    item_intro = item_intro[:150] + "..."
                reference_lines.append(f"   > {item_intro}")
            reference_lines.append("")

    return TextContent(type="text", text="\n".join(reference_lines).strip())


def _build_response_blocks(answer_text: str, reference_items: list[dict[str, Any]]) -> list[TextContent]:
    content_list = [TextContent(type="text", text=answer_text)]
    reference_block = _build_reference_block(reference_items)
    if reference_block:
        content_list.append(reference_block)
    return content_list


def _is_error_response_text(response_text: str) -> bool:
    normalized_text = (response_text or "").strip()
    if not normalized_text:
        return True

    if normalized_text.startswith("[ERROR]"):
        return True

    fallback_markers = (
        "没有收到有效回复",
        "没有收到任何响应",
        "请求超时",
        "认证失败",
        "网络连接失败",
        "询问失败",
    )
    return any(marker in normalized_text for marker in fallback_markers)


def _summarize_answer_snippet(answer_text: str, *, max_length: int = 220) -> str:
    paragraphs = [line.strip() for line in re.split(r"\n+", answer_text or "") if line.strip()]
    if not paragraphs:
        return ""

    snippet = paragraphs[0]
    if len(snippet) > max_length:
        snippet = snippet[:max_length].rstrip() + "..."
    return snippet


def _score_reference_item_relevance(
    question: str,
    item: dict[str, Any],
    *,
    source_match_score: float,
) -> float:
    question_text = _normalize_match_text(question)
    title_text = _normalize_match_text(str(item.get("title") or "").strip())
    intro_text = _normalize_match_text(str(item.get("introduction") or "").strip())
    source_text = _normalize_match_text(str(item.get("knowledge_base") or "").strip())
    reference_text = _normalize_match_text(" ".join([title_text, intro_text, source_text]))
    if not reference_text:
        return 0.0

    question_tokens = set(_tokenize_match_text(question))
    title_tokens = set(_tokenize_match_text(title_text))
    intro_tokens = set(_tokenize_match_text(intro_text))
    reference_tokens = set(_tokenize_match_text(reference_text))

    title_overlap = len(question_tokens & title_tokens)
    intro_overlap = len(question_tokens & intro_tokens)
    relevance_score = min(source_match_score * 0.2, 1.2)
    relevance_score += min(title_overlap * 1.8, 5.4)
    relevance_score += min(intro_overlap * 0.9, 2.7)
    relevance_score += min(len(question_tokens & reference_tokens) * 0.3, 1.2)
    if question_text:
        relevance_score += SequenceMatcher(None, question_text, title_text or reference_text).ratio() * 2.6
        relevance_score += SequenceMatcher(None, question_text, intro_text or reference_text).ratio() * 0.8

    if title_overlap == 0 and SequenceMatcher(None, question_text, title_text or reference_text).ratio() < 0.2:
        relevance_score -= 1.8

    return relevance_score


def _merge_reference_items(
    question: str,
    candidate_results: list[KnowledgeBaseCandidateResult],
    *,
    max_items: int = 8,
    max_items_per_source: int = 3,
    min_relevance_score: float = 1.8,
) -> list[dict[str, Any]]:
    merged_items: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    source_item_counts: dict[str, int] = {}
    fallback_items: list[tuple[dict[str, Any], float]] = []

    for candidate_result in sorted(candidate_results, key=lambda item: item.response_score, reverse=True):
        scored_items = sorted(
            (
                (
                    item,
                    _score_reference_item_relevance(
                        question,
                        item,
                        source_match_score=candidate_result.match_score,
                    ),
                )
                for item in candidate_result.query_result.reference_items
            ),
            key=lambda scored_item: scored_item[1],
            reverse=True,
        )
        if scored_items:
            fallback_items.append(scored_items[0])
        for item, relevance_score in scored_items:
            dedupe_key = (
                str(item.get("id") or "").strip(),
                str(item.get("title") or "").strip(),
            )
            if dedupe_key in seen_keys:
                continue

            source_name = str(item.get("knowledge_base") or "").strip() or "未标注知识库"
            if source_item_counts.get(source_name, 0) >= max_items_per_source:
                continue

            if relevance_score < min_relevance_score:
                continue

            seen_keys.add(dedupe_key)
            merged_items.append(item)
            source_item_counts[source_name] = source_item_counts.get(source_name, 0) + 1
            if len(merged_items) >= max_items:
                return merged_items

    if merged_items:
        return merged_items

    for item, relevance_score in sorted(fallback_items, key=lambda scored_item: scored_item[1], reverse=True):
        if relevance_score <= 0:
            continue

        dedupe_key = (
            str(item.get("id") or "").strip(),
            str(item.get("title") or "").strip(),
        )
        if dedupe_key in seen_keys:
            continue

        source_name = str(item.get("knowledge_base") or "").strip() or "未标注知识库"
        if source_item_counts.get(source_name, 0) >= max_items_per_source:
            continue

        seen_keys.add(dedupe_key)
        merged_items.append(item)
        source_item_counts[source_name] = source_item_counts.get(source_name, 0) + 1
        if len(merged_items) >= min(3, max_items):
            break

    return merged_items


def _score_candidate_response(
    question: str,
    match_score: float,
    query_result: KnowledgeBaseQueryResult,
) -> float:
    response_blocks = query_result.response_blocks
    if not response_blocks:
        return -100.0

    combined_text = "\n".join(block.text.strip() for block in response_blocks if block.text).strip()
    if not combined_text:
        return -100.0

    if query_result.is_error:
        return -100.0

    answer_text = query_result.answer_text.strip()
    if not answer_text:
        return -50.0

    response_score = match_score * 1.2
    response_score += min(len(answer_text) / 160.0, 4.0)

    question_tokens = set(_tokenize_match_text(question))
    answer_tokens = set(_tokenize_match_text(answer_text))
    response_score += min(len(question_tokens & answer_tokens) * 0.8, 4.0)

    if len(response_blocks) > 1 or "参考资料" in combined_text:
        response_score += 2.5

    if len(answer_text) < 20:
        response_score -= 1.5

    fallback_markers = (
        "没有收到有效回复",
        "没有收到任何响应",
        "请求超时",
        "认证失败",
        "网络连接失败",
        "询问失败",
    )
    if any(marker in combined_text for marker in fallback_markers):
        response_score -= 8.0

    weak_answer_markers = (
        "未找到",
        "没有找到",
        "暂无",
        "不清楚",
    )
    if any(marker in answer_text for marker in weak_answer_markers):
        if "没有找到相关" in answer_text:
            response_score -= 50.0
        else:
            response_score -= 1.5

    if query_result.entry.name and query_result.entry.name in answer_text:
        response_score += 0.5

    return response_score


def _build_fused_candidate_response(
    question: str,
    candidate_results: list[KnowledgeBaseCandidateResult],
) -> list[TextContent]:
    best_result = max(candidate_results, key=lambda item: item.response_score)
    successful_results = [
        item for item in candidate_results
        if not item.query_result.is_error and item.query_result.answer_text.strip()
    ]
    if not successful_results:
        return best_result.query_result.response_blocks

    primary_result = max(successful_results, key=lambda item: item.response_score)
    supporting_results: list[KnowledgeBaseCandidateResult] = []
    for item in successful_results:
        if item.query_result.entry.id == primary_result.query_result.entry.id:
            continue
        if item.response_score < max(primary_result.response_score * 0.55, 2.5):
            continue
        similarity = SequenceMatcher(
            None,
            primary_result.query_result.answer_text,
            item.query_result.answer_text,
        ).ratio()
        if similarity >= 0.96:
            continue
        supporting_results.append(item)

    fused_results = [primary_result, *supporting_results[:2]]
    merged_reference_items = _merge_reference_items(question, fused_results)

    fused_source_names = "、".join(item.query_result.entry.name for item in fused_results)
    answer_lines = [
        f"以下内容综合了 {fused_source_names} 的检索结果。",
        "",
        primary_result.query_result.answer_text.strip(),
    ]
    if len(fused_results) > 1:
        answer_lines.extend(["", "### 补充信息"])
        for item in fused_results[1:]:
            snippet = _summarize_answer_snippet(item.query_result.answer_text)
            if not snippet:
                continue
            answer_lines.append(f"- 来自「{item.query_result.entry.name}」的补充结论: {snippet}")

    fused_notice = TextContent(
        type="text",
        text=(
            f"[知识库匹配] 已并行检索 {len(candidate_results)} 个候选知识库，"
            f"融合 {len(fused_results)} 个候选库的结果，主结果来自「{primary_result.query_result.entry.name}」。"
        ),
    )
    fused_answer_block = TextContent(type="text", text="\n".join(answer_lines).strip())

    response_blocks = [fused_notice, fused_answer_block]
    merged_reference_block = _build_reference_block(merged_reference_items, title="### 📚 合并参考资料")
    if merged_reference_block:
        response_blocks.append(merged_reference_block)
    return response_blocks


async def _query_target_kb(question: str, entry: KnowledgeBaseCatalogEntry) -> KnowledgeBaseQueryResult:
    global ima_client

    request_kb_id = entry.id.strip()
    try:
        logger.debug("发送问题", length=len(question), knowledge_base_id=request_kb_id)
        mcp_safe_timeout = 300
        messages = await ima_client.ask_question_complete(
            question,
            timeout=mcp_safe_timeout,
            knowledge_base_id=request_kb_id,
        )

        if not messages:
            logger.warning("⚠️ 未收到响应", knowledge_base_id=request_kb_id)
            answer_text = "[ERROR] 没有收到任何响应，或者请求超时未产生任何输出"
            return KnowledgeBaseQueryResult(
                entry=entry,
                answer_text=answer_text,
                response_blocks=[TextContent(type="text", text=answer_text)],
                reference_items=[],
                is_error=True,
            )

        logger.info("-" * 80)
        logger.info(f"完整 QA 结果 (知识库: {request_kb_id}, 原始消息列表):")
        for index, message in enumerate(messages):
            logger.info(f"  消息 {index + 1} (类型: {message.type.value}): {message.content[:200]}...")
        logger.info("-" * 80)

        answer_text = ima_client._extract_text_content(messages)
        if not answer_text:
            error_messages = [message.content for message in messages if message.type == 'system']
            if error_messages:
                answer_text = f"[ERROR] {'; '.join(error_messages)}"
                logger.warning("⚠️ 未提取到文本，返回系统错误", error=answer_text, knowledge_base_id=request_kb_id)
            else:
                answer_text = "没有收到有效回复"

        reference_items: list[dict[str, Any]] = []
        try:
            reference_items = ima_client._extract_knowledge_info(messages)
            for reference_item in reference_items:
                if not str(reference_item.get("knowledge_base") or "").strip():
                    reference_item["knowledge_base"] = entry.name
            if reference_items:
                logger.debug("✅ 添加参考资料", count=len(reference_items), knowledge_base_id=request_kb_id)
        except Exception as exc:
            logger.warning(f"提取参考资料失败: {exc}", knowledge_base_id=request_kb_id)

        response_blocks = _build_response_blocks(answer_text, reference_items)
        logger.info("-" * 80)
        logger.info(f"ask 工具返回内容 (知识库: {request_kb_id}, Block 数量: {len(response_blocks)}):")
        for index, block in enumerate(response_blocks):
            logger.info(f"Block {index + 1} ({len(block.text)} chars):\n{block.text[:200]}...")
        logger.info("-" * 80)

        return KnowledgeBaseQueryResult(
            entry=entry,
            answer_text=answer_text,
            response_blocks=response_blocks,
            reference_items=reference_items,
            is_error=_is_error_response_text(answer_text),
        )

    except Exception as exc:
        logger.exception("询问 IMA 时发生错误", knowledge_base_id=request_kb_id)
        error_str = str(exc).lower()
        if "超时" in str(exc) or "timeout" in error_str:
            answer_text = "[ERROR] 请求超时，请稍后重试"
        elif "认证" in str(exc) or "auth" in error_str:
            answer_text = "[ERROR] 认证失败，请检查 IMA 配置信息"
        elif "网络" in str(exc) or "network" in error_str or "connection" in error_str:
            answer_text = "[ERROR] 网络连接失败，请检查网络设置"
        else:
            answer_text = f"[ERROR] 询问失败: {str(exc)}"

        return KnowledgeBaseQueryResult(
            entry=entry,
            answer_text=answer_text,
            response_blocks=[TextContent(type="text", text=answer_text)],
            reference_items=[],
            is_error=True,
        )


async def _ask_with_candidate_selection(
    question: str,
    candidates: list[tuple[KnowledgeBaseCatalogEntry, float]],
) -> list[TextContent]:
    async def _query_candidate(
        entry: KnowledgeBaseCatalogEntry,
        match_score: float,
    ) -> KnowledgeBaseCandidateResult:
        query_result = await _query_target_kb(question=question, entry=entry)
        response_score = _score_candidate_response(question, match_score, query_result)
        return KnowledgeBaseCandidateResult(
            query_result=query_result,
            match_score=match_score,
            response_score=response_score,
        )

    candidate_tasks = [
        _query_candidate(entry, match_score)
        for entry, match_score in candidates
    ]
    candidate_results = await asyncio.gather(*candidate_tasks)
    best_result = max(candidate_results, key=lambda item: item.response_score)

    logger.info(
        "✅ 候选知识库并发检索完成",
        selected_knowledge_base_id=best_result.query_result.entry.id,
        selected_knowledge_base_name=best_result.query_result.entry.name,
        candidate_count=len(candidate_results),
        selected_match_score=round(best_result.match_score, 3),
        selected_response_score=round(best_result.response_score, 3),
    )
    logger.debug(
        "候选知识库评分明细",
        candidates=[
            {
                "knowledge_base_id": item.query_result.entry.id,
                "knowledge_base_name": item.query_result.entry.name,
                "match_score": round(item.match_score, 3),
                "response_score": round(item.response_score, 3),
            }
            for item in candidate_results
        ],
    )
    return _build_fused_candidate_response(question, candidate_results)


def _is_multi_knowledge_base_mode() -> bool:
    return len(_get_knowledge_base_ids()) > 1


def _validate_knowledge_base_id(knowledge_base_id: str) -> tuple[bool, str]:
    kb_id = (knowledge_base_id or "").strip()
    if not kb_id:
        return False, "[ERROR] knowledge_base_id 不能为空"

    allowed_ids = _get_knowledge_base_ids()
    if kb_id not in allowed_ids:
        return False, (
            "[ERROR] knowledge_base_id 不在允许列表中，"
            f"可用值: {', '.join(allowed_ids)}"
        )

    return True, ""


async def _ask_with_target_kb(question: str, knowledge_base_id: str) -> list[TextContent]:
    """执行一次指定知识库的问答"""
    if not question or not question.strip():
        return [TextContent(type="text", text="[ERROR] 问题不能为空")]

    is_valid_kb_id, kb_error = _validate_knowledge_base_id(knowledge_base_id)
    if not is_valid_kb_id:
        return [TextContent(type="text", text=kb_error)]

    entry = _get_knowledge_base_entry_by_id(knowledge_base_id.strip())
    query_result = await _query_target_kb(question=question, entry=entry)
    return query_result.response_blocks


async def _sync_knowledge_bases() -> tuple[list[KnowledgeBaseCatalogEntry], str]:
    global ima_client

    if not await ensure_client_ready():
        raise RuntimeError("IMA 客户端初始化或 token 刷新失败，请检查配置")

    entries = await ima_client.fetch_knowledge_base_catalog()
    
    # 获取所有条目的向量化表示并保存至目录
    if entries:
        logger.info(f"正在为 {len(entries)} 个知识库生成向量嵌入并存入 ChromaDB...")
        
        # LlamaIndex 结合 ChromaDB 以及 KeywordTableIndex
        documents = []
        for entry in entries:
            target_for_emb = f"知识库名称：{entry.name}。相关描述：{entry.description or ''} {entry.introduction or ''}".strip()
            # Node doc_id 与 entry.id 绑定，写入文档
            doc = Document(text=target_for_emb, doc_id=entry.id, metadata={"name": entry.name, "category": entry.category, "entry_id": entry.id})
            documents.append(doc)
            
        # 设置 ChromaDB 客户端和集合
        db_path = str(Path(config_manager._workspace_root) / "chromadb")
        os.makedirs(db_path, exist_ok=True)
        
        chroma_client = chromadb.PersistentClient(path=db_path)
        chroma_collection = chroma_client.get_or_create_collection("kb_collection")
        
        # 生成向量索引和关键词索引
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        
        # vector index (嵌入到 ChromaDB)
        VectorStoreIndex.from_documents(documents, storage_context=storage_context)
        
        # 关键词索引 (本地)
        storage_context_kw = StorageContext.from_defaults()
        SimpleKeywordTableIndex.from_documents(documents, storage_context=storage_context_kw)
        storage_context_kw.persist(persist_dir=str(Path(db_path) / "keyword_index"))
        
        logger.info("ChromaDB 与关键词索引库生成完毕")
                
    config_manager.persist_knowledge_base_catalog(entries)
    return entries, config_manager.get_catalog_file_path()


# @mcp.on_shutdown()
# async def on_shutdown():
#     """服务器关闭时的清理工作"""
#     global ima_client
#     if ima_client:
#         logger.info("👋 正在关闭 IMA 客户端会话...")
#         await ima_client.close()
#         logger.info("✅ 客户端会话已关闭")


async def ensure_client_ready():
    """确保客户端已初始化并且 token 有效"""
    global ima_client, _token_refreshed

    if not ima_client:
        async with _client_init_lock:
            if not ima_client:
                logger.info("🚀 首次请求，初始化 IMA 客户端...")

                config = get_config()
                if not config:
                    logger.error("❌ 配置未加载")
                    return False

                try:
                    # 启用原始SSE日志
                    config.enable_raw_logging = True
                    config.raw_log_dir = "logs/debug/raw"
                    config.raw_log_on_success = False

                    ima_client = IMAAPIClient(config)
                    logger.debug("✅ IMA 客户端初始化成功")
                except Exception as e:
                    logger.exception("❌ IMA 客户端初始化失败")
                    return False
    
    # 如果还没刷新过 token，提前刷新一次（添加超时保护）
    if not _token_refreshed:
        logger.info("🔄 验证 token...")
        try:
            import asyncio
            # 为token刷新也添加超时保护（15秒）
            token_valid = await asyncio.wait_for(
                ima_client.ensure_valid_token(),
                timeout=15.0
            )
            
            if token_valid:
                _token_refreshed = True
                logger.info("✅ Token 验证成功")
                return True
            else:
                logger.warning("⚠️ Token 验证失败，尝试继续...")
                # 即使刷新失败也标记为 True，让后续请求在 ask_question 内部触发自动重试逻辑
                _token_refreshed = True 
                return True
        except asyncio.TimeoutError:
            logger.error("❌ Token 验证超时")
            return False
        except Exception as e:
            logger.exception("❌ Token 验证异常")
            return False
    
    return True


@mcp.tool()
async def list_knowledge_bases() -> list[TextContent]:
    """获取所有可用知识库的列表，包含名称、ID和描述信息。
    在调用 ask_with_kb 之前，建议先调用此工具以选择最匹配的知识库。
    """
    entries = _get_knowledge_base_entries()
    if not entries:
        try:
            entries, _ = await _sync_knowledge_bases()
        except Exception as exc:
            return [TextContent(type="text", text=f"[ERROR] 暂无可用知识库: {exc}")]

    response_lines = ["当前可用的知识库列表：\n"]
    for entry in entries:
        desc = entry.description or entry.introduction or "暂无描述"
        response_lines.append(f"- 【{entry.name}】(ID: {entry.id})\n  描述: {desc}\n")
        
    return [TextContent(type="text", text="\n".join(response_lines).strip())]


@mcp.tool()
async def ask(question: str, num: int = 5) -> list[TextContent]:
    """向腾讯 IMA 知识库询问任何问题。
    注意：此工具在未指定知识库时会基于关键词匹配多个候选库并在后台并发请求，速度较慢。
    优化建议：为了提高准确度并加快响应速度，建议先调用 list_knowledge_bases 获取知识库描述，
    由主模型根据问题语义选择最相关的一个或多个知识库，然后明确调用 ask_with_kb。

    Args:
        question: 要询问的问题
        num: 并行查询的最相关知识库数量，默认为 4

    Returns:
        IMA 知识库的回答
    """
    global ima_client
    
    # 生成请求ID用于日志追踪
    import uuid
    request_id = str(uuid.uuid4())[:8]
    
    # 绑定上下文
    with logger.contextualize(request_id=request_id):
        # 确保客户端已初始化并且 token 有效
        if not await ensure_client_ready():
            return [TextContent(type="text", text="[ERROR] IMA 客户端初始化或 token 刷新失败，请检查配置")]

        entries = _get_knowledge_base_entries()
        if not entries:
            try:
                entries, _ = await _sync_knowledge_bases()
            except Exception as exc:
                return [TextContent(type="text", text=f"[ERROR] 暂无可用知识库，请先执行 sync_knowledge_bases: {exc}")]

        has_catalog_names = any(entry.name and entry.name != entry.id for entry in entries)
        if _is_multi_knowledge_base_mode() and not has_catalog_names:
            kb_ids = _get_knowledge_base_ids()
            return [
                TextContent(
                    type="text",
                    text=(
                        "[ERROR] 当前为多知识库模式，但尚未同步知识库名称，请先执行 sync_knowledge_bases，或改用 ask_with_kb 并传入 knowledge_base_id。"
                        f"可用值: {', '.join(kb_ids)}"
                    ),
                )
            ]

        logger.debug("🔍 ask 工具调用", question_preview=question[:50], num=num)

        ranked_candidates = await _rank_knowledge_base_candidates(question, max_candidates=num)
        if not ranked_candidates:
            return [TextContent(type="text", text="[ERROR] 未找到可用知识库，请先执行 sync_knowledge_bases")]

        # 相关性阈值阻断：如果最高分都低于设定阈值，直接让大模型人工确认
        top_score = ranked_candidates[0][1]
        threshold = 0.15  # 基于 Ollama embedding 的正常相似度基准设立的安全阈值
        if top_score < threshold:
            logger.warning(f"所有知识库相关性得分过低 (最高分: {top_score:.2f} < 阈值: {threshold})，中断查询。")
            return [TextContent(
                type="text", 
                text="没有找到相关知识库请确认后重新手动选择数据库用ask_with_kb"
            )]

        if len(ranked_candidates) == 1:
            selected_entry, _ = ranked_candidates[0]
            logger.info(
                "✅ 自动匹配知识库",
                knowledge_base_id=selected_entry.id,
                knowledge_base_name=selected_entry.name,
            )
            return await _ask_with_target_kb(question=question, knowledge_base_id=selected_entry.id)

        logger.info(
            "🔀 启用多候选知识库检索",
            candidate_count=len(ranked_candidates),
            candidate_names=[entry.name for entry, _ in ranked_candidates],
        )
        return await _ask_with_candidate_selection(question=question, candidates=ranked_candidates)


@mcp.tool()
async def ask_with_kb(question: str, knowledge_base_id: str) -> list[TextContent]:
    """向指定知识库询问问题（多知识库模式使用）

    Args:
        question: 要询问的问题
        knowledge_base_id: 目标知识库 ID（必须在配置的知识库列表中）

    Returns:
        IMA 知识库的回答
    """
    import uuid

    request_id = str(uuid.uuid4())[:8]
    with logger.contextualize(request_id=request_id):
        if not await ensure_client_ready():
            return [TextContent(type="text", text="[ERROR] IMA 客户端初始化或 token 刷新失败，请检查配置")]

        logger.debug(
            "🔍 ask_with_kb 工具调用",
            question_preview=question[:50],
            knowledge_base_id=knowledge_base_id,
        )
        return await _ask_with_target_kb(question=question, knowledge_base_id=knowledge_base_id)


@mcp.tool()
async def sync_knowledge_bases(random_string: str = "") -> list[TextContent]:
    """同步 IMA 个人/共享知识库目录，并写入本地配置文件"""
    try:
        entries, catalog_file = await _sync_knowledge_bases()
    except Exception as exc:
        logger.exception("同步知识库目录失败")
        return [TextContent(type="text", text=f"[ERROR] 同步知识库目录失败: {exc}")]

    grouped_entries: dict[str, list[KnowledgeBaseCatalogEntry]] = {}
    for entry in entries:
        grouped_entries.setdefault(entry.category, []).append(entry)

    response_lines = [
        f"已同步 {len(entries)} 个知识库。",
        f"目录文件: {catalog_file}",
        "",
    ]
    for category, category_entries in grouped_entries.items():
        response_lines.append(f"[{category}]")
        for entry in category_entries:
            desc = entry.description or entry.introduction or ""
            desc_str = f" - 描述: {desc}" if desc else ""
            response_lines.append(f"- {entry.name} ({entry.id}){desc_str}")
        response_lines.append("")

    response_lines.append("已自动写回 .env 中的 IMA_KNOWLEDGE_BASE_ID / IMA_KNOWLEDGE_BASE_IDS。")
    return [TextContent(type="text", text="\n".join(response_lines).strip())]


@mcp.resource("ima://config")
def get_config_resource() -> str:
    """获取当前配置信息（不包含敏感数据）"""
    try:
        config = get_config()
        if not config:
            return "配置未加载"

        # 返回非敏感的配置信息
        config_info = "IMA 配置信息:\n"
        config_info += f"客户端ID: {config.client_id}\n"
        config_info += f"默认知识库ID: {config.knowledge_base_id}\n"
        config_info += f"可用知识库ID: {', '.join(config.knowledge_base_ids)}\n"
        config_info += f"知识库模式: {'多知识库' if len(config.knowledge_base_ids) > 1 else '单知识库'}\n"
        config_info += f"知识库目录文件: {config_manager.get_catalog_file_path()}\n"
        config_info += f"请求超时: {config.timeout}秒\n"
        config_info += f"重试次数: {config.retry_count}\n"
        config_info += f"代理设置: {config.proxy or '未设置'}\n"
        config_info += f"创建时间: {config.created_at}\n"
        if config.updated_at:
            config_info += f"更新时间: {config.updated_at}\n"

        return config_info

    except Exception as e:
        logger.error(f"获取配置资源时发生错误: {e}")
        return f"[ERROR] 获取配置失败: {str(e)}"


@mcp.resource("ima://help")
def get_help_resource() -> str:
    """获取使用帮助信息"""
    help_text = """
# IMA Copilot MCP 服务器帮助

## 概述
这是基于环境变量配置的 IMA Copilot MCP 服务器，提供腾讯 IMA 知识库的 MCP 协议接口。

## 配置方式
通过环境变量或 .env 文件配置 IMA 认证信息：

1. 复制 .env.example 为 .env
2. 填入从浏览器获取的认证信息：
   - IMA_COOKIES: 完整的 cookies 字符串
   - IMA_X_IMA_COOKIE: X-Ima-Cookie 请求头
   - IMA_X_IMA_BKN: X-Ima-Bkn 请求头

## 工具
- `ask`: 向 IMA 知识库询问问题
- `ask_with_kb`: 向指定知识库询问问题（多知识库模式推荐）
- `sync_knowledge_bases`: 自动同步个人/共享知识库目录并写入本地配置

## 资源
- `ima://config`: 查看配置信息
- `ima://help`: 查看帮助信息

## 启动方式
```bash
# 使用 fastmcp 命令启动
fastmcp run ima_server_simple.py:mcp --transport http --host 127.0.0.1 --port 8081

# 或使用 Python 直接运行
python ima_server_simple.py
```

## 连接方式
使用 MCP Inspector 连接到: http://127.0.0.1:8081/mcp
"""
    return help_text


def main():
    """主函数 - 直接启动服务器时使用"""
    app_config = get_app_config()

    print("IMA Copilot MCP 服务器")
    print("=" * 50)
    print("版本: 简化版 (基于环境变量)")
    print(f"服务地址: http://{app_config.host}:{app_config.port}")
    print(f"MCP 端点: http://{app_config.host}:{app_config.port}/mcp")
    print(f"日志级别: {app_config.log_level}")
    print("=" * 50)

    # 验证配置
    is_valid, error_message = _validate_startup_config()
    if not is_valid:
        print(f"[ERROR] 启动失败: {error_message}")
        sys.exit(1)

    config = get_config()
    if not config:
        print("[ERROR] 配置加载失败，请检查环境变量")
        sys.exit(1)

    print("[OK] 配置加载成功，必需认证信息已设置")
    print(f"[INFO] 默认知识库: {config.knowledge_base_id}")
    print(f"[INFO] 可用知识库: {', '.join(config.knowledge_base_ids)}")

    print("=" * 50)
    print("启动命令:")
    print(f"fastmcp run ima_server_simple.py:mcp --transport http --host {app_config.host} --port {app_config.port}")
    print("=" * 50)


if __name__ == "__main__":
    main()


__all__ = ["mcp"]
