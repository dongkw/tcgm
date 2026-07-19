"""HTTP routes for the local dashboard."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import commands, services
from .settings import DashboardSettings
from .view_models import dash, datetime_text, money, pct, short_text, status_class


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.filters["money"] = money
templates.env.filters["pct"] = pct
templates.env.filters["dash"] = dash
templates.env.filters["datetime"] = datetime_text
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
        separator = "&" if "?" in path else "?"
        path = f"{path}{separator}message={quote(message)}"
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


@router.get("/positions/{account_id}/{symbol}", response_class=HTMLResponse)
def position_detail_page(request: Request, account_id: str, symbol: str) -> HTMLResponse:
    detail = services.position_detail(settings(request), account_id, symbol)
    if detail is None:
        raise HTTPException(status_code=404, detail="position not found")
    return render(request, "position_detail.html", "positions", {"position": detail})


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


@router.get("/decisions/{decision_id}", response_class=HTMLResponse)
def decision_detail_page(request: Request, decision_id: str) -> HTMLResponse:
    detail = services.decision_detail(settings(request), decision_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return render(request, "decision_detail.html", "decisions", {"decision": detail})


@router.get("/strategy-context", response_class=HTMLResponse)
def strategy_context_page(request: Request) -> HTMLResponse:
    symbol = request.query_params.get("symbol") or ""
    try:
        data = services.strategy_context_page_data(settings(request), symbol)
        return render(request, "strategy_context.html", "decisions", data)
    except Exception as exc:
        return redirect("/decisions", error=f"打开策略输入失败：{exc}")


@router.post("/actions/strategy-analysis")
async def action_strategy_analysis(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        symbol = (form.get("symbol") or [""])[0]
        result = commands.generate_strategy_analysis(settings(request), symbol)
        return redirect(f"/decisions/{quote(result['decision_id'])}")
    except Exception as exc:
        return redirect("/decisions", error=f"生成多策略分析失败：{exc}")


@router.post("/actions/strategy-context-save")
async def action_strategy_context_save(request: Request) -> RedirectResponse:
    form = parse_qs(
        (await request.body()).decode("utf-8", errors="replace"),
        keep_blank_values=True,
    )
    symbol = (form.get("symbol") or [""])[0]
    try:
        field_names = (
            "holding_period", "stock_type", "future_eps", "high_risk",
            "core_logic", "catalyst", "medium_term_improvement", "tracking_metric",
            "fundamental_invalidation", "technical_invalidation", "event_invalidation",
            "business_model_stable", "profit_quality_5y", "cashflow_reliable",
            "competition_not_worse", "logic_still_valid", "thesis_fully_realized",
            "would_rebuy_now",
        )
        values = {field: (form.get(field) or [""])[0] for field in field_names}
        commands.save_strategy_context_profile(settings(request), symbol, values)
        if (form.get("next_action") or [""])[0] == "analyze":
            result = commands.generate_strategy_analysis(settings(request), symbol)
            return redirect(f"/decisions/{quote(result['decision_id'])}")
        return redirect(f"/strategy-context?symbol={quote(symbol)}", "策略输入已保存")
    except Exception as exc:
        return redirect(f"/strategy-context?symbol={quote(symbol)}", error=f"保存策略输入失败：{exc}")


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


@router.get("/strategies", response_class=HTMLResponse)
def strategies_page(request: Request) -> HTMLResponse:
    return render(request, "strategies.html", "strategies", services.strategy_catalog())


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request) -> HTMLResponse:
    return render(request, "reports.html", "reports", {"reports": services.reports(settings(request))})


@router.get("/watchlists", response_class=HTMLResponse)
def watchlists_page(request: Request) -> HTMLResponse:
    tab = request.query_params.get("tab") or "all"
    q = request.query_params.get("q") or ""
    page = int(request.query_params.get("page") or 1)
    page_size = int(request.query_params.get("page_size") or 20)
    data = services.watchlists_page_data(settings(request), tab_id=tab, q=q, page=page, page_size=page_size)
    return render(request, "watchlists.html", "watchlists", data)


@router.get("/watchlists/{code}", response_class=HTMLResponse)
def watchlist_detail_page(request: Request, code: str) -> HTMLResponse:
    detail = services.stock_detail(settings(request), code)
    if detail is None:
        raise HTTPException(status_code=404, detail="stock json not found")
    return render(request, "watchlist_detail.html", "watchlists", {"stock": detail})


@router.get("/watchlist-screens", response_class=HTMLResponse)
def watchlist_screens_page(request: Request) -> HTMLResponse:
    return render(
        request,
        "watchlist_screens.html",
        "watchlists",
        {"runs": services.watchlist_screen_runs(settings(request))},
    )


@router.get("/watchlist-screens/{run_id}", response_class=HTMLResponse)
def watchlist_screen_detail_page(request: Request, run_id: str) -> HTMLResponse:
    detail = services.watchlist_screen_detail(settings(request), run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="screen run not found")
    return render(request, "watchlist_screen_detail.html", "watchlists", detail)


@router.get("/post-market-diagnosis", response_class=HTMLResponse)
def post_market_diagnosis_page(request: Request) -> HTMLResponse:
    return render(
        request,
        "post_market_diagnosis.html",
        "post_market_diagnosis",
        services.post_market_diagnosis_page(settings(request)),
    )


@router.get("/post-market-data", response_class=HTMLResponse)
def post_market_data_prep_page(request: Request) -> HTMLResponse:
    return render(
        request,
        "post_market_data_prep.html",
        "post_market_data",
        services.post_market_data_prep_page(settings(request)),
    )


@router.get("/historical-data", response_class=HTMLResponse)
def historical_data_page(request: Request) -> HTMLResponse:
    return render(
        request,
        "historical_data.html",
        "historical_data",
        services.historical_data_page(settings(request)),
    )


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


@router.post("/actions/position-pre-market-check")
def action_position_pre_market_check(request: Request) -> RedirectResponse:
    try:
        result = commands.generate_position_pre_market_check(settings(request))
        return redirect(
            "/workflows",
            f"盘前持仓检查完成：{result.get('position_count')} 只持仓，{result.get('issue_count')} 条提示",
        )
    except Exception as exc:
        return redirect("/workflows", error=f"盘前持仓检查失败：{exc}")


@router.post("/actions/position-check-review")
async def action_position_check_review(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        check_id = (form.get("check_id") or [""])[0]
        status = (form.get("status") or [""])[0]
        note = (form.get("note") or [""])[0] or None
        commands.update_position_check_review(settings(request), check_id, status, note=note)
        return redirect("/workflows")
    except Exception as exc:
        return redirect("/workflows", error=f"更新盘前持仓处理状态失败：{exc}")


@router.post("/actions/position-check-decision")
async def action_position_check_decision(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        check_id = (form.get("check_id") or [""])[0]
        result = commands.generate_decision_from_position_check(settings(request), check_id)
        return redirect("/workflows", f"持仓决策已生成：{result.get('decision_id')}")
    except Exception as exc:
        return redirect("/workflows", error=f"生成持仓决策失败：{exc}")


@router.post("/actions/watchlist-refresh")
def action_watchlist_refresh(request: Request) -> RedirectResponse:
    try:
        return redirect("/watchlists", error="请勾选股票后使用刷新选中")
    except Exception as exc:
        return redirect("/watchlists", error=f"自选股刷新失败：{exc}")


@router.post("/actions/refresh-position-market")
async def action_refresh_position_market(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        return_to = (form.get("return_to") or ["/positions"])[0]
        if return_to not in {"/", "/accounts", "/positions"}:
            return_to = "/positions"
        result = commands.refresh_position_market_data(settings(request))
        message = (
            "持仓行情刷新完成："
            f"{result.get('ok_count')} 成功，"
            f"{result.get('failed_count')} 失败，"
            f"同步持仓 {((result.get('valuation_sync') or {}).get('positions') or 0)} 条，"
            f"账户 {((result.get('valuation_sync') or {}).get('accounts') or 0)} 个"
        )
        return redirect(return_to, message)
    except Exception as exc:
        return redirect("/positions", error=f"持仓行情刷新失败：{exc}")


@router.post("/actions/watchlist-refresh-selected")
async def action_watchlist_refresh_selected(request: Request) -> RedirectResponse:
    try:
        body = (await request.body()).decode("utf-8", errors="replace")
        form = parse_qs(body)
        codes = [str(value).strip() for value in form.get("codes", []) if str(value).strip()]
        tab = (form.get("tab") or ["all"])[0]
        q = (form.get("q") or [""])[0]
        if not codes:
            return redirect(f"/watchlists?tab={quote(tab)}&q={quote(q)}", error="请先勾选要刷新的股票")
        result = commands.refresh_selected_watchlist(settings(request), codes)
        message = (
            "选中股票刷新完成："
            f"{result.get('ok_count')} 成功，"
            f"{result.get('failed_count')} 失败，"
            f"同步持仓 {((result.get('valuation_sync') or {}).get('positions') or 0)} 条，"
            f"账户 {((result.get('valuation_sync') or {}).get('accounts') or 0)} 个"
        )
        return redirect(f"/watchlists?tab={quote(tab)}&q={quote(q)}", message)
    except Exception as exc:
        return redirect("/watchlists", error=f"选中股票刷新失败：{exc}")


@router.post("/actions/watchlist-screen-selected")
async def action_watchlist_screen_selected(request: Request) -> RedirectResponse:
    try:
        body = (await request.body()).decode("utf-8", errors="replace")
        form = parse_qs(body)
        codes = [str(value).strip() for value in form.get("codes", []) if str(value).strip()]
        tab = (form.get("tab") or ["all"])[0]
        q = (form.get("q") or [""])[0]
        if not codes:
            return redirect(f"/watchlists?tab={quote(tab)}&q={quote(q)}", error="请先勾选要筛选的股票")
        result = commands.generate_watchlist_screen(settings(request), tab, codes)
        return redirect(
            f"/watchlist-screens/{quote(result['run_id'])}",
            f"盘前筛选完成：{len(result.get('results') or [])} 只",
        )
    except Exception as exc:
        return redirect("/watchlists", error=f"生成盘前筛选失败：{exc}")


@router.post("/actions/post-market-diagnosis-run")
async def action_post_market_diagnosis_run(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        tab_ids = [str(value).strip() for value in form.get("tab_ids", []) if str(value).strip()]
        include_positions = (form.get("include_positions") or [""])[0] == "1"
        result = commands.run_post_market_diagnosis(settings(request), tab_ids=tab_ids, include_positions=include_positions)
        return redirect(
            "/post-market-diagnosis",
            f"盘后诊股完成：{result.get('total')} 只，可诊断 {result.get('success_count')} 只，缺数据 {result.get('failed_count')} 只，明日关注 {result.get('next_watch_count')} 只",
        )
    except Exception as exc:
        return redirect("/post-market-diagnosis", error=f"盘后诊股失败：{exc}")


@router.post("/actions/post-market-data-prep-run")
async def action_post_market_data_prep_run(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        tab_ids = [str(value).strip() for value in form.get("tab_ids", []) if str(value).strip()]
        include_positions = (form.get("include_positions") or [""])[0] == "1"
        result = commands.run_post_market_data_prep(settings(request), tab_ids=tab_ids, include_positions=include_positions)
        return redirect(
            "/post-market-data",
            f"盘后数据准备完成：{result.get('total')} 只，成功 {result.get('success_count')} 只，失败 {result.get('failed_count')} 只",
        )
    except Exception as exc:
        return redirect("/post-market-data", error=f"盘后数据准备失败：{exc}")


@router.post("/actions/post-market-snapshot-prepare")
async def action_post_market_snapshot_prepare(request: Request) -> RedirectResponse:
    try:
        result = commands.run_post_market_snapshot_prepare(settings(request))
        sync = result.get("valuation_sync") or {}
        return redirect(
            "/post-market-data",
            f"盘后准备完成：交易日 {result.get('trade_date')}，持仓 {result.get('success_count')} 可用，缺数据 {result.get('failed_count')}，同步持仓 {sync.get('positions') or 0} 条，账户 {sync.get('accounts') or 0} 个",
        )
    except Exception as exc:
        return redirect("/post-market-data", error=f"盘后准备失败：{exc}")


@router.post("/actions/post-market-close-run")
async def action_post_market_close_run(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        limit_text = (form.get("limit") or [""])[0].strip()
        limit = int(limit_text) if limit_text else None
        result = commands.create_post_market_close_workflow(settings(request), limit=limit)
        if not result.get("already_running"):
            thread = threading.Thread(
                target=commands.run_post_market_close_workflow,
                args=(settings(request), result["run_id"]),
                kwargs={"limit": limit},
                daemon=True,
                name=f"post-market-close-{result['run_id']}",
            )
            thread.start()
        prefix = "收盘流程正在运行" if result.get("already_running") else "收盘流程已启动"
        return redirect("/post-market-data", f"{prefix}：{result.get('run_id')}")
    except Exception as exc:
        return redirect("/post-market-data", error=f"启动收盘流程失败：{exc}")


@router.post("/actions/full-market-data-collect")
async def action_full_market_data_collect(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        limit_text = (form.get("limit") or [""])[0].strip()
        limit = int(limit_text) if limit_text else None
        result = commands.create_full_market_data_collection(settings(request), limit=limit)
        if not result.get("already_running"):
            thread = threading.Thread(
                target=commands.run_full_market_data_collection,
                args=(settings(request), result["batch_id"]),
                daemon=True,
                name=f"full-market-data-{result['batch_id']}",
            )
            thread.start()
        prefix = "全市场盘后采集正在运行" if result.get("already_running") else "全市场盘后采集已启动"
        return redirect(
            "/post-market-data",
            f"{prefix}：{result.get('total')} 只，批次 {result.get('batch_id')}",
        )
    except Exception as exc:
        return redirect("/post-market-data", error=f"启动全市场采集失败：{exc}")


@router.post("/actions/next-day-watch-review")
async def action_next_day_watch_review(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        item_id = (form.get("item_id") or [""])[0]
        status = (form.get("status") or [""])[0]
        note = (form.get("note") or [""])[0] or None
        commands.update_next_day_watch_review(settings(request), item_id, status, note=note)
        return redirect("/post-market-diagnosis")
    except Exception as exc:
        return redirect("/post-market-diagnosis", error=f"更新明日关注状态失败：{exc}")


@router.post("/actions/next-day-watch-decision")
async def action_next_day_watch_decision(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        item_id = (form.get("item_id") or [""])[0]
        result = commands.generate_decision_from_next_day_watch(settings(request), item_id)
        return redirect("/post-market-diagnosis", f"明日关注决策已生成：{result.get('decision_id')}")
    except Exception as exc:
        return redirect("/post-market-diagnosis", error=f"生成明日关注决策失败：{exc}")


@router.post("/actions/watchlist-screen-review")
async def action_watchlist_screen_review(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        result_id = (form.get("result_id") or [""])[0]
        status = (form.get("status") or [""])[0]
        note = (form.get("note") or [""])[0] or None
        if status.strip().upper() == "GENERATE_DECISION":
            result = commands.generate_decision_from_screen_result(settings(request), result_id)
        else:
            result = commands.update_screen_result_review(settings(request), result_id, status, note=note)
        return redirect(f"/watchlist-screens/{quote(result['run_id'])}")
    except Exception as exc:
        return redirect("/watchlist-screens", error=f"更新处理状态失败：{exc}")


@router.post("/actions/watchlist-add-tab")
async def action_watchlist_add_tab(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        name = (form.get("name") or [""])[0]
        result = commands.add_watchlist_tab(settings(request), name)
        return redirect(f"/watchlists?tab={quote(result['tab_id'])}", f"已创建 tab：{name}")
    except Exception as exc:
        return redirect("/watchlists", error=f"创建 tab 失败：{exc}")


@router.post("/actions/watchlist-add-stock")
async def action_watchlist_add_stock(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        tab = (form.get("tab") or ["all"])[0]
        symbol = (form.get("symbol") or [""])[0]
        if not symbol.strip():
            return redirect(f"/watchlists?tab={quote(tab)}", error="请输入股票代码")
        commands.add_watchlist_stock(settings(request), tab, symbol)
        return redirect(f"/watchlists?tab={quote(tab)}", f"已加入自选：{symbol}")
    except Exception as exc:
        return redirect("/watchlists", error=f"添加自选失败：{exc}")


@router.post("/actions/watchlist-remove-stock")
async def action_watchlist_remove_stock(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        tab = (form.get("tab") or ["all"])[0]
        symbol = (form.get("symbol") or [""])[0]
        q = (form.get("q") or [""])[0]
        commands.remove_watchlist_stock(settings(request), tab, symbol)
        return redirect(f"/watchlists?tab={quote(tab)}&q={quote(q)}", f"已移出：{symbol}")
    except Exception as exc:
        return redirect("/watchlists", error=f"移出自选失败：{exc}")


@router.post("/actions/watchlist-delete-tab")
async def action_watchlist_delete_tab(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        tab = (form.get("tab") or [""])[0]
        commands.delete_user_watchlist_tab(settings(request), tab)
        return redirect("/watchlists?tab=all", "已删除 tab")
    except Exception as exc:
        return redirect("/watchlists", error=f"删除 tab 失败：{exc}")


@router.post("/actions/watchlist-bootstrap")
def action_watchlist_bootstrap(request: Request) -> RedirectResponse:
    try:
        result = commands.bootstrap_watchlists(settings(request))
        return redirect("/watchlists", f"自选股数据库初始化完成：{result.get('items')} 条，行情 {result.get('quotes')} 条")
    except Exception as exc:
        return redirect("/watchlists", error=f"自选股数据库初始化失败：{exc}")


@router.post("/actions/position-update")
async def action_position_update(request: Request) -> RedirectResponse:
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        account_id = (form.get("account_id") or [""])[0]
        symbol = (form.get("symbol") or [""])[0]
        commands.update_position_plan(
            settings(request),
            account_id,
            symbol,
            buy_logic=(form.get("buy_logic") or [""])[0],
            invalidation_point=(form.get("invalidation_point") or [""])[0],
            stop_loss_price=(form.get("stop_loss_price") or [""])[0],
            target_price=(form.get("target_price") or [""])[0],
            planned_position_pct=(form.get("planned_position_pct") or [""])[0],
            position_note=(form.get("position_note") or [""])[0],
        )
        return redirect(f"/positions/{quote(account_id)}/{quote(symbol)}", "持仓计划已更新")
    except Exception as exc:
        return redirect("/positions", error=f"更新持仓失败：{exc}")


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


@router.get("/api/watchlists")
def api_watchlists(request: Request) -> JSONResponse:
    return JSONResponse(services.watchlists_page_data(settings(request), tab_id="all", q="", page=1, page_size=20))


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
