from fastapi import APIRouter

from schemas.auth import (
    LoginRequest,
    LoginResponse,
    UserResponse
)


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


@router.post("/login", response_model=LoginResponse)
def login(data: LoginRequest):

    # Temporary implementation
    # Real authentication will be added later.

    return LoginResponse(
        access_token="fake-token-for-now",
        token_type="bearer",
        user=UserResponse(
            id=1,
            name="Test Student",
            role="student"
        )
    )