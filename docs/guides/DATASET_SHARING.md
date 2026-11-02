# 小组共享数据集

源码与数据分开保存。源码在公开仓库，数据在同一仓库的
[Half-year research dataset v1 Release](https://github.com/yorrkchow03/aether-research-agent/releases/tag/data-halfyear-v1)。
直接分享仓库和 Release 链接即可查看、下载，不需要邀请或授予访问权限。

## 包内有什么

- 固定窗口：`[2026-04-26 00:00, 2026-10-26 00:00) UTC`。
- ETH/BTC 行情与派生指标、合约/资金流/链上/DeFi/宏观的已取得记录。
- 新闻、论坛及外部报告原文版本、原始响应、来源与实际取得时间。
- SQLite/FTS 查询库、覆盖矩阵、来源/发现清单、缺口与文件哈希。

共享版从已冻结的半年库独立生成。保留公共核心记录的 ID、值和原始字节，
移除作者的旧回答、个人研究报告、用户配置及未被公共数据引用的旧导入对象。
因此它不包含作者的聊天、持仓、账户凭证或完整运行环境，也不复制旧个人记忆。
已有来源缺口保持缺失，`core_complete=false`；不能描述为全指标完整库。
发布方材料保留各自的来源与条款，用于小组研究。

## 下载与恢复

1. 在仓库 Release 页面下载 `aether-halfyear-data-v1.zip` 和 `SHA256SUMS`。
2. 按根目录 README 安装 Python 依赖，进入源码仓库根目录。
3. 假设两个下载文件都在 `~/Downloads/`，执行：

```sh
python scripts/data/restore_shared_library.py \
  ~/Downloads/aether-halfyear-data-v1.zip \
  --checksums ~/Downloads/SHA256SUMS
```

恢复器核对压缩包 SHA-256、所有文件哈希与 SQLite 完整性。
它拒绝覆盖已有资料库，不执行压缩包中的代码，不访问网络。
默认恢复到 `data/library-halfyear-20260426-20261026/`。

4. 在自己本机 `.env` 中设置：

```dotenv
AETHER_LIBRARY_PATH=data/library-halfyear-20260426-20261026
```

5. 离线检查数据：

```sh
python -m research_data.library coverage --root data/library-halfyear-20260426-20261026
python -m research_data.library search --root data/library-halfyear-20260426-20261026 --query Ethereum --limit 3
```

仅查询已下载资料不需要模型凭证。启动 agent 研究仍使用自己的本机配置。
不要把恢复后的 `data/` 再提交进 Git，也不要让多个人通过网盘同步写同一个 SQLite。

## 包内文件

`SHARE_MANIFEST.json` 列出共享文件哈希及时间范围；`PRIVACY_EXPORT.json`
说明从原快照排除了什么；`COVERAGE.json`、`SOURCES.json`、`DISCOVERY.json`
及 `GAPS.json` 保留覆盖和来源。原始冻结库未被更改。

## 分享仓库给组员

直接转发以下两个链接：

- [源码仓库](https://github.com/yorrkchow03/aether-research-agent)：点击 **Code → Download ZIP**，或用 Git 克隆。
- [半年数据包](https://github.com/yorrkchow03/aether-research-agent/releases/tag/data-halfyear-v1)：下载 ZIP 和 `SHA256SUMS`，按上面的步骤恢复。

仓库公开后，所有人都能查看和下载源码、数据 Release 及历史 CI 记录。
公开访问不授予直接推送代码的权限；若组员需要直接推送修改，再单独添加协作者。
无需发送 GitHub 登录密码或个人模型凭证。后续数据更新使用新 Release，
保留旧版用于复现实验。
