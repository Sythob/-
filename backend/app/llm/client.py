"""LLM 客户端通用件：重试判定与 AsyncRetrying 工厂（限速+指数退避）。"""
from openai import APIConnectionError, APIStatusError, APITimeoutError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential


def is_retryable(exc: BaseException) -> bool:
    """429 限流 / 5xx 服务端 / 网络超时 → 重试；其他错误不重试。"""
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return isinstance(exc, (APIConnectionError, APITimeoutError))


def retrying(max_retries: int) -> AsyncRetrying:
    return AsyncRetrying(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        retry=retry_if_exception(is_retryable),
        reraise=True,
    )
