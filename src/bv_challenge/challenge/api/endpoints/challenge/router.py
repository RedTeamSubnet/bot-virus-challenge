# -*- coding: utf-8 -*-

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from api.core.responses import BaseResponse
from api.endpoints.challenge.schemas import (
    MinerInput,
    MinerOutput,
    EvalPayload,
    RandomValRequest,
)
from api.endpoints.challenge import service
from api.logger import logger

router = APIRouter(tags=["Challenge"])


@router.get(
    "/task",
    summary="Get task",
    description="This endpoint returns the webpage URL for the challenge.",
    response_class=JSONResponse,
    response_model=MinerInput,
)
def get_task(request: Request):

    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Getting task...")

    _miner_input: MinerInput
    try:
        _miner_input = service.get_task()

        logger.success(f"[{_request_id}] - Successfully got the task.")
    except Exception as err:
        if isinstance(err, HTTPException):
            raise

        logger.error(
            f"[{_request_id}] - Failed to get task!",
        )
        raise

    return _miner_input


@router.post(
    "/score",
    summary="Score",
    description="This endpoint score miner output.",
    response_class=JSONResponse,
    responses={400: {}, 422: {}},
)
def post_score(
    request: Request,
    miner_input: MinerInput,
    miner_output: MinerOutput,
):

    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Evaluating the miner output...")

    try:
        _score = service.score(miner_output=miner_output)
    except HTTPException:
        # Already a well-formed HTTP error (e.g. TOO_MANY_REQUESTS) -- let it
        # propagate so the client gets the real status, never a 200/null.
        logger.error(f"[{_request_id}] - Failed to evaluate the miner output!")
        raise
    except Exception as err:
        logger.error(
            f"[{_request_id}] - Unexpected error evaluating the miner output: {err}"
        )
        raise HTTPException(
            status_code=500, detail="Failed to evaluate the miner output."
        )

    logger.success(f"[{_request_id}] - Successfully scored the miner output: {_score}")
    return _score


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


@router.get(
    "/_web",
    summary="Serves the webpage",
    description="This endpoint serves the webpage for the challenge.",
    response_class=HTMLResponse,
    responses={429: {}},
)
def _get_web(request: Request):

    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Getting webpage...")

    _html_response: HTMLResponse
    try:
        _html_response = service.get_web(request=request)

        logger.success(f"[{_request_id}] - Successfully got the webpage.")
    except Exception as err:
        if isinstance(err, HTTPException):
            raise

        logger.error(
            f"[{_request_id}] - Failed to get the webpage!",
        )
        raise

    return _html_response


@router.post(
    "/_random_val",
    summary="Random value",
    responses={401: {}, 422: {}, 429: {}},
)
def post_random_val(request: Request, payload: RandomValRequest):
    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Checking random val...")

    random_val = payload.random_val.strip()
    nonce_val: str
    try:
        nonce_val = service.get_random_val(nonce=random_val)
        logger.success(f"[{_request_id}] - Successfully checked the random val.")
    except Exception as err:
        if isinstance(err, HTTPException):
            raise
        logger.error(f"[{_request_id}] - Failed to check the random val!")
        raise

    _response = {"nonce_val": nonce_val}
    return _response


@router.post(
    "/_eval",
    summary="Evaluate",
    description="This endpoint evaluate.",
    responses={422: {}, 429: {}},
)
def _post_eval_bot(
    request: Request,
    payload: EvalPayload,
):
    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Evaluating the bot...")

    try:
        # Extract the data from the nested structure
        data = payload.error.data
        service.eval_bot(
            data=data,
            trusted_request={
                "userAgent": request.headers.get("user-agent"),
                "acceptLanguage": request.headers.get("accept-language"),
                "secChUa": request.headers.get("sec-ch-ua"),
            },
        )

        logger.success(f"[{_request_id}] - Successfully evaluated the bot.")
    except Exception as err:
        if isinstance(err, HTTPException):
            raise

        logger.error(
            f"[{_request_id}] - Failed to evaluate the bot!",
        )
        raise

    _response = BaseResponse(request=request, message="Successfully evaluated the bot.")
    return _response


__all__ = ["router"]
