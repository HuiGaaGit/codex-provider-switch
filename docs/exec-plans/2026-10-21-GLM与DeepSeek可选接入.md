# GLM 与 DeepSeek 可选接入（1.3.11）

## 交付目标

总览保留 OpenAI、API1、API2 和一个可选的模型 API 入口；最后一个入口可选 GLM 或 DeepSeek，两份供应商档案及加密 Key 独立保存。旧 GLM 档案原样迁移。配置预览、模型目录、首次部署、托盘及 Token 归属均支持 DeepSeek。

## 官方依据

- https://api-docs.deepseek.com/quick_start/agent_integrations/codex/
- https://api-docs.deepseek.com/zh-cn/guides/responses_api/
- https://api-docs.deepseek.com/zh-cn/api/list-models/

DeepSeek 使用 https://api.deepseek.com 的 Responses 协议，默认 deepseek-flash（text/image），另含 deepseek-v4-pro（text）；默认推理 high，关闭内置搜索。模型目录按供应商隔离。官方推荐的 API 登录字段只对 DeepSeek 生效，切走时恢复原字段，不删除官方账号凭据。

## 实施顺序

1. 为 DeepSeek 增加独立 profile、可选入口状态、模型目录与旧设置迁移。
2. 接入配置渲染、DeepSeek 临时字段的快照/恢复、默认配置与供应商识别；保持稳定 provider 标签。
3. 修改 Qt/Tk 总览选择器、供应商编辑、部署和配置预览。监控按实际请求归属，未配置供应商不影响切换。
4. 隔离临时 Codex Home 执行往返/凭据隔离/目录/界面回归测试；构建新版本并执行安装验证。

## 边界

- 不改动当前用户运行中的 .codex/config.toml、不执行自动重启测试、不使用或打印真实凭据。
- 不执行官方网页上的一键安装脚本，仅参考其中配置数据。
- 不新增按量消费金额或余额显示。DeepSeek 无套餐额度接口时明确显示未配置。
- 未取得 DeepSeek Key 时仅可验证本地配置与模拟 Responses 请求流程，交付说明明确实测边界。
- 保留 1.3.10 及更早源码/安装产物与未提交改动。

## 进度

- 已查阅官方文档并确认原生 Responses、模型标识和图片能力。
- 已实现独立 DeepSeek 档案/目录、第四卡片选择器、Qt/Tk 部署与编辑器，以及 DeepSeek 配置快照和恢复。
- 104 项单元/集成测试通过，包含 GLM/DeepSeek 往返、官方登录态保留、模型目录和 Key 隔离、旧设置迁移、按切换时间区分 Token 增量及 DeepSeek 请求监控汇总。
- 真实 Codex 在临时 Home 中加载 DeepSeek、GLM、API1 配置成功；补齐了当前客户端要求的模型元数据字段。Qt/托盘、Tk 选择器/目录/向导冒烟通过。
- 界面预览位于 outputs/codex_api_provider_switch/audit-1.3.11/，已修复目录路径导致操作按钮遮挡的问题。
- 已构建 1.3.11 便携版与安装包，文件版本与 UI 版本一致；安装、安装版版本/启动/托盘、卸载验证退出码全部为 0。已检查便携包包含 DeepSeek Flash/Pro 模型目录。
- 本次交付完成。未取得真实 DeepSeek Key，未发送真实推理请求；不将离线验证描述为真实 API 推理实测。
