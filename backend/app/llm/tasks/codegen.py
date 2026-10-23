"""自然语言策略代码生成：严格模板 + import 白名单 + ast 校验 + 样本空跑 → 入库。

安全边界：生成的代码仅运行于回测/模拟环境，不接触真实资金；
但仍执行三重防护：语法解析、白名单 import、禁用危险函数。
"""
import ast
import re
from pathlib import Path

import pandas as pd

from app.config import BASE_DIR
from app.data import database
from app.data.models import Strategy
from app.llm.base import get_provider
from app.llm.prompts import CODEGEN_SYSTEM, codegen_user_prompt
from app.strategies.base import Strategy as StrategyBase
from app.utils.logger import get_logger

logger = get_logger("llm.codegen")

GENERATED_DIR = BASE_DIR / "backend" / "app" / "strategies" / "generated"

ALLOWED_IMPORT_ROOTS = {"pandas", "app.strategies.base", "app.ml.indicators"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,30}$")


def _strip_markdown(code: str) -> str:
    """去掉 LLM 输出中可能包裹的 ```python ...``` 标记。"""
    code = code.strip()
    fence = re.match(r"^```(?:python)?\s*\n(.*?)\n?```$", code, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return code


def _validate_code(code: str) -> str:
    """语法 + 白名单 + 危险调用校验。返回策略类名；失败抛 ValueError。"""
    code = _strip_markdown(code)
    tree = ast.parse(code)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"不允许 import 模块: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module not in ALLOWED_IMPORT_ROOTS:
                raise ValueError(f"不允许 import 模块: {node.module}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                raise ValueError(f"禁用函数调用: {node.func.id}")

    classes = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
        and any(isinstance(b, ast.Name) and b.id == "Strategy" for b in n.bases)
    ]
    if not classes:
        raise ValueError("未找到继承 Strategy 的策略类")
    class_name = classes[0].name

    # 类内 name 属性必须是合法策略名
    for node in classes[0].body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "name":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        if not NAME_RE.match(node.value.value):
                            raise ValueError(f"策略 name 不合法: {node.value.value!r}")
                        return node.value.value
    raise ValueError("策略类缺少 name 属性（英文小写+下划线）")


def _synthetic_daily(n: int = 60) -> pd.DataFrame:
    """60 根合成日线（简单正弦波动），供空跑验证。"""
    dates = pd.date_range("2020-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
    close = 10 + pd.Series(range(n)) * 0.01
    return pd.DataFrame({
        "trade_date": dates, "open": close * 0.995, "high": close * 1.02,
        "low": close * 0.98, "close": close, "volume": 1e6,
        "amount": close * 1e6, "pct_change": 0.0,
    })


def _smoke_run(code: str) -> None:
    """用 60 根合成 bar 空跑一遍，确认策略可实例化且引擎可运行。"""
    namespace = {"pd": pd}
    exec(compile(_strip_markdown(code), "<generated_strategy>", "exec"), namespace)  # noqa: S102 白名单已校验

    strategy_cls = None
    for value in namespace.values():
        if isinstance(value, type) and issubclass(value, StrategyBase) and value is not StrategyBase:
            strategy_cls = value
            break
    if strategy_cls is None:
        raise ValueError("代码中未找到可用的策略类")

    from app.engine.backtester import BacktestConfig, BacktestEngine

    BacktestEngine(
        {"600519": _synthetic_daily()}, strategy_cls,
        config=BacktestConfig(initial_capital=100_000),
    ).run()


async def generate_strategy(description: str, provider=None) -> dict:
    """自然语言 → 策略代码 → 校验 → 空跑 → 落盘入库。

    校验/空跑失败时带错误信息重试一次（模型首次生成可能用了不存在的 API）。
    """
    provider = provider or get_provider()
    messages = [
        {"role": "system", "content": CODEGEN_SYSTEM},
        {"role": "user", "content": codegen_user_prompt(description)},
    ]
    code = ""
    for attempt in range(2):
        result = await provider.chat_json(messages, temperature=0.2, schema_hint="codegen")
        code = str(result.get("code", "")).strip()
        if not code:
            raise ValueError("模型未返回代码")
        try:
            name = _validate_code(code)
            _smoke_run(code)
            break
        except ValueError as exc:
            if attempt == 0:
                logger.warning("策略生成首轮校验失败: %s，带错误重试", exc)
                messages.append({"role": "user",
                                 "content": f"你生成的代码运行报错：{exc}\n请修正后重新输出代码。"})
                continue
            raise

    with database.get_session() as session:
        exists = session.query(Strategy).filter(Strategy.name == name).first()
        if exists:
            raise ValueError(f"策略名 {name} 已存在，请换一个名称重新生成")
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        (GENERATED_DIR / f"{name}.py").write_text(_strip_markdown(code), encoding="utf-8")
        row = Strategy(name=name, kind="generated", params_json="{}", code=_strip_markdown(code))
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info("策略生成成功: %s (%d 字符)", name, len(code))
        return {"id": row.id, "name": name, "code": _strip_markdown(code)}


def load_generated_strategy(name: str):
    """动态加载 generated 目录下的策略类（供回测使用）。"""
    import importlib

    module = importlib.import_module(f"app.strategies.generated.{name}")
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, StrategyBase) and value is not StrategyBase:
            if getattr(value, "name", "") == name:
                return value
    raise ValueError(f"生成策略模块中未找到类: {name}")
