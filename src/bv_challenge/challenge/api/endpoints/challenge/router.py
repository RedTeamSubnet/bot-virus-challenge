# -*- coding: utf-8 -*-

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse

from api.core.responses import BaseResponse
from api.core.dependencies.auth import auth_api_key
from api.endpoints.challenge import service
from api.endpoints.challenge.schemas import (
    MinerOutput,
    PayloadRequest,
    ScoreResponse,
)
from api.logger import logger

router = APIRouter(tags=["Challenge"])


@router.post(
    "/score",
    summary="Score",
    description="Build + run the miner bot inside DinD against the web page; wait for scorer callback; return final score.",
    response_class=JSONResponse,
    response_model=ScoreResponse,
    responses={400: {}, 422: {}},
    dependencies=[Depends(auth_api_key)],
)
def post_score(request: Request, miner_output: MinerOutput) -> ScoreResponse:
    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Evaluating the miner output...")
    try:
        _response = service.score(miner_output=miner_output)
    except HTTPException:
        logger.error(f"[{_request_id}] - Failed to evaluate the miner output!")
        raise
    except Exception as err:
        logger.error(
            f"[{_request_id}] - Unexpected error evaluating the miner output: {err}"
        )
        raise HTTPException(
            status_code=500, detail="Failed to evaluate the miner output."
        )

    logger.success(
        f"[{_request_id}] - Scored session {_response.session_id}: {_response.score}"
    )
    return _response


@router.get(
    "/result",
    summary="Latest scoring result",
    description="Returns the latest global score feedback result.",
    response_class=JSONResponse,
)
def get_result(request: Request):
    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Getting latest scoring result...")
    return service.get_result()


@router.post(
    "/payload",
    summary="Scorer callback",
    description="Scorer posts the computed score keyed by session_id; unblocks the matching /score call.",
    response_class=JSONResponse,
    responses={401: {}, 404: {}, 422: {}},
    dependencies=[Depends(auth_api_key)],
)
def post_payload(request: Request, payload: PayloadRequest):
    _request_id = request.state.request_id
    logger.info(
        f"[{_request_id}] - Recording payload for session {payload.session_id}..."
    )
    try:
        service.record_payload(payload=payload)
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"[{_request_id}] - Failed to record payload: {err}")
        raise HTTPException(status_code=500, detail="Failed to record payload.")

    return BaseResponse(request=request, message="Payload recorded.")


__all__ = ["router"]
