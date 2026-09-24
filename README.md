# 11CodexAPI供应商开关

Windows 桌面工具，用一个稳定的 Codex provider 标签在 OpenAI 直连、中转 1、中转 2 与 GLM 之间切换，并集中完成历史会话同步、链路健康、额度查询和本机 Token 统计。

当前交付版本：`1.3.6`

## 日常使用

首次启动会打开四步部署向导：

1. 自动定位 `%USERPROFILE%\.codex\config.toml`，也可手动选择其他 Codex Home。
2. 导入原 `model_provider`、模型、供应商地址与旧版中转设置；中转 1 / 2 可只配置其中一个，未配置项会在总览中禁用。
3. 检测 OpenAI 官方登录态，选择是否保留它供后续 OpenAI 直连使用；API1、API2、GLM 始终使用各自独立 API Key。确认稳定 provider 标签、注入 `models.json`，并检测或一键安装 Codex Threadripper。
4. 选择首次启用的供应商，备份并写入配置。

完成部署后默认进入总览，只保留四个一键切换入口和健康、额度、Token 摘要。供应商详情、模型目录、Threadripper、官方登录态与高级设置分别位于独立功能区。关闭主窗口时默认缩入 Windows 系统托盘；健康检查仅在启动、切换完成或手动刷新时检查当前供应商，托盘菜单可恢复窗口、立即刷新或彻底退出。Windows 优先使用 PySide6 液态毛玻璃界面，缺少 Qt 运行库时回退到 Tk 界面。

## 切换与历史兼容

- OpenAI 直连：取消自定义 `model_provider` 默认值，继续使用 Codex 当前 OpenAI/ChatGPT 登录；只有检测到保留的官方登录态时按钮才可用。
- API1 / API2：写入各自的 `base_url`、模型和 `experimental_bearer_token`，名称与 Key 可本地修改；新装默认显示名是 `API1` 和 `API2`。
- API1 / API2 默认写入 Codex Fast mode：`service_tier = "fast"` 与 `[features].fast_mode = true`；如果 API1 使用 `aqyimin.chat` 且原配置已有明确 `priority`，会保留该供应商的显式 tier，同时启用 Fast mode 标记。
- GLM：写入 `responses` 协议、GLM 模型、Key 和当前 Codex Home 下的 `models-glm.json`；切换时会同步未归档会话线程的模型字段，重新打开旧会话也会跟随当前 API 模型。团队项目 ID 使用 `proj_xxxxxxx`，保存时自动纠正粘贴产生的空格分隔。旧版本写入的纯 GLM `models.json` 会自动迁移到独立文件。
- 切换到 GLM 时会清理顶层 `service_tier` 与 `[features].fast_mode`，保留其它功能开关，避免 Fast mode 参数影响 GLM 请求。
- aqyimin.chat GPT 中转：只有 API1 地址的主机属于 `aqyimin.chat`（含 `www` 和子域名）时，切回该供应商才恢复原配置中的 GPT 模型、图像能力相关模型目录和推理配置；切换 GLM 时使用独立的 `models-glm.json`，不再污染 API1 原来的 `models.json`。旧版本留下的歧义 `models.json` 备份不会被恢复到 API1。API1 改成其他供应商 URL 后，完全按 API2 的通用中转策略处理，并清理残留的 AP1 专用字段。
- AP1 认证兼容：aqyimin.chat 始终使用自己的 bearer/API Key，启动时会把旧配置误写的 `requires_openai_auth = true` 修正为 `false`，并只在缺失时补齐 `features.image_generation` 与 `local-image-extension` 头；明确的人工禁用或自定义值会保留。
- 图像调用边界：AP1 的 Codex 图像扩展/图片输入与 GPT Image 2 本地生成命令是两条链路。后者由 OpenAI Platform API 凭据驱动，按官方文档读取独立的 `OPENAI_API_KEY`；切换器不会把 AP1/GLM Key 复制到该环境变量。
- API1 / API2 / GLM 之间切换时直接断开旧路由并写入新配置，不再弹出“保留共存”选择；涉及 OpenAI 直连时仍保留确认提示。
- GLM 切换会清掉旧配置里遗留的 `env_key` 和 `http_headers`（尤其是 `OPENAI_API_KEY`、`x-openai-actor-authorization`），避免认证来源歧义和应用专用请求头破坏 GLM 流式响应；`experimental_bearer_token` 是唯一认证来源。
- 保留官方登录态与 GLM 不冲突：GLM 始终写入 `requires_openai_auth = false` 并使用自己的 bearer token；官方凭据缓存保持不变，切回 OpenAI 时继续使用。
- “保持同一 provider 标签”默认开启。第三方供应商切换时复用首次读取到的 provider key（本机当前为 `cch_gz`），避免新会话按多个标签分裂。
- OpenAI 直连不会删除第三方 provider 注册段，因此已有历史会话仍能解析原 provider。
- “部署与登录”可运行 `codex login status`、启动 `codex login` 或执行 `codex logout`。选择不保留官方登录态后，软件会清除当前 Codex Home 的 `auth.json`，并把中转切换为独立 API Key 模式；重新登录不会改动中转 1 / 2 的地址与 Key。
- 每次写入前在 `.codex\provider-switch-backups\` 创建时间戳备份；写入后重新解析验证，失败时尝试自动回滚。
- 如果 Threadripper 可用，切换后自动同步并校验会话索引。工具页也可随时手动同步。
- 自动重启默认开启。重启助手只结束 Codex 应用进程，不再使用进程树终止；配置始终先保存、验证，再尝试重启，因此从 Codex 内打开本工具时也不会被一同关闭。

## GLM 模型目录

软件内置用户提供的 `models.json`，当前包含：

- `glm-5.3`
- `glm-5.3-flash`（默认模型；内置目录声明 `text + image`，切换后可直接使用图片输入）
- `glm-5-turbo`

“配置预览及调整”功能区分为上下两部分。上半部分是 GLM `models.json`：支持读取本机文件、恢复内置版本、直接编辑、JSON/模型字段校验和一键保存注入。下半部分是当前 `config.toml`：支持读取、TOML 校验、人工调整和保存；保存时会备份原文件，并把当前供应商名称、地址、模型、认证策略和 bearer token 同步回本机供应商档案，后续一键切换会保留这些兼容调整。

默认 GLM 配置为：

```toml
model = "glm-5.3-flash"
model_reasoning_effort = "max"
model_catalog_json = "C:/Users/<用户名>/.codex/models-glm.json"

[model_providers.<稳定标签>]
name = "GLM"
base_url = "https://open.bigmodel.cn/api/v1"
wire_api = "responses"
requires_openai_auth = false
experimental_bearer_token = "<本机保存的 Key>"
```

## Threadripper

“一键安装 / 更新”会从 `Wangnov/codex-threadripper` 的最新官方 GitHub Release 下载 Windows x64 ZIP，下载对应 `.sha256` 后校验，校验通过才安装到 `%LOCALAPPDATA%\CodexProviderSwitch\tools\`。程序也兼容 npm/PATH 中已有的安装。

## 健康与用量

- OpenAI 直连：检查 OpenAI 网络和 `codex login status`；只有 ChatGPT/官方账号凭据会解锁直连按钮，API Key 只显示为检测到的凭据，不作为官方账号登录态。
- API1 / API2：使用当前 Key 请求 OpenAI 兼容 `/models`，显示正常、认证失败、限流、不可达和延迟。
- GLM：优先查询 Coding Plan 配额接口，并以 `/models` 校验实际模型链路；个人套餐留空组织/项目 ID，团队套餐必须同时填写 `org-...` 和 `proj-...`，软件会自动改用团队额度请求。
- GLM Coding Plan Key 可显示 5 小时和周额度及重置时间。普通按量 Key 可以正常调用模型，但官方接口会返回“不存在 Coding Plan”，此时只显示链路健康，不伪造剩余额度。
- API2 / Sub2API 网关默认自动请求 `/v1/usage?days=30`；接口只返回钱包余额、没有套餐总量时不显示无意义的剩余金额，界面会明确提示无法计算百分比。其它中转可在供应商页配置专用额度 URL 与 JSON 字段路径。API1 当前网关未开放可用的 Key 级额度接口，软件会明确提示，而不是伪造数值。
- 供应商卡片的“查额度”只查询已保存 Key 的额度接口，不切换当前供应商，也不发起模型链路探测；API1/API2 会查询配置的 `/v1/usage?days=30`，未使用中的 GLM 也可以单独查询并保留结果。
- Token 统计扫描当前 Codex Home 的本机会话 JSONL，每个会话只取最后一条累计 token 事件，默认汇总近 30 天 input、output、cache 与总量；界面统一以 M（百万 token）显示。
- 系统托盘提示当前供应商、链路状态和统计窗口内的 Token 总量；仅在异常发生变化或全部恢复时通知，不会每轮监控重复弹窗。
- 主窗口右下角只显示操作状态，不再显示 `config.toml` 路径。
- 监控页不再显示供应商可用性时间线或监控条区域，只保留基于实际请求记录的健康摘要、平均首响应时间、错误率和 Token 仪表盘。

## 本地数据与安全

- API Key 使用当前 Windows 用户的 DPAPI 加密，保存在 `.codex\codex-provider-switch\credentials.dat`。
- 非敏感设置位于 `.codex\codex-provider-switch\settings.json`。
- 旧版 `.codex\codex_provider_switch_labels.json` 仅在首次迁移时读取，不删除、不覆盖。
- 健康检查只向所配置的供应商地址发送对应 Key；日志、界面、测试结果均不输出完整 Key。
- 程序只通过 Codex 官方 CLI 检查/管理登录态，不读取或展示完整 auth 内容；不删除会话、备份或旧版本交付物。
- 应用图标采用 MIT 许可的 Tabler Icons `switch-horizontal` 图形并重新渲染；原始 SVG 和许可全文保存在 `assets/`。

## 源码结构

- `work/codex_api_provider_switch/codex_api_provider_switch.py`：兼容入口、版本与冒烟命令。
- `work/codex_api_provider_switch/provider_switch/`：配置、设置、模型目录、Threadripper、监控、进程与 UI 模块。
- `work/codex_api_provider_switch/assets/models.json`：内置 GLM 模型目录。
- `work/codex_api_provider_switch/Codex Provider Switch-1.3.6.spec`：PyInstaller 交付配置。
- `work/codex_api_provider_switch/installer/Codex Provider Switch-1.3.6.iss`：免管理员权限的 Inno Setup 安装器配置。
- `outputs/codex_api_provider_switch/Codex Provider Switch-1.3.6.exe`：可运行交付物。
- `outputs/codex_api_provider_switch/Codex Provider Switch-Setup-1.3.6.exe`：推荐的 Windows 安装包，可选桌面快捷方式和开机托盘监控。

## 验证与打包

在 `work\codex_api_provider_switch` 下运行：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q codex_api_provider_switch.py provider_switch tests
python codex_api_provider_switch.py --smoke-test
python codex_api_provider_switch.py --tray-smoke-test
python -m PyInstaller --noconfirm --distpath ..\..\outputs\codex_api_provider_switch "Codex Provider Switch-1.3.6.spec"
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp "installer\Codex Provider Switch-1.3.6.iss"
```

打包后验证：

```powershell
& "..\..\outputs\codex_api_provider_switch\Codex Provider Switch-1.3.6.exe" --version
& "..\..\outputs\codex_api_provider_switch\Codex Provider Switch-1.3.6.exe" --smoke-test
& "..\..\outputs\codex_api_provider_switch\Codex Provider Switch-1.3.6.exe" --tray-smoke-test
.\installer\verify_install.ps1
```

## 1.2.16 变更

- Token 统计改为按每条 `token_count` 事件的累计增量计算，并按事件时间归属供应商，避免跨供应商会话和重复累计造成偏差。
- 可用性监控改为读取 Codex 实际请求结果事件；刷新只读取本地会话日志，不主动向供应商发送探测请求。监控面板使用 Codex 的 `time_to_first_token_ms` 计算平均响应；总览供应商卡片不再显示延迟毫秒。

- 统一官方登录态文案：auth 仅用于 OpenAI 直连，API1、API2、GLM 使用各自独立 API Key。
- 修复未分类凭据或 API Key 登录误解锁 OpenAI 直连的问题。
- 当前供应商健康检查、额度和旧遥测状态按实际 `config.toml` 路由刷新，避免显示过期供应商数据。
- 总览、监控、部署与设置页支持小窗口滚动，供应商卡片和右下角缩放控件不再裁切。
- 修复旧版监控面板可用性时间线最后一行 GLM 被面板高度裁切的问题；1.3.4 起该时间线已移除。
- 压缩总览卡片和底部摘要模块，默认窗口无需上下滚动即可查看完整内容。
- 无边框窗口支持从边缘和四个角自由调整宽度与高度。

## 1.2.1 变更

- GLM 供应商卡片同时显示 5 小时和周额度；进度条仍使用 5 小时窗口，悬停可见两个窗口的已用比例。

1.2.1 发布校验：安装包 SHA-256 为 `F10F76CC4904BB6F3EE9FA96F2B891BCBAD70D3375FB8C831DB0D73A2BDA05D9`；便携 EXE SHA-256 为 `6BDA47C611748A6EF4AB64D59A0DB063434F81E4A559898A5DEDAC91145F3DCF`。

## 1.2.0 变更

- 加高 GLM 团队组织/项目 ID 输入框，并改为等宽字体，避免下划线被输入框裁剪导致无法确认格式。
- 项目 ID 标签和示例明确为 `proj_xxxxxxx`。
- 保存团队项目 ID 时自动把 `proj 46…` 一类空格分隔纠正为 `proj_46…`，并清理不可见空白。

## 1.1.9 变更

- “GLM 模型目录”升级为“配置预览及调整”：同一模块内支持 `models.json` 和 `config.toml` 编辑、校验、备份保存。
- 人工修改 `config.toml` 保存后，会同步供应商名称、地址、模型、认证策略和本地 Key 档案，后续切换不再覆盖必要的人工兼容调整。
- 切换供应商后，在 Codex 安全重启窗口内同步未归档线程的模型字段；重新打开旧会话也会跟随当前 API 的目标模型，不再恢复旧模型。

1.1.9 发布校验：安装包 SHA-256 为 `B8522278513410308A6D1DF3697E4B6BF9E7CB11B8661A8413EE3D49AFE50470`；便携 EXE SHA-256 为 `1A8E4BD29B663FB8FF95D28D323D867897D529977D8FCB0059F2164C77150FE3`。

## 1.1.8 变更

- 撤回无意义的钱包剩余金额展示；接口只返回余额、没有套餐总量时隐藏额度条，并提示无法计算百分比。
- GLM 切换清理旧配置遗留的 `env_key` 与 `http_headers`，避免认证优先级歧义和应用专用请求头影响流式链路。

1.1.8 发布校验：安装包 SHA-256 为 `11678FE6B6FA411C53D8E22D9F80D301614A111F87B446E30298851587EF67C1`；便携 EXE SHA-256 为 `14739EFF71704E41AB7C5D01A58498D490649A15EA0F5333C9312955A184C43C`。

## 1.1.7 变更

- 修复 Sub2API 钱包余额“额度已更新”但卡片不显示数据的问题；剩余金额现在会以货币格式显示。
- 只返回剩余金额、不返回套餐总量时，额度条改为“总量未知”的动态状态，并明确提示无法计算百分比，避免误造额度比例。

1.1.7 发布校验：安装包 SHA-256 为 `952877482A0F8A5B07D6609C460FB6025416CA844A69D11C41612B1A275786EC`；便携 EXE SHA-256 为 `BDA772D8C72668DB0C8EFD6836786C1FCBC59BC141B1AE3EF823D869DF2D1E4D`。

## 1.1.6 变更

- 本地 Token 统计、供应商卡片用量和托盘摘要统一改为 M（百万 token）显示；例如 8,810,684,590 tokens 会显示为 `8,810.7M tokens`。

1.1.6 发布校验：安装包 SHA-256 为 `3AE685F5AC2EF431E247149941143C5A3DCF005BBFA3BED94E0B5D6C821467AC`；便携 EXE SHA-256 为 `20E8CF3DB9AFE695409CE8335932448AAE7ADB4A9C9A5F8F14E094866A7281C6`。

## 1.1.5 变更

- GLM 额度查询支持团队套餐：同时填写 BigModel 团队组织 ID 和项目 ID 后自动请求 `?type=2` 并携带团队请求头；个人套餐仍保持原逻辑。
- 自动接入 Sub2API 兼容网关的 `/v1/usage` Key 额度；API2 无需手工配置即可显示剩余额度，API1 若网关不提供数据则显示明确原因。
- 额度解析新增币种/单位透传，错误信息不再吞掉网关业务提示。
- 设置结构升级到 v7，团队组织 ID 和项目 ID 使用非敏感 JSON 本地保存；API Key 仍保持 DPAPI 加密。

1.1.5 发布校验：安装包 SHA-256 为 `4E48D9099CB7E599D5013D719047BE3B3093025F01AC4540CA2CF584E0D760EF`；便携 EXE SHA-256 为 `AD66137652D1878BA1ABB34429FAAC737660D2E3662F0A9391A9FF972C7A623B`。

## 1.1.4 变更

- 区分 OpenAI API Key 与官方账号登录态：API Key 不再解锁 OpenAI 直连；只有 ChatGPT/官方账号凭据才启用直连按钮。
- 首次部署在没有官方账号时不再默认选择 OpenAI 直连，改为 API1。
- 清空 auth 后，即使环境变量或系统凭据库仍检测到 API Key，也不再判定为官方直连可用。
- 移除供应商卡片和切换按钮 tooltip，避免 Windows/Qt 主题差异导致白色空白弹窗，并为剩余 Qt tooltip 增加深色兜底样式。

1.1.4 发布校验：安装包 SHA-256 为 `A44F28EA294F1882CB341CE1B2AF4415BC810A8645F0E4E3885BB396E03F8329`；便携 EXE SHA-256 为 `55C7495674F179E830C8C589A03C939D7806FC92AFFD24D04A91C1F39B859FE2`。

## 1.1.3 变更

- 默认中转显示名改为 `API1` / `API2`，旧默认名自动迁移；用户自定义名称保持不变。
- 自动重启默认开启；重启助手仅结束 Codex 应用进程，配置先保存、验证后再重启。
- GLM 默认模型改为 `glm-5.3-flash`，内置 `models.json` 声明图片输入，切换时自动合并升级本机模型能力。
- 供应商与通用设置保存改为后台执行，Token 统计刷新不再阻塞主窗口。
- 移除切换按钮悬停空白提示；右下角状态栏不再显示配置文件路径。

1.1.3 发布校验：安装包 SHA-256 为 `F3B4F2A86247F80722E88C39F99F26F3F852B399E1C688D9012A9002C9BF6C9B`；便携 EXE SHA-256 为 `62E2872CD2948BEAD7D6F0FEC678ACFF8E81070D63E69F39FA72DFFCEF506CDC`。

## 1.1.2 变更

- 换用来自 Tabler Icons GitHub 仓库的 MIT 许可双向切换图标，统一 EXE、任务栏、窗口与标题栏图标。
- 修复从 Codex 内打开工具后部署时被 `taskkill /T` 一同结束的问题；默认关闭自动重启，并增加父进程保护和部署后重启顺序测试。
- 补齐单中转部署逻辑：只配置中转 1 或中转 2 均可，空供应商按钮显示“未配置”并禁用，核心切换层再次校验。
- 验证保留官方登录态与 GLM 往返：GLM 使用独立 Key 且不清理 auth，切回直连仍可使用原登录态。
- 从 GLM 恢复官方登录时，切回的中转会立即同步新的认证策略。
- 新增 Windows 系统托盘后台常驻、异常/恢复通知、后台 Token 刷新和单实例唤醒；重复打开快捷方式会恢复已有窗口。
- 新增按当前用户安装的 Inno Setup 安装包，可选开机后以 `--tray` 启动后台监控，并验证安装、运行和卸载闭环。

1.1.2 发布校验：安装包 SHA-256 为 `4E2BB614DA01EB4412B1E8A43E28C48D39451BE57BE4CEF222DE4B5E0CFDE6FA`；便携 EXE SHA-256 为 `529F87F59E026971A2F66744DFDACF4944E6BD0B3DFB038BD5E1B7A9180ED4A5`。

## 1.1.1 变更

- 新增 OpenAI 官方 auth 检查、交互式登录、清空和保留策略；官方直连按钮按真实登录态动态禁用/恢复。
- 新增独立部署与登录模块，GLM 状态恢复官方登录前自动切回已配置中转。
- 新增 PySide6 液态毛玻璃主界面，保留 Tk 兼容回退；总览、部署、监控、用量和工具模块分区显示。
- 参考 Metrik 的配额、重置、Token 用量分层表达，避免把额度查询结果误当作本地调用统计。

- 从单文件双按钮工具重构为多模块供应商控制台。
- 新增首次部署向导与简化日常总览。
- 新增 OpenAI 直连、中转 1、中转 2、GLM 四路切换。
- 新增稳定 provider 标签、配置备份/回滚和旧设置迁移。
- 新增 GLM 内置模型目录编辑与注入。
- 新增 Threadripper 官方包一键安装与 SHA-256 校验。
- 新增供应商健康、GLM/自定义额度和本机 Token 统计。
- API Key 改为 Windows DPAPI 本地加密保存。

## 1.3.1 变更

- 修复安装/升级时托盘常驻进程无法关闭的问题。非静默安装仍提供“关闭并继续 / 取消”选择；确认关闭后优先通过本地 IPC 让新版本干净退出，旧版本或无响应进程只按 `Codex Provider Switch.exe` 文件过滤强制关闭，不会结束 `codex.exe` 或其他应用。
- 新增 `--installer-shutdown` 隐藏命令，并让安装器在文件替换前主动请求退出；安装完成后不会自动重启后台进程，避免安装器卡在关闭阶段。
- 1.3.1 产物 SHA-256：便携版 `0674EBB245283ED2DD634DA57572C241237826F94664837B68E8BEFD9BDCAABA`；安装包 `4BCF1281A7240B67A631330CA460F4F6A9777B56D69821A71A0EE52C5BCD1B4C`。

## 1.3.3 变更

- 修复 AP1 首次导入和启动迁移误用 OpenAI 官方认证的问题，确保 API1 bearer/API Key 链路可调用。
- 缺少历史 AP1 快照时只补齐缺失的图像扩展标记；切换 GLM/API2/OpenAI 仍会隔离 AP1 专用字段。
- 明确 GPT Image 2 本地命令需要独立 OpenAI Platform `OPENAI_API_KEY`，不会误用供应商 Key。
- 1.3.3 产物 SHA-256：便携版 `E08DCCAB83F6ACFDD545A94BD51FA8A18805AC66F09263856A3F19B65DFB632C`；安装包 `19146F465EB01E6286C0D873FBA96F1428E3DD8CD69B629FB7A2C06895083325`。

## 1.3.4 变更

- API1 的 AP1 图像扩展兼容策略改为按 URL 主机识别，仅对 `aqyimin.chat` 生效；API1 改成其他 URL 后与 API2 使用同一套通用中转策略，并自动清理残留的 AP1 图像字段、服务级别和专用请求头，同时保留普通自定义请求头。
- 监控页移除供应商可用性时间线/条形区域，避免无实际请求时产生误导；保留请求健康摘要、按有效请求加权的平均首响应时间、错误率和 Token 使用量仪表盘。
- 新增 API1 URL 迁移回归测试，验证 AP1 残留配置不会泄漏到普通中转。
- 1.3.4 产物 SHA-256：便携版 `C27678B87C5BFEB1E855C38246E74D8A84323A832A0992A4D67259D94F1C3B9F`；安装包 `8D574E678B99482DC44E0D45C1015B302ED19C81FC2ECE43416536782E2D3112`。

## 1.3.5 变更

- 配置预览及调整保存 TOML 时，自动修复明确的 Windows 单反斜杠路径和常见未加引号字符串；无法自动修复时显示行列、指针和建议。
- TOML 错误提示会脱敏 `experimental_bearer_token`、API Key、Authorization、Token、Secret 和 Password，不会把密钥写入界面、日志或测试输出。
- 1.3.5 产物 SHA-256：便携版 `56CF7CD4308F506F732877F7BD3ABB9040B7C9F8970E9671D8D3E136CC3C2D9A`；安装包 `53BB346516B75A6071D73EE8CDC14D59B41430D9FBBB389D02D49C9479401256`。

## 1.3.6 变更

- API1/API2 切换时默认写入 `service_tier = "fast"` 和 `[features].fast_mode = true`。
- 切换到 GLM 时清理 Fast mode 字段，但保留其它 `[features]` 配置；API1 的 aqyimin 显式 `priority` tier 继续兼容保留。
- 1.3.6 产物 SHA-256：便携版 `916C1C766C0CA6C3FC5EC5C88885F20004EEF127CC93949A3E38D575F8C47414`；安装包 `97E1BB6B1905A7715747E53F2BB5BB9CF720593FECE6BB64A72EAB896A2D753E`。

## 1.3.0 变更

- API1、API2、GLM 之间切换直接执行，不再弹出共存选择；修复 Qt 切换入口未启动后台任务的问题。
- Qt 与 Tk 监控统一读取 Codex 实际请求结果，不再因刷新界面主动请求供应商；监控只反馈当前路由。
- 修复 API1/API2 “仅查询额度”提前返回导致不请求 `/v1/usage` 的问题；GLM 额度成功后仍会独立校验模型链路。
- 额度-only 查询不再要求先填写默认模型；只要保存了额度地址（或 API 地址）和 Key，即可查询未启用供应商的额度。
- 可用率与错误率按请求总数加权，兼容旧监控记录中的异常延迟值。
- 安装器检测到本程序运行时会提供关闭提示；静默安装会自动处理，避免托盘进程导致安装卡住。

## 1.2.20 变更

- 监控平均响应及最近 3 次响应统一以秒显示，保留两位小数；内部毫秒数据保持不变。






