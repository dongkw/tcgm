"""HTTP routes for the local dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import commands, services
from .settings import DashboardSettings
from .view_models import dash, money, pct, short_text, status_class


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.filters["money"] = money
templates.env.filters["pct"] = pct
templates.env.filters["dash"] = dash
templates.env.filters["status_class"] = status_class
templates.env.filters["short_text"] = short_text


def settings(request: Request) -> DashboardSettings:
    return request.app.state.settings


def render(
    request: Request,
    template: str,
    active: str,
    data: dict[str, Any] | None = None,
) -> HTMLResponse:
    ctx = services.template_context(
        settings(request),
        active,
        request.query_params.get("message"),
        request.query_params.get("error"),
    )
    ctx["request"] = request
    ctx.update(data or {})
    return templates.TemplateResponse(request, template, ctx)


def redirect(path: str, message: str | None = None, error: str | None = None) -> RedirectResponse:
    if message:
        path = f"{path}?message={quote(message)}"
    if error:
        separator = "&" if "?" in path else "?"
        path = f"{path}{separator}error={quote(error)}"
    return RedirectResponse(path, status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request) -> HTMLResponse:
    data = services.dashboard(settings(request))
    return render(request, "dashboard.html", "dashboard", data)


@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request) -> HTMLResponse:
    return render(request, "accounts.html", "accounts", {"accounts": services.accounts(settings(request))})


@router.get("/positions", response_class=HTMLResponse)
def positions_page(request: Request) -> HTMLResponse:
    return render(request, "positions.html", "positions", {"positions": services.positions(settings(request))})


@router.get("/decisions", response_class=HTMLResponse)
def decisions_page(request: Request) -> HTMLResponse:
    symbol = request.query_params.get("symbol") or None
    action = request.query_params.get("action") or None
    task_type = request.query_params.get("task_type") or None
    items = services.decisions(settings(request), symbol=symbol, action=action, task_type=task_type)
    return render(
        request,
        "decisions.html",
        "decisions",
        {"decisions": items, "filters": {"symbol": symbol or "", "action": action or "", "task_type": task_type or ""}},
    )


@router.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request) -> HTMLResponse:
    s = settings(request)
    return render(
        request,
        "plans.html",
        "plans",
        {
            "risk_checks": services.risk_checks(s),
            "allocation_plans": services.allocation_plans(s),
            "order_intents": services.order_intents(s),
        },
    )


@router.get("/workflows", response_class=HTMLResponse)
def workflows_page(request: Request) -> HTMLResponse:
    return render(request, "workflows.html", "workflows", services.workflows(settings(request)))


@router.get("/replays", response_class=HTMLResponse)
def replays_page(request: Request) -> HTMLResponse:
    return render(request, "replays.html", "replays", {"replays": services.replays(settings(request))})


@router.get("/strategy-iterations", response_class=HTMLResponse)
def strategy_iterations_page(request: Request) -> HTMLResponse:
    return render(
        request,
        "strategy_iterations.html",
        "strategy_iterations",
        {"iterations": services.strategy_iterations(settings(request))},
    )


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request) -> HTMLResponse:
    return render(request, "reports.html", "reports", {"reports": services.reports(settings(request))})


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_detail_page(request: Request, report_id: str) -> HTMLResponse:
    report = services.report_detail(settings(request), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    return render(request, "report_detail.html", "reports", {"report": report})


@router.get("/data-health", response_class=HTMLResponse)
def data_health_page(request: Request) -> HTMLResponse:
    return render(request, "data_health.html", "data_health", services.data_health(settings(request)))


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return render(request, "settings.html", "settings", {"info": services.settings_info(settings(request))})


@router.post("/actions/import-json")
def action_import_json(request: Request) -> RedirectResponse:
    try:
        result = commands.import_json(settings(request))
        return redirect("/data-health", f"导入完成：{result.get('batch_id')}")
    except Exception as exc:
        return redirect("/data-health", error=f"导入失败：{exc}")


@router.post("/actions/validate")
def action_validate(request: Request) -> RedirectResponse:
    try:
        result = commands.validate(settings(request))
        return redirect("/data-health", f"校验完成：{result.get('issue_count')} 个问题")
    except Exception as exc:
        return redirect("/data-health", error=f"校验失败：{exc}")


@router.post("/actions/reconcile")
def action_reconcile(request: Request) -> RedirectResponse:
    try:
        result = commands.reconcile(settings(request))
        status = "通过" if result.get("ok") else "发现问题"
        return redirect("/data-health", f"同步对账{status}：{result.get('issue_count')} 个问题")
    except Exception as exc:
        return redirect("/data-health", error=f"同步对账失败：{exc}")


@router.post("/actions/backup")
def action_backup(request: Request) -> RedirectResponse:
    try:
        result = commands.backup(settings(request))
        return redirect("/data-health", f"备份完成：{result.get('backup_path')}")
    except Exception as exc:
        return redirect("/data-health", error=f"备份失败：{exc}")


@router.get("/api/summary")
def api_summary(request: Request) -> JSONResponse:
    return JSONResponse(services.dashboard(settings(request)))


@router.get("/api/accounts")
def api_accounts(request: Request) -> JSONResponse:
    return JSONResponse({"accounts": services.accounts(settings(request))})


@router.get("/api/positions")
def api_positions(request: Request) -> JSONResponse:
    return JSONResponse({"positions": services.positions(settings(request))})


@router.get("/api/decisions")
def api_decisions(request: Request) -> JSONResponse:
    return JSONResponse({"decisions": services.decisions(settings(request))})


@router.get("/api/reports")
def api_reports(request: Request) -> JSONResponse:
    return JSONResponse({"reports": services.reports(settings(request))})


@router.get("/api/replays")
def api_replays(request: Request) -> JSONResponse:
    return JSONResponse({"replays": services.replays(settings(request))})


@router.get("/api/strategy-iterations")
def api_strategy_iterations(request: Request) -> JSONResponse:
    return JSONResponse({"iterations": services.strategy_iterations(settings(request))})


@router.get("/api/data-health")
def api_data_health(request: Request) -> JSONResponse:
    return JSONResponse(services.data_health(settings(request)))


@router.get("/api/sync-status")
def api_sync_status(request: Request) -> JSONResponse:
    return JSONResponse(services.sync_status(settings(request)))
