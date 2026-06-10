from pydantic import BaseModel, ConfigDict


class JiraDefectCreate(BaseModel):
    test_result_id: int
    jira_url: str
    jira_username: str
    jira_api_token: str
    project_key: str
    summary: str
    description: str


class GitHubDefectCreate(BaseModel):
    test_result_id: int
    repo_owner: str
    repo_name: str
    token: str
    title: str
    body: str


class GitLabDefectCreate(BaseModel):
    test_result_id: int
    gitlab_url: str
    project_id: int
    token: str
    title: str
    description: str


class DefectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tracker: str
    key: str
    url: str
    summary: str
