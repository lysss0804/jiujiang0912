import argparse
import json
import sys
from pathlib import Path

from app.common.logging import configure_logging
from app.config import get_settings
from app.demo import run_demo, run_real_data_demo
from app.schemas.output_contract import build_public_assessment



def _print(payload) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def command_demo() -> None:
    result = run_demo()
    _print(build_public_assessment(result))


def command_rag_demo() -> None:
    from app.rag.service import build_rag_service

    settings = get_settings()
    # 与线上检索共用同一构建路径，确保 embedding 通道与哈希维度配置一致
    service = build_rag_service(settings.vector_index_path)
    path = settings.knowledge_dir / "mock_policy.md"
    count = service.rebuild([(path, True)])
    _print(
        {
            "indexed_chunks": count,
            "mode": "APPROVED_SIMULATION_ONLY",
            "vector_source": service.store.vector_source,
            "results": service.search("证据失败后能否处置", top_k=2, approved_only=True),
        }
    )


def command_real_data_demo() -> None:
    settings = get_settings()
    result = run_real_data_demo(source_dir=settings.source_data_dir)
    _print(build_public_assessment(result))


def command_real_data_demo_live() -> None:
    settings = get_settings()
    result = run_real_data_demo(source_dir=settings.source_data_dir, enable_live_llm=True)
    _print(build_public_assessment(result))


def command_analyze(supplier_id: str = "S‑NOR001") -> None:
    """分析指定供应商（默认 S‑NOR001）。"""
    from app.service import analyze_supplier
    from app.schemas.report import build_risk_report

    settings = get_settings()
    result = analyze_supplier(supplier_id=supplier_id, source_dir=settings.source_data_dir)
    _print(result.get("risk_report") or build_risk_report(result))


def command_batch() -> None:
    """批量分析全部供应商并输出重点监测名单。"""
    from app.batch.service import analyze_batch

    settings = get_settings()
    result = analyze_batch(source_dir=settings.source_data_dir)
    _print(
        {
            "current_week": result["current_week"],
            "analyzed_count": result["analyzed_count"],
            "level_counts": result["level_counts"],
            "report_period_counts": result["report_period_counts"],
            "weekly_report_count": len(result["weekly_report_suppliers"]),
            "monthly_report_count": len(result["monthly_report_suppliers"]),
            "watchlist": result["watchlist"],
        }
    )


def command_export_report(supplier_id: str = "S‑NOR001") -> None:
    """生成某供应商的 Markdown 报告文件。"""
    from app.schemas.report import build_risk_report
    from app.reporting.renderer import render_markdown
    from app.service import analyze_supplier

    settings = get_settings()
    result = analyze_supplier(supplier_id=supplier_id, source_dir=settings.source_data_dir)
    report = result.get("risk_report") or build_risk_report(result)
    markdown = render_markdown(report)
    settings.report_output_dir.mkdir(parents=True, exist_ok=True)
    path = settings.report_output_dir / f"risk_report_{report['supplier_id']}.md"
    path.write_text(markdown, encoding="utf-8")
    _print({"report_path": str(path), "risk_level": report["risk_grade"]["risk_level"]})


def command_serve() -> None:
    """启动 FastAPI 联调服务（POST /agent/analyze）。"""
    import uvicorn

    uvicorn.run("app.api.server:app", host="0.0.0.0", port=8000, reload=False)


def command_export_contracts() -> None:
    from app.schemas.export import export_contract_schemas

    _print({"schema_version": "C-DRAFT-V0.3", "files": [str(path) for path in export_contract_schemas()]})


def main() -> None:
    parser = argparse.ArgumentParser(description="Jiujiang multi-agent risk analysis")
    parser.add_argument(
        "command",
        choices=(
            "demo",
            "rag-demo",
            "analyze",
            "batch",
            "export-report",
            "serve",
            "export-contracts",
            "real-data-demo",
            "real-data-demo-live",
        ),
        nargs="?",
        default="demo",
    )
    parser.add_argument("--supplier", default="S‑NOR001", help="analyze / export-report 指定的供应商 ID")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    {
        "analyze": lambda: command_analyze(args.supplier),
        "export-report": lambda: command_export_report(args.supplier),
        "demo": command_demo,
        "rag-demo": command_rag_demo,
        "batch": command_batch,
        "serve": command_serve,
        "export-contracts": command_export_contracts,
        "real-data-demo": command_real_data_demo,
        "real-data-demo-live": command_real_data_demo_live,
    }[args.command]()


if __name__ == "__main__":
    main()
