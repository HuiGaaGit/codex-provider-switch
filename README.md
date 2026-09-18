# 11CodexAPI供应商开关

Windows 桌面工具，用一个稳定的 Codex provider 标签在 OpenAI 直连、中转 1、中转 2 与 GLM 之间切换，并集中完成历史会话同步、链路健康、额度查询和本机 Token 统计。

当前交付版本：`1.2.1`

## 日常使用

首次启动会打开四步部署向导：

1. 自动定位 `%USERPROFILE%\.codex\config.toml`，也可手动选择其他 Codex Home。
2. 导入原 `model_provider`、模型、供应商地址与旧版中转设置；中转 1 / 2 可只配置其中一个，未配置项会在总览中禁用。
3. 检测 OpenAI 官方登录态，选择是否在中转模式保留 auth；确认稳定 provider 标签、注入 `models.json`，并检测或一键安装 Codex Threadripper。
4. 选择首次启用的供应商，备份并写入配置。

完成部署后默认进入总览，只保留四个一键切换入口和健康、额度、Token 摘要。供应商详情、模型目录、Threadripper、官方登录态与高级设置分别位于独立功能区。关闭主窗口时默认缩入 Windows 系统托盘，链路、额度和 Token 统计继续后台刷新；托盘菜单可恢复窗口、立即刷新或彻底退出。Windows 优先使用 PySide6 液态毛玻璃界面，缺少 Qt 运行库时回退到 Tk 界面。

## 切换与历史兼容

- OpenAI 直连：取消自定义 `model_provider` 默认值，继续使用 Codex 当前 OpenAI/ChatGPT 登录；只有检测到保留的官方登录态时按钮才可用。
- API1 / API2：写入各自的 `base_url`、模型和 `experimental_bearer_token`，名称与 Key 可本地修改；新装默认显示名是 `API1` 和 `API2`。
- GLM：写入 `responses` 协议、GLM 模型、Key 和当前 Codex Home 下的 `models.json`；切换时会同步未归档会话线程的模型字段，重新打开旧会话也会跟随当前 API 模型。团队项目 ID 使用 `proj_xxxxxxx`，保存时自动纠正粘贴产生的空格分隔。
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
model_catalog_json = "C:/Users/<用户名>/.codex/models.json"

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
- Token 统计扫描当前 Codex Home 的本机会话 JSONL，每个会话只取最后一条累计 token 事件，默认汇总近 30 天 input、output、cache 与总量；界面统一以 M（百万 token）显示。
- 系统托盘提示当前供应商、链路状态和统计窗口内的 Token 总量；仅在异常发生变化或全部恢复时通知，不会每轮监控重复弹窗。
- 主窗口右下角只显示操作状态，不再显示 `config.toml` 路径。

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
- `work/codex_api_provider_switch/Codex Provider Switch-1.2.1.spec`：PyInstaller 交付配置。
- `work/codex_api_provider_switch/installer/Codex Provider Switch-1.2.1.iss`：免管理员权限的 Inno Setup 安装器配置。
- `outputs/codex_api_provider_switch/Codex Provider Switch-1.2.1.exe`：可运行交付物。
- `outputs/codex_api_provider_switch/Codex Provider Switch-Setup-1.2.1.exe`：推荐的 Windows 安装包，可选桌面快捷方式和开机托盘监控。

## 验证与打包

在 `work\codex_api_provider_switch` 下运行：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q codex_api_provider_switch.py provider_switch tests
python codex_api_provider_switch.py --smoke-test
python codex_api_provider_switch.py --tray-smoke-test
python -m PyInstaller --noconfirm --distpath ..\..\outputs\codex_api_provider_switch "Codex Provider Switch-1.2.1.spec"
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp "installer\Codex Provider Switch-1.2.1.iss"
```

打包后验证：

```powershell
& "..\..\outputs\codex_api_provider_switch\Codex Provider Switch-1.2.1.exe" --version
& "..\..\outputs\codex_api_provider_switch\Codex Provider Switch-1.2.1.exe" --smoke-test
& "..\..\outputs\codex_api_provider_switch\Codex Provider Switch-1.2.1.exe" --tray-smoke-test
.\installer\verify_install.ps1
```

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

- 本地 Token 统计、供应商卡片用量和托盘摘要统一改为 M（百万 token）显示；例如 8,810,684,590 tokens 会显示为 `8810.7M tokens`。

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
