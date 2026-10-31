from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    task: str = Field(..., description="要执行的任务描述")
    github_owner: str = Field(..., description="GitHub 用户名")
    github_repo: str = Field(..., description="GitHub 仓库名")
    github_base_branch: str = Field("main", description="基础分支")