# Codex Provider Switch v1.0.23

此工具专用于切换 `C:\Users\ASUS\.codex\config.toml` 里的 `cch_gz` API 供应商。

它按 `model_provider = "cch_gz"` 和 `[model_providers.cch_gz]` 定位配置，不依赖行号。“打开Pro 20x”将 CCH 设为默认供应商；“打开Plus”仅取消该默认设置，同时保留 CCH 注册以兼容历史会话。`notify` 与 `service_tier` 不会被切换。

界面也提供“仅同步历史会话（当前供应商）”按钮，不改动 API 供应商配置。同步由 `codex-threadripper 0.3.6` 完成；它会自行在 `.codex\\backups` 创建会话状态保护备份。此前取消的是 `config.toml` 的自动备份。

v1.0.8 使用内置的蓝绿切换与同步箭头图标，应用窗口和桌面快捷方式均会显示该图标。它集成来自用户提供重启脚本的独立重启助手：在关闭前记录运行中 Codex 的实际包根和启动标识，关闭后将其交给启动助手，因此不再依赖当前环境中不可用的 `Get-AppxPackage` 或对 WindowsApps 的目录枚举。如果退出或启动超时，程序会明确报错并恢复操作按钮。

v1.0.9 将桌面图标改为高对比、低细节的双状态开关，并提供 16、20、24、32、40、48、64、128、256 像素的 ICO 图层，避免 Windows 缩小复杂大图造成模糊。

v1.0.10 将“关闭”定义为取消默认使用 `cch_gz`，而不是删除其注册段。这样 Threadripper 同步过的旧会话仍然能够解析自己的 provider key。程序还会显式传入真实 `C:\Users\ASUS\.codex` 给 Threadripper，避免在受限环境中错误同步到 `CodexSandboxOffline` 目录；同步后会执行状态校验。

v1.0.11 在调用 Windows AppsFolder 时临时关闭批处理的延迟变量展开，保留 AppUserModelID 必需的 `!` 分隔符，解决“Windows could not launch Codex”。

v1.0.12 仅更新了界面文字：软件名称为 `Codex Provider Switch`，“打开 API 供应商”改为“打开CCH”，“关闭默认 API 供应商”改为“打开OpenAI”；两个按钮仍分别调用原有的 CCH 默认供应商启用与 OpenAI 默认供应商恢复逻辑。

v1.0.13 将 Windows 可执行文件名也更新为 `Codex Provider Switch-1.0.13.exe`。

v1.0.14 将两个按钮显示为“打开Pro 20X”和“打开Plus”；前者仍启用 CCH 默认供应商，后者仍恢复 OpenAI 默认供应商。

v1.0.15 统一使用“打开Pro 20x”与“打开Plus”，状态栏相应显示“已启用Pro 20x”或“已启用Plus”。

v1.0.16 修复切换完成后按钮仍被禁用的问题，并将进度提示简化为“正在写入供应商设置”。

v1.0.17 修复 WindowsApps 进程路径暂时不可读时的启动误判：启动前记录已有 ChatGPT 进程，启动后以“包路径匹配或出现新增进程”作为成功条件，并统一等待 60 秒。

v1.0.18 新增标题栏“设置”入口，可分别自定义两个供应商按钮的显示名称。自定义内容保存在 `.codex\\codex_provider_switch_labels.json`，仅改变界面文字，按钮仍执行原来的启用/恢复逻辑。

v1.0.19 当前状态文字会同步采用对应按钮的自定义名称；“打开”“启用”“切换到”“切换至”等动作前缀会在状态栏中自动省略。

v1.0.20 设置窗口新增 API 1（Pro 20x Key）和 API 2（Pro 5x Key）密码框。Pro 5x 入口右侧下拉可选 OPENAI 或 API 5x：OPENAI 保持原来的 OpenAI 默认供应商逻辑，API 5x 启用 cch_gz 并写入 API 2 的 `experimental_bearer_token`。

v1.0.21 将界面更新为浅色半透明玻璃风格：Windows 11 使用 Acrylic/Mica 背景，非 Windows 11 使用浅色玻璃回退样式；状态指示点和背景光晕会缓慢呼吸变化。

v1.0.22 移除玻璃卡片硬描边和按钮焦点框，降低控件与底色的对比，使用更柔和的同色系层次和留白。

v1.0.23 恢复到毛玻璃改动前的原生 Tk/ttk UI 风格；API Key、OPENAI/API 5x 下拉及状态联动功能继续保留。
