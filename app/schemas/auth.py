from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    name: str
    role: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse