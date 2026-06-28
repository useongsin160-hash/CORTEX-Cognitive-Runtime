from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="User prompt to process.")
    session_id: str | None = Field(
        default=None,
        description="Optional session identifier for PFC SessionGoalContext binding.",
    )
