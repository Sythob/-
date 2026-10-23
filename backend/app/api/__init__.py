"""API 路由聚合（前缀 /api）。"""
from fastapi import APIRouter

from app.api import ai, backtest, market, ml, paper, strategy, system

api_router = APIRouter(prefix="/api")
api_router.include_router(market.router)
api_router.include_router(backtest.router)
api_router.include_router(strategy.router)
api_router.include_router(ml.router)
api_router.include_router(ai.router)
api_router.include_router(paper.router)
api_router.include_router(system.router)
