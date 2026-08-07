# -*- coding: utf-8 -*-

from fastapi.responses import JSONResponse
from fastapi import APIRouter, HTTPException, Request
from api.core.constants import ErrorCodeEnum
from api.core.exceptions import BaseHTTPException
from api.logger import logger

from . import service
from .schemas import BuildRequest, RunSimpleBotRequest, RunWebRequest

router = APIRouter(tags=["Challenge"])


@router.get(
    "/health",
    summary="Health check",
    description="Check if the VM runner service is healthy and running.",
    response_class=JSONResponse,
)
def get_health(request: Request):
    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Health check...")

    return {
        "status": "healthy",
        "service": "vm-runner",
        "message": "VM runner is up and running",
    }


@router.post(
    "/build",
    summary="Build miner container image",
    description="Receives bot.py and Dockerfile and builds the miner image.",
    response_class=JSONResponse,
    responses={422: {}, 500: {}},
)
def post_build(request: Request, payload: BuildRequest):
    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Building miner image...")

    try:
        result = service.build_bot_image(
            bot_py=payload.bot_py,
            dockerfile=payload.dockerfile,
            score_job_id=payload.score_job_id,
        )
        logger.success(f"[{_request_id}] - Successfully built miner image.")
        return result
    except HTTPException:
        raise
    except Exception as err:
        logger.exception(f"[{_request_id}] - Failed to build miner image!")
        error_msg = str(err)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "... (truncated)"
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.INTERNAL_SERVER_ERROR,
            message=f"Failed to build miner image: {error_msg}",
        )


@router.post(
    "/run-simple-bot",
    summary="Run miner image against simple bot page",
    description="Runs the already-built miner image against the simple bot page.",
    response_class=JSONResponse,
    responses={422: {}, 500: {}},
)
def post_run_simple_bot(request: Request, payload: RunSimpleBotRequest):
    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Running simple bot phase...")

    try:
        result = service.run_simple_bot(
            score_job_id=payload.score_job_id,
            timeout_sec=payload.timeout_sec,
        )
        logger.success(f"[{_request_id}] - Successfully ran simple bot phase.")
        return result
    except HTTPException:
        raise
    except Exception as err:
        logger.exception(f"[{_request_id}] - Failed to run simple bot phase!")
        error_msg = str(err)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "... (truncated)"
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.INTERNAL_SERVER_ERROR,
            message=f"Failed to run simple bot phase: {error_msg}",
        )


@router.post(
    "/run-web",
    summary="Run miner image against challenge web page",
    description="Runs the already-built miner image against the challenge _web page.",
    response_class=JSONResponse,
    responses={422: {}, 500: {}},
)
def post_run_web(request: Request, payload: RunWebRequest):
    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Running challenge web phase...")

    try:
        result = service.run_web_bot(
            session_count=payload.session_count,
            score_job_id=payload.score_job_id,
        )
        logger.success(f"[{_request_id}] - Successfully ran challenge web phase.")
        return result
    except HTTPException:
        raise
    except Exception as err:
        logger.exception(f"[{_request_id}] - Failed to run challenge web phase!")
        error_msg = str(err)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "... (truncated)"
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.INTERNAL_SERVER_ERROR,
            message=f"Failed to run challenge web phase: {error_msg}",
        )


__all__ = [
    "router",
]
