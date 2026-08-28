> [!WARNING]
> **请先确认你确实要提交代码贡献。**
>
> 如果你的目的是更新自己 Fork 的代码，请不要创建 Pull Request。
> 请返回自己的仓库主页，使用 `Sync fork` → `Update branch`。
> 如果出现同步冲突，请按照教程选择 `Discard commits`。
>
> Pull Request 仅用于向主仓库提交经过确认的代码、文档、测试或 CI 修改。
> 如果只是修改个人配置、提交运行结果或同步代码，请关闭当前页面。
>
> **Please confirm that you intentionally want to submit a code contribution.**
> If you only want to update your fork, use `Sync fork` → `Update branch` in your own repository.
> Pull Requests are for intentional code, documentation, test, or CI contributions to the upstream repository.

<!--
Do not remove the marker on the contribution confirmation checkbox.
It is used to distinguish intentional contributions from accidental update PRs.
不要删除贡献确认复选框中的标记，自动校验会使用它识别误操作 PR。
-->

## Contribution confirmation | 贡献确认

- [ ] I intentionally want to submit code changes to Guovin/iptv-api for review | 我确认这是一次有意提交给主仓库审核的代码贡献 <!-- pr-contribution-confirmed -->

## Summary | 修改概述

<!-- 简要说明本 PR 修改了什么，以及为什么需要修改。 -->

## Related issue | 关联 Issue

<!-- 例如：Fixes #123、Closes #456；如果没有关联 Issue，请填写 N/A。 -->

## Change type | 变更类型

- [ ] Bug fix | Bug 修复
- [ ] Feature | 功能新增或改进
- [ ] Documentation | 文档
- [ ] Test | 测试
- [ ] Refactor | 重构
- [ ] Performance | 性能优化
- [ ] Build / CI | 构建或 CI
- [ ] Chore | 其他维护性修改
- [ ] Other | 其他：

## Scope | 影响范围

- [ ] Core / API | 核心功能或 API
- [ ] GUI | 软件界面
- [ ] Command line | 命令行
- [ ] Docker / Deployment | Docker 或部署
- [ ] GitHub Actions / Workflow | GitHub 工作流
- [ ] Documentation | 文档
- [ ] Tests | 测试
- [ ] Other | 其他：

## Implementation details | 实现说明

<!-- 说明核心实现方式、重要设计决策，以及可能影响维护的内容。 -->

## Testing | 测试情况

### Automated tests | 自动化测试

- [ ] 已新增或更新相关测试（如适用）
- [ ] 已运行相关测试
- [ ] 相关测试已通过

测试命令：

```text
# 例如：
pipenv run python -m unittest discover -s tests -v
```

### Manual verification | 手动验证

<!-- 如果涉及 GUI、Docker、Workflow 或其他运行行为，请说明验证方式；不适用时填写 N/A。 -->

## User-facing changes | 用户可见变化

<!-- 描述用户能够看到的变化；如果没有，请填写 N/A。 -->

截图或录屏（如适用）：

## Breaking changes | 破坏性变更

- [ ] No breaking changes | 没有破坏性变更
- [ ] Yes, this is a breaking change | 存在破坏性变更

如果存在破坏性变更，请说明：

- 受影响的功能：
- 旧用法：
- 新用法：
- 迁移步骤：

## Checklist | 提交前检查

- [ ] 没有提交个人配置、生成结果、运行日志或敏感信息
- [ ] 已更新必要的文档
- [ ] PR 只包含与本次修改相关的内容
- [ ] 已填写所有适用的部分；不适用的部分已填写 N/A
