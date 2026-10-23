"""HTML 绩效报告生成：完全自包含（内联 CSS + 内联 SVG 图表），离线可看。

借鉴 QuantStats 的报告思路：指标总览、净值/回撤曲线、月度收益热力图、
滚动夏普、成交明细。图表用轻量 SVG 生成器，无任何 CDN/JS 依赖。
"""
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import DATA_DIR
from app.engine.metrics import rolling_sharpe

REPORT_DIR = DATA_DIR / "reports"

# 中国习惯：红涨绿跌
UP = "#e03131"
DOWN = "#2f9e44"
BLUE = "#2f6fed"
GRID = "#eef0f4"
TEXT = "#5c6470"


# ---------------------------------------------------------------- SVG 图表

def _svg_curve(values: list[float], dates: list[str], *, width: int = 920,
               height: int = 240, color: str = BLUE, area: bool = False,
               fmt: str = "{:,.2f}") -> str:
    """折线图（可选面积填充）。返回内联 SVG 字符串。"""
    if not values:
        return '<p class="muted">无数据</p>'
    n = len(values)
    vmin, vmax = min(values), max(values)
    pad = (vmax - vmin) * 0.08 or max(abs(vmax) * 0.02, 1.0)
    vmin, vmax = vmin - pad, vmax + pad
    left, right, top, bottom = 46, 14, 14, 24

    def px(i: int) -> float:
        return left + i * (width - left - right) / max(n - 1, 1)

    def py(v: float) -> float:
        return top + (vmax - v) / (vmax - vmin) * (height - top - bottom)

    points = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(values))
    grid_lines = "".join(
        f'<line x1="{left}" y1="{py(v):.1f}" x2="{width - right}" y2="{py(v):.1f}" '
        f'stroke="{GRID}" stroke-width="1"/>'
        for v in (vmin + (vmax - vmin) * q for q in (0.25, 0.5, 0.75))
    )
    area_path = f'<polygon points="{left},{py(vmin)} {points} {px(n - 1):.1f},{py(vmin)}" ' \
                f'fill="{color}" opacity="0.08"/>' if area else ""
    y_labels = "".join(
        f'<text x="{left - 6}" y="{py(v):.1f}" text-anchor="end" font-size="10" fill="{TEXT}">'
        f'{fmt.format(v)}</text>'
        for v in (vmax, vmin + (vmax - vmin) / 2, vmin)
    )
    x_labels = (
        f'<text x="{left}" y="{height - 6}" font-size="10" fill="{TEXT}">{dates[0]}</text>'
        f'<text x="{width - right}" y="{height - 6}" text-anchor="end" font-size="10" '
        f'fill="{TEXT}">{dates[-1]}</text>'
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto">'
        f"{grid_lines}{area_path}"
        f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>'
        f"{y_labels}{x_labels}</svg>"
    )


def _monthly_matrix(dates: list[str], equity: list[float]) -> dict[int, list]:
    """月度收益率矩阵 {年份: [1月..12月 收益率或 None]}。"""
    df = pd.DataFrame({
        "date": pd.to_datetime(dates), "equity": equity,
    }).set_index("date")
    monthly = df.resample("ME").last()["equity"].pct_change()
    matrix: dict[int, list] = {}
    for ts, ret in monthly.items():
        if pd.isna(ret):
            continue
        year = int(ts.year)
        if year not in matrix:
            matrix[year] = [None] * 12
        matrix[year][int(ts.month) - 1] = round(float(ret) * 100, 2)
    return matrix


def _svg_heatmap(matrix: dict[int, list], width: int = 920) -> str:
    """月度收益热力图：行=年，列=月，红正绿负（透明度按强度）。"""
    if not matrix:
        return '<p class="muted">数据不足，无法生成月度热力图</p>'
    years = sorted(matrix)
    cell_w, cell_h = 66, 26
    left = 44
    top = 30
    height = top + len(years) * (cell_h + 4) + 10
    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto">']
    for m in range(12):
        x = left + m * (cell_w + 4)
        parts.append(
            f'<text x="{x + cell_w / 2}" y="16" text-anchor="middle" font-size="10" '
            f'fill="{TEXT}">{m + 1}月</text>'
        )
    for row, year in enumerate(years):
        y = top + row * (cell_h + 4)
        parts.append(f'<text x="{left - 8}" y="{y + cell_h / 2 + 3}" text-anchor="end" '
                     f'font-size="10" fill="{TEXT}">{year}</text>')
        for m in range(12):
            v = matrix[year][m]
            x = left + m * (cell_w + 4)
            if v is None:
                color, text = "#f5f6f8", ""
            else:
                strength = min(abs(v) / 10.0, 1.0)
                color = f"rgba(224,49,49,{0.15 + 0.75 * strength})" if v >= 0 \
                    else f"rgba(47,158,68,{0.15 + 0.75 * strength})"
                text = f"{v:+.1f}"
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" rx="3" '
                f'fill="{color}"/>'
                f'<text x="{x + cell_w / 2}" y="{y + cell_h / 2 + 3}" text-anchor="middle" '
                f'font-size="9" fill="#fff">{text}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- 报告生成

def generate_report_html(run: dict) -> str:
    """run 需含：strategy_name/params/universe/start_date/end_date/capital/
    metrics/equity[{date,equity}]/trades。返回完整 HTML 字符串。"""
    metrics = run.get("metrics", {})
    equity_list = run.get("equity", [])
    dates = [e["date"] for e in equity_list]
    equity = [e["equity"] for e in equity_list]

    # 回撤序列
    peak, drawdowns = -1e18, []
    for v in equity:
        peak = max(peak, v)
        drawdowns.append((v / peak - 1) * 100 if peak else 0.0)

    # 滚动夏普
    rs = metrics.get("rolling_sharpe")
    if not rs or not rs.get("values"):
        eq_series = pd.Series(equity, index=pd.to_datetime(dates), dtype=float)
        rs = rolling_sharpe(eq_series)

    metric_rows = "".join(
        f"<tr><td>{name}</td><td>{value}</td></tr>"
        for name, value in _metric_table(metrics).items()
    )
    trade_rows = "".join(
        f"<tr><td>{t['trade_time']}</td><td>{t['symbol']}</td>"
        f"<td>{'买入' if t['direction'] == 'buy' else '卖出'}</td>"
        f"<td>{t['price']:.2f}</td><td>{t['volume']}</td>"
        f"<td>{t['commission']:.2f}</td>"
        f"<td style='color:{UP if t['pnl'] > 0 else DOWN if t['pnl'] < 0 else TEXT}'>"
        f"{t['pnl']:.2f}</td></tr>"
        for t in run.get("trades", [])[:50]
    )
    params_str = ", ".join(f"{k}={v}" for k, v in (run.get("params") or {}).items()) or "默认"
    universe = "、".join(run.get("universe") or [])
    heatmap = _svg_heatmap(_monthly_matrix(dates, equity))

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>回测报告 #{run.get('id', '')} {run.get('strategy_name', '')}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 24px auto; max-width: 980px;
       color: #30343a; background: #f7f8fa; }}
.card {{ background: #fff; border-radius: 8px; padding: 20px 24px; margin-bottom: 16px;
         box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
h1 {{ font-size: 20px; margin: 0 0 4px; }}
h2 {{ font-size: 15px; border-left: 3px solid {BLUE}; padding-left: 8px; margin: 0 0 12px; }}
.meta {{ color: {TEXT}; font-size: 13px; margin-bottom: 12px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
td, th {{ border-bottom: 1px solid {GRID}; padding: 6px 10px; text-align: left; }}
th {{ background: #fafbfc; }}
.metric-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 24px; }}
.muted {{ color: #a0a6ad; font-size: 12px; }}
.warn {{ color: #b8860b; font-size: 12px; }}
footer {{ text-align: center; color: #a0a6ad; font-size: 12px; margin-top: 20px; }}
</style></head><body>
<div class="card">
  <h1>回测报告 #{run.get('id', '')} · {run.get('strategy_name', '')}</h1>
  <div class="meta">标的：{universe} ｜ 区间：{run.get('start_date', '')} ~ {run.get('end_date', '') or '最新'}
  ｜ 初始资金：{run.get('capital', 0):,.0f} ｜ 参数：{params_str}</div>
  <table class="metric-grid"><tr>{metric_rows}</tr></table>
</div>
<div class="card"><h2>净值曲线</h2>{_svg_curve(equity, dates, color=BLUE, area=True)}</div>
<div class="card"><h2>回撤曲线（%）</h2>{_svg_curve(drawdowns, dates, color=UP, area=True, fmt="{{:,.1f}}")}</div>
<div class="card"><h2>月度收益热力图（%）</h2>{heatmap}</div>
<div class="card"><h2>滚动夏普比率（60 日）</h2>
{_svg_curve(rs.get('values', []), rs.get('dates', []), color="#8a63d2")}</div>
<div class="card"><h2>成交明细（前 50 笔）</h2>
<table><tr><th>时间</th><th>代码</th><th>方向</th><th>价格</th><th>数量</th><th>费用</th><th>盈亏</th></tr>
{trade_rows or '<tr><td colspan="7" class="muted">无成交</td></tr>'}</table></div>
<footer>唯我量化 · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ·
本报告仅供学习研究，不构成投资建议</footer>
</body></html>"""


def _metric_table(metrics: dict) -> dict:
    """指标中文名映射与格式化。"""
    fmt_pct = lambda v: "—" if v is None else f"{v * 100:+.2f}%"  # noqa: E731
    fmt_num = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
    mapping = [
        ("总收益率", fmt_pct(metrics.get("total_return"))),
        ("年化收益率", fmt_pct(metrics.get("annualized_return"))),
        ("最大回撤", fmt_pct(metrics.get("max_drawdown"))),
        ("夏普比率", fmt_num(metrics.get("sharpe_ratio"))),
        ("索提诺比率", fmt_num(metrics.get("sortino_ratio"))),
        ("卡玛比率", fmt_num(metrics.get("calmar_ratio"))),
        ("胜率", fmt_pct(metrics.get("win_rate"))),
        ("盈亏比", fmt_num(metrics.get("profit_loss_ratio"))),
        ("交易次数", str(metrics.get("trade_count", 0))),
        ("往返次数", str(metrics.get("round_trips", 0))),
        ("平均持仓天数", fmt_num(metrics.get("avg_holding_days"))),
        ("换手率", fmt_num(metrics.get("turnover_rate"))),
        ("最大连赢", str(metrics.get("max_consecutive_wins", 0))),
        ("最大连亏", str(metrics.get("max_consecutive_losses", 0))),
        ("期末资产", f"{metrics.get('final_equity', 0):,.0f}"),
    ]
    if "benchmark_return" in metrics:
        mapping.append(("基准（沪深300）", fmt_pct(metrics.get("benchmark_return"))))
        mapping.append(("超额收益", fmt_pct(metrics.get("excess_return"))))
        mapping.append(("Alpha", fmt_pct(metrics.get("alpha"))))
        mapping.append(("Beta", fmt_num(metrics.get("beta"))))
        mapping.append(("信息比率", fmt_num(metrics.get("information_ratio"))))
    return dict(mapping)


def save_report(run_id: int, html: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"run_{run_id}.html"
    path.write_text(html, encoding="utf-8")
    return path
