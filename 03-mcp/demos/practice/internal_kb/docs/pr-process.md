# PR 流程

我们团队的 PR 标准流程：

1. 从 main 拉新分支：`git checkout -b feat/xxx`
2. 写代码 + 自测
3. 提 PR，标题用 conventional commit 格式
4. 至少一个 reviewer 批准 + CI 全绿才 merge
5. squash merge 后删分支
