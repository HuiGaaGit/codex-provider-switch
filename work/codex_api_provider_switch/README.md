# Codex Provider Switch v1.3.12

源码入口：`codex_api_provider_switch.py`

## 下载与配置

- [下载 1.3.12 安装包（推荐）](https://github.com/HuiGaaGit/codex-provider-switch/releases/download/v1.3.12/Codex-Provider-Switch-Setup-1.3.12.exe)
- [下载 1.3.12 便携版](https://github.com/HuiGaaGit/codex-provider-switch/releases/download/v1.3.12/Codex-Provider-Switch-1.3.12.exe)
- [发布页面](https://github.com/HuiGaaGit/codex-provider-switch/releases/tag/v1.3.12) · [交付说明与 SHA-256](../../docs/releases/v1.3.12.md)

DeepSeek 已内置官方 API 地址 `https://api.deepseek.com`。在“供应商配置”保存自己的 DeepSeek Key 后，到总览“模型 API”选择 DeepSeek 并点击切换；只切换下拉框不会改变当前 Codex 路由。GLM / DeepSeek 的 Key、模型目录与设置独立保存。

## 1.3.12 变更

- 接入 DeepSeek 官方 `GET https://api.deepseek.com/user/balance`，使用自身 Bearer Key，显示币种、可用总余额、赠送/充值余额及查询时间。
- 总览增加“查余额”，供应商配置增加“仅查询余额”。旧官方 DeepSeek 档案无需重新部署，也不要求已切换或已选模型。自定义 API 地址必须显式填写兼容余额接口。
- 金额使用 Decimal、千分位和两位小数；不同币种不合并，不生成百分比或消费估算。零余额、空数据、错误响应、认证失败与限流分别处理，不回显 Key。
- 移除监控页面、导航、Token 仪表盘及总览统计，不再自动读取请求或扫描 Token；保留托盘驻留、手动链路测试、GLM 与 API1/API2 额度查询，原日志和设置不删除。
- 查询余额不改 config/auth、不重启、不发起模型请求；配置刷新保留查询结果，Key/地址变化后丢弃旧请求结果。
- 116 项测试、源码编译、Qt/托盘与 Tk 向导冒烟及默认/小窗口余额截图检查通过。安装测试采用独立 AppId、程序名和临时目录，不关闭用户已运行的旧程序。
- 本机无已保存的官方 DeepSeek Key，尚未实测真实账户余额或真实推理。旧版更新记录中的“监控”描述不再适用于 1.3.12。

## 开发验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q codex_api_provider_switch.py provider_switch tests
python codex_api_provider_switch.py --smoke-test
python codex_api_provider_switch.py --tray-smoke-test
```

## 打包

```powershell
python -m PyInstaller --noconfirm --distpath ..\..\outputs\codex_api_provider_switch "Codex Provider Switch-1.3.12.spec"
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp "installer\Codex Provider Switch-1.3.12.iss"
.\installer\verify_install.ps1
```

安装包内置图标、许可、重启助手、GLM 和 DeepSeek 模型目录；分别注入 `models-glm.json`、`models-deepseek.json`。便携版为 `outputs/codex_api_provider_switch/Codex Provider Switch-1.3.12.exe`，安装包为同目录 `Codex Provider Switch-Setup-1.3.12.exe`。运行版本来自 `provider_switch/constants.py`，文件版本来自 `assets/version_info-1.3.12.txt`。保留全部旧版产物。

## 1.3.11 变更

- 将总览固定 GLM 位置改为可选择 GLM / DeepSeek 的“模型 API”卡片。两份档案、加密 Key 和模型目录独立保存；仅选择不会修改 Codex 当前路由。
- DeepSeek 按官方 Codex 文档使用原生 Responses，默认 `deepseek-flash`（text/image），另包含 `deepseek-v4-pro`（text）。独立模型目录支持编辑保存，避免混入 GLM/GPT 模型。
- DeepSeek 默认推理 high，停用 Fast mode 与 AP1 图像头；进入前保存登录偏好、搜索、原始推理显示及桌面推理档位的非敏感快照，离开时恢复。保留官方登录文件。
- 首次部署、默认配置预览、Qt/Tk 和真实请求监控支持 DeepSeek。不会把 DeepSeek Key 复制给 API1，也不请求其不存在的中转额度接口。
- 隔离回归测试、Qt/托盘冒烟，以及 `scripts/verify_deepseek_local.py` 调用真实 Codex `features list` 的离线配置加载检查。未使用真实 DeepSeek Key，未验证真实推理请求。
- 文档与模型能力参考：https://api-docs.deepseek.com/quick_start/agent_integrations/codex/ 、https://api-docs.deepseek.com/zh-cn/api/list-models/ （2026-10-21）。
- 最终验证：104 项测试通过；Qt/托盘与 Tk 向导冒烟通过；真实 Codex 离线加载 DeepSeek/GLM/API1 配置通过；安装、安装版启动/托盘及卸载检查全部退出 0。
- SHA-256：便携版 `49233C064F80B040105B7AD16818A76938FCE2743862DB5BA75400E365B81256`；安装包 `E1B833FAC11D02D62E2B9DB76E852374853CE9C6DF8B5B20B1DABBB44B71185A`。

离线配置加载与界面预览（全部数据位于临时目录）：

```powershell
python scripts/verify_deepseek_local.py --codex "C:/完整路径/codex.exe" --preview-dir "../../outputs/codex_api_provider_switch/audit-1.3.11"
```

## 1.3.10 变更

- 各供应商“恢复当前默认”时移除 `[features.multi_agent_v2]` 自定义提示段，使用 Codex 内置子智能体提示。普通供应商切换继续保留人工配置。
- 根据字段作用判断，此改动不影响 Codex 启动、模型选择和供应商认证；按用户要求未执行运行测试。
- 1.3.10 已完成便携版与安装包构建。SHA-256：便携版 `2CA86FA54B3329FBAA042F670083C36B7C8723488C03A7F68E2FF1F1B13D3CA9`；安装包 `2256CF0624E2A780612D1589D48378A7CE0A3D0024484EE7886987B338FEA541`。

## 1.3.9 变更

- 修复监控、本地刷新和登录检查并发结束后部署页按钮仍保持禁用的问题。
- 明确区分“ChatGPT 官方登录态已检测”和“当前正在使用 OpenAI 官方直连”；检测到账号登录但当前路由是 API1、API2 或 GLM 时，仍可直接点击“登录并切换直连”。
- 1.3.9 产物 SHA-256：便携版 `58872B8C7561AF1BFF080DF1D19E721FAD68B1C6C5D800011430FAE115E788F4`；安装包 `C822D3B83E971C82C9E6D576E984D2A212301D92C85ECA7931EAFE2907CF033D`。

1.3.1 修复安装器关闭常驻进程：非静默安装仍会询问是否关闭程序；确认后优先通过本地 IPC 让托盘进程干净退出，旧版本或无响应进程只会按文件过滤强制关闭 `Codex Provider Switch.exe`，不会结束 Codex 或其他应用。
1.3.2 增强 AP1 `aqyimin.chat` 兼容性：切换 GLM/API2/OpenAI 时隔离 AP1 专用配置，切回时恢复服务级别、模型设置、图像开关和白名单图像头；快照不保存任何 API key。
1.3.3 修复 AP1 首次导入时误写 `requires_openai_auth = true` 的问题，并在启动时安全迁移旧配置；AP1 始终使用自己的 bearer/API Key。Codex Desktop 的 GPT Image 2 本地生成命令是独立的 OpenAI Platform API 路径，需要单独提供 `OPENAI_API_KEY`，不会读取或代用 AP1/GLM 的 Key。
1.3.4 将 API1 的 AP1 图像扩展兼容策略严格绑定到 `aqyimin.chat` 主机；API1 改成其他 URL 后完全按 API2 的通用中转策略处理，并清理残留的 AP1 专用字段。监控页移除供应商可用性时间线/条形区域，保留实际请求健康摘要、平均响应时间和 Token 仪表盘。
1.3.5 修复配置预览及调整保存 TOML 时对 Windows 路径和常见未加引号字符串的兼容性；自动修复可安全识别的格式，无法修复时显示脱敏的行列定位，不会把 bearer/API Key 写入错误提示。
1.3.6 为 API1/API2 默认写入 Fast mode；切换 GLM 时清理 `service_tier` 与 `[features].fast_mode`，保留其它功能开关。aqyimin.chat 的显式 `priority` tier 继续保留。
1.3.8 修复官方直连操作：新增“登录并切换直连”，一次完成官方账号登录、OpenAI 路由启用和第三方 API 当前连接断开；总览页在官方直连时提供“断开所有 API”入口，并按当前配置动态清理旧版本或手工添加的 provider 标签。1.3.7 在“配置预览及调整”增加“恢复当前默认”：按当前实际路由分别生成普通 API、aqyimin AP1、GLM 或 OpenAI 直连模板，先载入编辑器预览，确认后再保存；不覆盖无关配置和本地加密 Key。
1.3.8 修复官方直连操作：新增“登录并切换直连”，一次完成官方账号登录、官方路由启用和所有第三方 API 当前连接断开；总览页新增断开入口，动态清理旧版本或手工添加的 provider 标签，并兼容 Windows npm shim 的 Codex CLI。产物 SHA-256：便携版 `C247576A0C8793E89164653BB1D5C5E378A1DCBA55633FD2B5F8BD1CD92AA7CB`；安装包 `5589783A294F4EB1FF21FCD31FD1A02A82D1CC9B12EE976B111981ECE4771433`。1.3.1 产物 SHA-256：便携版 `0674EBB245283ED2DD634DA57572C241237826F94664837B68E8BEFD9BDCAABA`；安装包 `4BCF1281A7240B67A631330CA460F4F6A9777B56D69821A71A0EE52C5BCD1B4C`。1.3.2 产物 SHA-256：便携版 `A95C6A2014836ED69D5385ADB3C7A29E855EF43F7BA765CC8F9DF74B7A30EA90`；安装包 `64FA18D555F46C2710C5BA1EF458265265A077D6FB983E48D922EE5EC9147932`。1.3.3 产物 SHA-256：便携版 `E08DCCAB83F6ACFDD545A94BD51FA8A18805AC66F09263856A3F19B65DFB632C`；安装包 `19146F465EB01E6286C0D873FBA96F1428E3DD8CD69B629FB7A2C06895083325`。1.3.4 产物 SHA-256：便携版 `C27678B87C5BFEB1E855C38246E74D8A84323A832A0992A4D67259D94F1C3B9F`；安装包 `8D574E678B99482DC44E0D45C1015B302ED19C81FC2ECE43416536782E2D3112`。1.3.5 产物 SHA-256：便携版 `56CF7CD4308F506F732877F7BD3ABB9040B7C9F8970E9671D8D3E136CC3C2D9A`；安装包 `53BB346516B75A6071D73EE8CDC14D59B41430D9FBBB389D02D49C9479401256`。1.3.6 产物 SHA-256：便携版 `916C1C766C0CA6C3FC5EC5C88885F20004EEF127CC93949A3E38D575F8C47414`；安装包 `97E1BB6B1905A7715747E53F2BB5BB9CF720593FECE6BB64A72EAB896A2D753E`。1.3.7 产物 SHA-256：便携版 `06E9CC0EF36C39599A02B3D930E65D88C74EAE1C6702CBA182043710A816224E`；安装包 `82ACBE1619EAE5F333A57200AD645BE6666C06607B1C7735BF4B0B7735949687`。

额度查询支持在供应商未使用时单独执行。点击卡片上的“查额度”只调用已保存的额度接口，不切换当前供应商，也不执行模型链路探测；只要有额度地址（或 API 地址）和 Key，即使还没有默认模型也可以查询，API1/API2 会查询配置的 `/v1/usage?days=30`。对于 aqyimin.chat，切回时会恢复 GPT 模型、图像能力相关的原模型目录和推理配置；切换 GLM 时使用独立的 `models-glm.json`。API1 / API2 / GLM 互相切换直接执行，不弹出共存选择。

完整功能、部署流程、安全边界和额度口径见项目根目录 `README.md`。

## API1 aqyimin.chat 兼容项

当 API1 的地址主机是 `aqyimin.chat`（包括 `www` 或其他子域名）时，切换器只识别并保留该供应商需要的行为配置：`responses` 协议、`service_tier`、GPT 模型/推理设置、`[features] image_generation`，以及固定的 `x-openai-actor-authorization` 图像扩展头。缺少历史快照时只补齐缺失的 `image_generation=true` 和白名单头，不覆盖明确的人工设置。切到 GLM、API2 或 OpenAI 直连时会移除 AP1 专用的服务级别、图像开关和请求头，避免污染其他链路；切回 AP1 时从本机 `provider-switch-backups/image-capability.json` 恢复这些非敏感项。API1 改成其他主机后，不再启用 AP1 兼容分支，而是与 API2 共用通用中转策略；即使配置里残留旧 AP1 标记，也会在下一次保存/切换时清理。

快照不会保存 bearer/API key、`env_key`、插件/MCP 或浏览器运行时配置；若原配置已有自定义模型目录，只会按原有逻辑记录该目录路径以便恢复。API key 始终从本地加密凭据库写入当前 provider 表；因此可以把用户的 AP1 配置作为参考，不需要把原始 `config.toml` 纳入安装包或仓库。

1.3.12 已移除监控面板、Token 仪表盘与自动扫描，仅保留手动余额/额度查询和链路测试；历史日志不删除。

## 图像调用边界

AP1 配置中的 `features.image_generation = true` 与 `x-openai-actor-authorization = "local-image-extension"` 只用于 aqyimin.chat 对 Codex 请求的图像扩展/图像输入能力。桌面端通过 `gpt-image` 技能执行的 GPT Image 2 生成命令使用 OpenAI Platform Images/Responses API，按官方文档读取环境变量 `OPENAI_API_KEY`；它不会从 Codex 的 `experimental_bearer_token` 或 `auth.json` 自动取值。若未配置该独立凭据，命令会明确提示无法调用，供应商切换器不会复制、打印或改写任何密钥。







