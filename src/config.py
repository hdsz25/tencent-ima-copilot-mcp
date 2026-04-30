"""
配置管理系统 - 基于环境变量的简化版本
"""
import json
import re
import uuid
import base64
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from models import IMAConfig, IMAStatus, KnowledgeBaseCatalog, KnowledgeBaseCatalogEntry


class AppConfig(BaseSettings):
    """应用配置 - 从环境变量读取"""
    # 服务配置
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8081
    mcp_debug: bool = False
    mcp_log_level: str = "INFO"
    mcp_log_file: Optional[str] = None
    mcp_secret_key: str = "default-secret-key-change-in-production"
    mcp_server_name: str = "ima-copilot"
    mcp_server_version: str = "0.2.0"

    # IMA API 配置
    api_endpoint: str = "https://ima.qq.com/cgi-bin/assistant/qa"
    request_timeout: int = 30
    retry_count: int = 3
    ask_concurrency_limit: int = 2
    proxy: Optional[str] = None

    model_config = SettingsConfigDict(
        env_prefix="IMA_",
        env_file=".env",
        extra="ignore"  # 忽略额外的字段
    )

    @property
    def host(self) -> str:
        return self.mcp_host

    @property
    def port(self) -> int:
        return self.mcp_port

    @property
    def debug(self) -> bool:
        return self.mcp_debug

    @property
    def log_level(self) -> str:
        return self.mcp_log_level

    @property
    def log_file(self) -> Optional[str]:
        return self.mcp_log_file

    @property
    def secret_key(self) -> str:
        return self.mcp_secret_key


class IMAEnvironmentConfig(BaseSettings):
    """IMA 认证配置 - 从环境变量读取"""
    # IMA 认证信息
    cookies: str = ""
    x_ima_cookie: str = ""
    x_ima_bkn: str = ""
    
    # 默认配置常量
    DEFAULT_KNOWLEDGE_BASE_ID: str = "7305806844290061"
    DEFAULT_ROBOT_TYPE: int = 5
    DEFAULT_SCENE_TYPE: int = 1
    DEFAULT_MODEL_TYPE: int = 4

    knowledge_base_id: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "IMA_KNOWLEDGE_BASE_ID",
            "knowledgeBaseId",
        ),
    )
    knowledge_base_ids: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "IMA_KNOWLEDGE_BASE_IDS",
            "knowledgeBaseIds",
        ),
    )
    knowledge_base_catalog_file: Optional[str] = Field(
        ".ima_knowledge_bases.json",
        validation_alias=AliasChoices(
            "IMA_KNOWLEDGE_BASE_CATALOG_FILE",
            "knowledgeBaseCatalogFile",
        ),
    )
    uskey: Optional[str] = None
    client_id: Optional[str] = None
    robot_type: int = DEFAULT_ROBOT_TYPE
    scene_type: int = DEFAULT_SCENE_TYPE
    model_type: int = DEFAULT_MODEL_TYPE

    model_config = SettingsConfigDict(
        env_prefix="IMA_",
        env_file=".env",
        extra="ignore"  # 忽略额外的字段
    )


class ConfigManager:
    """简化的配置管理器 - 基于环境变量"""

    def __init__(self):
        self.app_config = AppConfig()
        self.env_config = IMAEnvironmentConfig()
        self._ima_config: Optional[IMAConfig] = None
        self._workspace_root = Path(__file__).resolve().parent.parent

    def _generate_missing_params(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """自动生成缺失的参数"""
        # 生成client_id（如果缺失）
        if not config_data.get('client_id'):
            config_data['client_id'] = str(uuid.uuid4())
            logger.info(f"Generated client_id: {config_data['client_id']}")

        # 生成uskey（如果缺失）
        if not config_data.get('uskey'):
            random_bytes = secrets.token_bytes(32)
            config_data['uskey'] = base64.b64encode(random_bytes).decode('utf-8')
            logger.info("Generated uskey: 32-byte random string")

        # 确保created_at存在
        if not config_data.get('created_at'):
            config_data['created_at'] = datetime.now()
            logger.info("Set created_at to current time")

        return config_data

    @staticmethod
    def _parse_knowledge_base_ids(raw_value: Optional[str]) -> List[str]:
        if not raw_value:
            return []

        parsed_ids: List[str] = []
        for item in raw_value.split(","):
            candidate = item.strip()
            if candidate and candidate not in parsed_ids:
                parsed_ids.append(candidate)

        return parsed_ids

    def _catalog_file_path(self) -> Path:
        configured_path = (self.env_config.knowledge_base_catalog_file or "").strip() or ".ima_knowledge_bases.json"
        path = Path(configured_path)
        if not path.is_absolute():
            path = self._workspace_root / path
        return path

    def get_catalog_file_path(self) -> str:
        return str(self._catalog_file_path())

    def load_knowledge_base_catalog(self) -> KnowledgeBaseCatalog:
        catalog_path = self._catalog_file_path()
        if not catalog_path.exists():
            return KnowledgeBaseCatalog()

        try:
            raw_data = json.loads(catalog_path.read_text(encoding="utf-8"))
            if isinstance(raw_data, list):
                return KnowledgeBaseCatalog(entries=[KnowledgeBaseCatalogEntry.model_validate(item) for item in raw_data])
            return KnowledgeBaseCatalog.model_validate(raw_data)
        except Exception as exc:
            logger.warning(f"加载知识库目录文件失败: {exc}")
            return KnowledgeBaseCatalog()

    def get_knowledge_base_catalog_entries(self) -> List[KnowledgeBaseCatalogEntry]:
        return self.load_knowledge_base_catalog().entries

    def _upsert_env_variable(self, key: str, value: str) -> None:
        env_path = self._workspace_root / ".env"
        rendered_value = json.dumps(value, ensure_ascii=False)
        new_line = f"{key}={rendered_value}"

        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
            if pattern.search(content):
                content = pattern.sub(new_line, content, count=1)
            else:
                if content and not content.endswith("\n"):
                    content += "\n"
                content += new_line + "\n"
        else:
            content = new_line + "\n"

        env_path.write_text(content, encoding="utf-8")

    def persist_knowledge_base_catalog(
        self,
        entries: List[KnowledgeBaseCatalogEntry],
        *,
        update_env: bool = True,
    ) -> KnowledgeBaseCatalog:
        deduplicated_entries: List[KnowledgeBaseCatalogEntry] = []
        seen_ids: set[str] = set()
        for entry in entries:
            if not entry.id or entry.id in seen_ids:
                continue
            seen_ids.add(entry.id)
            deduplicated_entries.append(entry)

        catalog = KnowledgeBaseCatalog(
            synced_at=datetime.now(),
            entries=deduplicated_entries,
        )

        catalog_path = self._catalog_file_path()
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(
            catalog.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )

        if update_env:
            if deduplicated_entries:
                all_ids = ",".join(entry.id for entry in deduplicated_entries)
                self._upsert_env_variable("IMA_KNOWLEDGE_BASE_IDS", all_ids)
                self._upsert_env_variable("IMA_KNOWLEDGE_BASE_ID", deduplicated_entries[0].id)

            configured_catalog_path = (self.env_config.knowledge_base_catalog_file or "").strip() or ".ima_knowledge_bases.json"
            self._upsert_env_variable("IMA_KNOWLEDGE_BASE_CATALOG_FILE", configured_catalog_path)

        self.env_config = IMAEnvironmentConfig()
        self._ima_config = None
        logger.info(f"知识库目录已写入: {catalog_path}")
        return catalog

    def load_config(self, auto_generate: bool = True) -> Optional[IMAConfig]:
        """从环境变量加载配置"""
        try:
            configured_single_kb_id = (self.env_config.knowledge_base_id or "").strip()
            configured_single_kb_ids = self._parse_knowledge_base_ids(configured_single_kb_id)
            configured_multi_kb_ids = self._parse_knowledge_base_ids(self.env_config.knowledge_base_ids)
            configured_catalog_ids = [entry.id for entry in self.get_knowledge_base_catalog_entries()]

            if configured_single_kb_id and len(configured_single_kb_ids) == 1:
                resolved_kb_id = configured_single_kb_id
                resolved_kb_ids = [configured_single_kb_id]
            elif len(configured_single_kb_ids) > 1 and not configured_multi_kb_ids:
                logger.warning(
                    "检测到 IMA_KNOWLEDGE_BASE_ID 包含多个值，将按多知识库模式处理；"
                    "建议改用 IMA_KNOWLEDGE_BASE_IDS"
                )
                resolved_kb_id = configured_single_kb_ids[0]
                resolved_kb_ids = configured_single_kb_ids
            elif configured_multi_kb_ids:
                resolved_kb_id = configured_multi_kb_ids[0]
                resolved_kb_ids = configured_multi_kb_ids
            elif configured_catalog_ids:
                resolved_kb_id = configured_catalog_ids[0]
                resolved_kb_ids = configured_catalog_ids
            else:
                resolved_kb_id = self.env_config.DEFAULT_KNOWLEDGE_BASE_ID
                resolved_kb_ids = []

            # 从环境变量获取配置数据
            config_data = {
                'cookies': self.env_config.cookies,
                'x_ima_cookie': self.env_config.x_ima_cookie,
                'x_ima_bkn': self.env_config.x_ima_bkn,
                'knowledge_base_id': resolved_kb_id,
                'knowledge_base_ids': resolved_kb_ids,
                'uskey': self.env_config.uskey,
                'client_id': self.env_config.client_id,
                'robot_type': self.env_config.robot_type or self.env_config.DEFAULT_ROBOT_TYPE,
                'scene_type': self.env_config.scene_type or self.env_config.DEFAULT_SCENE_TYPE,
                'model_type': self.env_config.model_type or self.env_config.DEFAULT_MODEL_TYPE,
                'timeout': self.app_config.request_timeout,
                'retry_count': self.app_config.retry_count,
                'ask_concurrency_limit': max(1, self.app_config.ask_concurrency_limit),
                'proxy': self.app_config.proxy,
                'created_at': datetime.now()
            }

            # 自动生成缺失的参数
            if auto_generate:
                config_data = self._generate_missing_params(config_data)

            # 验证并创建配置对象
            self._ima_config = IMAConfig(**config_data)
            logger.info("Configuration loaded from environment variables successfully")
            return self._ima_config

        except ValidationError as e:
            logger.error(f"Configuration validation failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error loading config: {e}")
            return None

    def get_config(self) -> Optional[IMAConfig]:
        """获取当前配置"""
        if self._ima_config is None:
            self._ima_config = self.load_config()
        return self._ima_config

    def validate_config(self) -> tuple[bool, Optional[str]]:
        """验证环境变量配置"""
        # 必需认证信息：X-Ima-Cookie 和 X-Ima-Bkn
        required_fields = [
            (self.env_config.x_ima_cookie, "IMA_X_IMA_COOKIE"),
            (self.env_config.x_ima_bkn, "IMA_X_IMA_BKN")
        ]

        for value, name in required_fields:
            if not value or value.strip() == "":
                return False, f"Missing required environment variable: {name}"

        single_kb_ids = self._parse_knowledge_base_ids((self.env_config.knowledge_base_id or "").strip())
        multi_kb_ids = self._parse_knowledge_base_ids(self.env_config.knowledge_base_ids)
        catalog_entries = self.get_knowledge_base_catalog_entries()

        if not single_kb_ids and not multi_kb_ids and not catalog_entries:
            logger.warning("未配置知识库 ID，首次问答前需要同步知识库目录")

        return True, None

    def get_config_status(self) -> IMAStatus:
        """获取配置状态"""
        status = IMAStatus()

        # 验证环境变量配置
        is_valid, error = self.validate_config()
        if is_valid:
            config = self.get_config()
            if config:
                status.is_configured = True
                status.session_info = {
                    'client_id': config.client_id,
                    'knowledge_base_id': config.knowledge_base_id,
                    'knowledge_base_ids': config.knowledge_base_ids,
                    'knowledge_base_catalog_file': self.get_catalog_file_path(),
                    'created_at': config.created_at.isoformat(),
                    'updated_at': config.updated_at.isoformat() if config.updated_at else None,
                }
        else:
            status.error_message = error or "环境变量配置不完整"

        return status


# 全局配置管理器实例
config_manager = ConfigManager()


def get_config() -> Optional[IMAConfig]:
    """获取全局配置"""
    return config_manager.get_config()


def get_app_config() -> AppConfig:
    """获取应用配置"""
    return config_manager.app_config
