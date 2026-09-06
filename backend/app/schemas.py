"""API 请求/响应模型（pydantic，FastAPI 官方配套的数据校验库）。

对应关系：Node/Express 手写 req.body 校验 → pydantic 模型自动校验 + 自动生成接口文档
"""
from typing import Optional

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(..., description="system / user / assistant")
    content: str


class ClarifyRequest(BaseModel):
    history: list[Message] = Field(default_factory=list, description="对话历史")
    user_input: str = Field(..., description="用户本轮输入")
    prev_collected: dict = Field(default_factory=dict, description="之前已收集的需求字段")


class ClarifyResponse(BaseModel):
    collected: dict
    missing: list[str]
    question: Optional[str]
    done: bool


class ItineraryRequest(BaseModel):
    collected: dict = Field(..., description="M1 澄清完成后的需求字段")
