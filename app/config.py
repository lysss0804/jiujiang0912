from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    llm_provider: str = "mock"
    llm_model: str = "mock-model"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    max_output_chars: int = Field(default=8000, ge=100, le=100000)

    # ---- 全链路 LLM 节点级开关 ----
    # 仅当 enable_live_llm=true（由请求/调用方传入）时，以下细粒度开关才生效。
    # 语义：LLM 不可用（无 Key / 余额不足 / 超时 / 输出不合法）时统一降级为确定性模板，
    #      并在各节点状态与 llm_status 中标记 FALLBACK，保证报告始终可生成。
    llm_text_risk_identification: bool = True  # 风险识别 summary 文案
    llm_text_association: bool = True  # 关联分析 trend_desc / cross_dimension_note 文案
    llm_text_evidence: bool = True  # 证据 readable_summary 转述
    llm_consistency_check: bool = True  # 多智能体一致性校验
    # 维度归类节点（旧事件类别 -> 新六维度）：关闭或失败时用确定性别名表降级
    llm_dimension_mapping: bool = True

    # ---- 向量化（RAG embedding）----
    # 复用同一 OpenAI 兼容通道；未配置 embedding_model 时回退到对话模型配置，
    # 两者都为空则退回本地哈希向量（降级），RAG 仍可用。
    embedding_provider: str = ""  # 留空则复用 llm_provider
    embedding_model: str = ""  # 留空则复用 llm_model；仍为空时使用哈希向量
    embedding_base_url: str = ""  # 留空则复用 llm_base_url
    embedding_api_key: str = ""  # 留空则复用 llm_api_key
    embedding_dimensions: int = Field(default=256, ge=8, le=4096)

    rag_top_k: int = Field(default=3, ge=1, le=10)
    knowledge_dir: Path = Path("data/knowledge")
    vector_index_path: Path = Path("data/index/vector_index.json")
    source_data_dir: Path = Path("data/source")

    # ---- 风险分级规则引擎 ----
    rules_config_path: Path = Path("app/rules/rules.yaml")
    # 「近期」统计窗口：可指定单位（week / month）与窗口大小
    # 不传新参数时等价于改造前的「近 12 周」。
    window_unit: str = "week"  # week / month
    window_size: int = Field(default=12, ge=1, le=52)
    # 月的周换算口径：1 月 = 4 周（可配，便于替换为银行正式口径）
    window_weeks_per_month: int = Field(default=4, ge=1, le=6)
    # 兼容旧字段：以周为单位的近期窗口（优先级低于 window_size）
    recent_window_weeks: int = Field(default=12, ge=1, le=52)
    # 趋势研判对照窗口：与近期窗口做对比的上一段等长窗口
    trend_baseline_weeks: int = Field(default=12, ge=1, le=52)

    # ---- 处置门禁：是否允许供应商「重新申报」----
    # 判定为「问题较小」时置为 AWAITING_SUPPLIER_REDECLARE，门禁完全确定性（模型不参与）。
    redeclare_max_score: float = Field(default=0.8, ge=0.0)

    # ---- 报告输出 ----
    report_template_dir: Path = Path("templates/report")
    report_output_dir: Path = Path("artifacts/reports")
    review_output_path: Path = Path("artifacts/reviews/review_feedback.jsonl")

    # ---- 批量分析 ----
    batch_max_workers: int = Field(default=4, ge=1, le=16)

    # ---- API 服务 ----
    # 留空表示不鉴权（本地联调 / 自动化测试）；配置后所有接口需携带 X-API-Key
    api_key: str = ""
    api_cors_origins: str = ""

    workflow_version: str = "0.2.0-draft"
    prompt_version: str = "0.2.0-draft"


@lru_cache
def get_settings() -> Settings:
    return Settings()
