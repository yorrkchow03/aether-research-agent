# 最小框架示例

安装工程后，在仓库根目录运行：

```bash
python examples/framework/minimal_agent.py
python scripts/dev/check_framework.py
```

第一个脚本使用真实的 Deep Agents / LangGraph 图、项目的计划中间件、四个已安装的 skill、偏好文件和 checkpoint。模型回复与文章都是明确标记的合成数据，不需要 key、不联网、不执行终端、不发布报告。

它故意先请求读取材料，验证计划门禁会拦截；然后读取 skill、保存计划、成功读取材料、更新完成状态，并检查保存的 checkpoint。`DemoModel` 只用于验证接线，不代表豆包的回答质量。

生产环境仍使用 `src/research_agent/runtime.py`：豆包 API、本机终端/SDK、SQLite checkpoint、来源库和发布审核都在那里组装。教学示例用临时目录和内存 checkpoint，不是另一个需要维护的产品 harness，也不能用它的单个演示工具替换生产工具集。

完整接手顺序、代码入口和扩展方法见 [团队交接指南](../../docs/development/TEAM_HANDOFF.md)。
