import re

with open("ima_server_simple.py", "r", encoding="utf-8") as f:
    text = f.read()

imports_chunk = """from llama_index.core import QueryBundle, Document, VectorStoreIndex, StorageContext, Settings, SimpleKeywordTableIndex
from llama_index.core.schema import NodeWithScore
from llama_index.core.retrievers import BaseRetriever, VectorIndexRetriever, KeywordTableSimpleRetriever
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
import chromadb
from typing import List
import os

class HybridRetriever(BaseRetriever):
    \"\"\"支持AND/OR模式的混合检索器，实现语义与关键词检索结果的集合运算\"\"\"
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
        \"\"\"核心检索逻辑：执行双检索器查询并按模式合并结果\"\"\"
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
Settings.embed_model = OllamaEmbedding(model_name="embeddinggemma:latest", base_url="http://127.0.0.1:11434")

"""

if "class HybridRetriever" not in text:
    text = text.replace("import httpx\n", "import httpx\n\n" + imports_chunk)


sync_pattern = r"async def _sync_knowledge_bases\(\) -> tuple\[list\[KnowledgeBaseCatalogEntry\], str\]:.*?return entries, config_manager\.get_catalog_file_path\(\)"
sync_replacement = """async def _sync_knowledge_bases() -> tuple[list[KnowledgeBaseCatalogEntry], str]:
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
            doc = Document(text=target_for_emb, doc_id=entry.id, metadata={"name": entry.name, "category": entry.category})
            documents.append(doc)
            
        # 设置 ChromaDB 客户端和集合
        db_path = str(Path(config_manager._workspace_root) / "data" / "chromadb")
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
    return entries, config_manager.get_catalog_file_path()"""

text = re.sub(sync_pattern, sync_replacement, text, flags=re.DOTALL)

rank_pattern = r"async def _rank_knowledge_base_candidates\(.*?return shortlisted_entries\[:max_candidates\]"
rank_replacement = """async def _rank_knowledge_base_candidates(
    question: str,
    *,
    max_candidates: int = 3,
) -> list[tuple[KnowledgeBaseCatalogEntry, float]]:
    entries = _get_knowledge_base_entries()
    if not entries:
        return []

    # 尝试从 ChromaDB 调用 HybridRetriever 进行检索
    db_path = str(Path(config_manager._workspace_root) / "data" / "chromadb")
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
                vector_retriever=vector_index.as_retriever(similarity_top_k=max_candidates),
                keyword_retriever=kw_index.as_retriever(),
                mode="OR"  # 因为搜索短句容易没有共现词汇，采用OR更宽松
            )
            
            query_bundle = QueryBundle(f"查询问题：{question}")
            nodes = retriever.retrieve(query_bundle)
            
            entry_dict = {e.id: e for e in entries}
            for n in nodes:
                kb_id = n.node.node_id
                if kb_id in entry_dict:
                    score = n.score or 0.0
                    ranked_entries.append((entry_dict[kb_id], score))
                    
            if ranked_entries:
                return ranked_entries[:max_candidates]
                
        except Exception as e:
            logger.warning(f"HybridRetriever / ChromaDB 检索异常: {e}")

    # Fallback 如果混合检索失败且只有一个库时
    if len(entries) == 1:
        return [(entries[0], 0.0)]
    return []"""

text = re.sub(rank_pattern, rank_replacement, text, flags=re.DOTALL)

with open("ima_server_simple.py", "w", encoding="utf-8") as f:
    f.write(text)
print("done")
