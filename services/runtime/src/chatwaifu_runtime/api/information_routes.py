"""Authenticated owner-only information proposals. Provider keys stay in Runtime."""

from fastapi import APIRouter, HTTPException, Request, Response

from chatwaifu_runtime.information.contracts import (
    AnalyzeInformationRequest,
    AnalyzeInformationResponse,
)
from chatwaifu_runtime.information.service import (
    InformationUnavailable,
    InvalidInformationOutput,
    PersonalInformationService,
)
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError

router = APIRouter(prefix="/v1/personal-information", tags=["personal-information"])


def _service(request: Request) -> PersonalInformationService:
    return request.app.state.container.information


@router.get("/status")
async def information_status(request: Request, response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return _service(request).status()


@router.post("/analyze", response_model=AnalyzeInformationResponse)
async def analyze_information(
    request: Request, response: Response, body: AnalyzeInformationRequest
) -> AnalyzeInformationResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).analyze(body)
    except AssistantAccessError:
        raise HTTPException(403, "owner_session_required") from None
    except InformationUnavailable as error:
        raise HTTPException(503, str(error)) from None
    except InvalidInformationOutput:
        raise HTTPException(502, "information_model_output_invalid") from None
