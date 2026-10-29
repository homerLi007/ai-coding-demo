from github import Github, Auth

g = Github(auth=Auth.Token(""))
repo = g.get_repo("homerLi007/ai-coding-demo")

# 尝试创建一个测试分支
try:
    ref = repo.create_git_ref(
        ref="refs/heads/test-write-permission",
        sha=repo.get_branch("main").commit.sha
    )
    print("✅ 写权限正常，分支创建成功:", ref.ref)
except Exception as e:
    print("❌ 写操作失败:", e)