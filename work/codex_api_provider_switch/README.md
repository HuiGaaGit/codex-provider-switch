# Codex Provider Switch v1.0.15

此工具专用于切换 `C:\Users\ASUS\.codex\config.toml` 里的 `cch_gz` API 供应商。

它用 `model_provider = "cch_gz"` 和 `[model_providers.cch_gz]` 定位配置，不依赖行号。启用时去掉这两项及其供应商配置项前的注释；关闭时恢复注释。操作顺序是先关闭整个 Codex 进程树，再写配置、同步全部历史会话，最后重新启动 Codex；窗口会逐步显示各阶段结果。

`notify`、`service_tier` 不在切换范围中，因为当前配置的相同顶层键已经处于启用状态，重复启用会让 TOML 无法解析。

界面也提供“仅同步历史会话（当前供应商）”按钮，不改动 API 供应商配置。同步由 `codex-threadripper 0.3.6` 完成；它会自行在 `.codex\\backups` 创建会话状态保护备份。此前取消的是 `config.toml` 的自动备份。

v1.0.8 使用内置的蓝绿切换与同步箭头图标，应用窗口和桌面快捷方式均会显示该图标。它集成来自用户提供重启脚本的独立重启助手：在关闭前记录运行中 Codex 的实际包根和启动标识，关闭后将其交给启动助手，因此不再依赖当前环境中不可用的 `Get-AppxPackage` 或对 WindowsApps 的目录枚举。如果退出或启动超时，程序会明确报错并恢复操作按钮。

v1.0.9 将桌面图标改为高对比、低细节的双状态开关，并提供 16、20、24、32、40、48、64、128、256 像素的 ICO 图层，避免 Windows 缩小复杂大图造成模糊。

v1.0.10 将“关闭”定义为取消默认使用 `cch_gz`，而不是删除其注册段。这样 Threadripper 同步过的旧会话仍然能够解析自己的 provider key。程序还会显式传入真实 `C:\Users\ASUS\.codex` 给 Threadripper，避免在受限环境中错误同步到 `CodexSandboxOffline` 目录；同步后会执行状态校验。

v1.0.11 在调用 Windows AppsFolder 时临时关闭批处理的延迟变量展开，保留 AppUserModelID 必需的 `!` 分隔符，解决“Windows could not launch Codex”。

v1.0.12 仅更新了界面文字：软件名称为 `Codex Provider Switch`，“打开 API 供应商”改为“打开CCH”，“关闭默认 API 供应商”改为“打开OpenAI”；两个按钮仍分别调用原有的 CCH 默认供应商启用与 OpenAI 默认供应商恢复逻辑。

v1.0.13 将 Windows 可执行文件名也更新为 `Codex Provider Switch-1.0.13.exe`。

v1.0.14 将两个按钮显示为“打开Pro 20X”和“打开Plus”；前者仍启用 CCH 默认供应商，后者仍恢复 OpenAI 默认供应商。

v1.0.15 统一使用“打开Pro 20x”与“打开Plus”，状态栏相应显示“已启用Pro 20x”或“已启用Plus”。
