"""策略 API：内置策略列表（参数 schema 驱动前端表单）、生成策略列表、自然语言生成。"""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.data import database
from app.data.models import Strategy
from app.llm.tasks.codegen import generate_strategy
from app.strategies import StrategyRegistry

router = APIRouter(tags=["strategy"])


@router.get("/strategies")
def list_strategies():
    return StrategyRegistry.list_meta()


@router.get("/strategies/generated")
def list_generated():
    """自然语言生成的策略。"""
    with database.get_session() as session:
        rows = session.query(Strategy).filter(Strategy.kind == "generated").all()
        return [
            {"id": r.id, "name": r.name, "params_json": r.params_json, "code": r.code,
             "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")}
            for r in rows
        ]


class GenerateRequest(BaseModel):
    description: str


@router.post("/strategies/generate")
def generate(req: GenerateRequest):
    """自然语言 → 策略代码（LLM 生成 → 白名单校验 → 样本空跑 → 入库）。"""
    try:
        return asyncio.run(generate_strategy(req.description))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"策略生成失败: {exc}") from exc
