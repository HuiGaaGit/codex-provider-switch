# Codex Provider Switch v1.3.3

源码入口：`codex_api_provider_switch.py`

## 开发验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q codex_api_provider_switch.py provider_switch tests
python codex_api_provider_switch.py --smoke-test
python codex_api_provider_switch.py --tray-smoke-test
```

## 打包

```powershell
python -m PyInstaller --noconfirm --distpath ..\..\outputs\codex_api_provider_switch "Codex Provider Switch-1.3.3.spec"
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp "installer\Codex Provider Switch-1.3.3.iss"
.\installer\verify_install.ps1
```

打包配置会内置 `assets/` 下的 Tabler 衍生窗口图标、第三方许可、重启助手和 GLM `models.json`。安装注入使用独立的 `models-glm.json`，避免污染 GPT 中转的模型目录。单文件产物为 `outputs/codex_api_provider_switch/Codex Provider Switch-1.3.3.exe`，安装包为同目录下的 `Codex Provider Switch-Setup-1.3.3.exe`。安装版支持系统托盘常驻和可选开机后台监控。版本号的运行时事实源为 `provider_switch/constants.py`，Windows 文件版本由 `assets/version_info-1.3.3.txt` 提供。

1.3.1 修复安装器关闭常驻进程：非静默安装仍会询问是否关闭程序；确认后优先通过本地 IPC 让托盘进程干净退出，旧版本或无响应进程只会按文件过滤强制关闭 `Codex Provider Switch.exe`，不会结束 Codex 或其他应用。
1.3.2 增强 AP1 `aqyimin.chat` 兼容性：切换 GLM/API2/OpenAI 时隔离 AP1 专用配置，切回时恢复服务级别、模型设置、图像开关和白名单图像头；快照不保存任何 API key。
1.3.3 修复 AP1 首次导入时误写 `requires_openai_auth = true` 的问题，并在启动时安全迁移旧配置；AP1 始终使用自己的 bearer/API Key。Codex Desktop 的 GPT Image 2 本地生成命令是独立的 OpenAI Platform API 路径，需要单独提供 `OPENAI_API_KEY`，不会读取或代用 AP1/GLM 的 Key。
1.3.1 产物 SHA-256：便携版 `0674EBB245283ED2DD634DA57572C241237826F94664837B68E8BEFD9BDCAABA`；安装包 `4BCF1281A7240B67A631330CA460F4F6A9777B56D69821A71A0EE52C5BCD1B4C`。1.3.2 产物 SHA-256：便携版 `A95C6A2014836ED69D5385ADB3C7A29E855EF43F7BA765CC8F9DF74B7A30EA90`；安装包 `64FA18D555F46C2710C5BA1EF458265265A077D6FB983E48D922EE5EC9147932`。1.3.3 产物 SHA-256：便携版 `E08DCCAB83F6ACFDD545A94BD51FA8A18805AC66F09263856A3F19B65DFB632C`；安装包 `19146F465EB01E6286C0D873FBA96F1428E3DD8CD69B629FB7A2C06895083325`。

额度查询支持在供应商未使用时单独执行。点击卡片上的“查额度”只调用已保存的额度接口，不切换当前供应商，也不执行模型链路探测；只要有额度地址（或 API 地址）和 Key，即使还没有默认模型也可以查询，API1/API2 会查询配置的 `/v1/usage?days=30`。对于 aqyimin.chat，切回时会恢复 GPT 模型、图像能力相关的原模型目录和推理配置；切换 GLM 时使用独立的 `models-glm.json`。API1 / API2 / GLM 互相切换直接执行，不弹出共存选择。

完整功能、部署流程、安全边界和额度口径见项目根目录 `README.md`。

## API1 aqyimin.chat 兼容项

当 API1 的地址是 `https://www.aqyimin.chat/v1` 时，切换器只识别并保留该供应商需要的行为配置：`responses` 协议、`service_tier`、GPT 模型/推理设置、`[features] image_generation`，以及固定的 `x-openai-actor-authorization` 图像扩展头。缺少历史快照时只补齐缺失的 `image_generation=true` 和白名单头，不覆盖明确的人工设置。切到 GLM、API2 或 OpenAI 直连时会移除 AP1 专用的服务级别、图像开关和请求头，避免污染其他链路；切回 AP1 时从本机 `provider-switch-backups/image-capability.json` 恢复这些非敏感项。

快照不会保存 bearer/API key、`env_key`、插件/MCP 或浏览器运行时配置；若原配置已有自定义模型目录，只会按原有逻辑记录该目录路径以便恢复。API key 始终从本地加密凭据库写入当前 provider 表；因此可以把用户的 AP1 配置作为参考，不需要把原始 `config.toml` 纳入安装包或仓库。

## 图像调用边界

AP1 配置中的 `features.image_generation = true` 与 `x-openai-actor-authorization = "local-image-extension"` 只用于 aqyimin.chat 对 Codex 请求的图像扩展/图像输入能力。桌面端通过 `gpt-image` 技能执行的 GPT Image 2 生成命令使用 OpenAI Platform Images/Responses API，按官方文档读取环境变量 `OPENAI_API_KEY`；它不会从 Codex 的 `experimental_bearer_token` 或 `auth.json` 自动取值。若未配置该独立凭据，命令会明确提示无法调用，供应商切换器不会复制、打印或改写任何密钥。







