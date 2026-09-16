# 11CodexAPI供应商开关

Windows 桌面工具：切换 Codex 的 `cch_gz` API 供应商配置，并可调用已安装的 `codex-threadripper` 同步历史会话。

## 事实源

- `work/codex_api_provider_switch/codex_api_provider_switch.py`：应用逻辑与版本号。
- `work/codex_api_provider_switch/assets/`：应用图标资源。
- `outputs/codex_api_provider_switch/Codex Provider Switch-1.0.23.exe`：当前可运行交付版本。

## 最小验证

```powershell
python -m py_compile work\codex_api_provider_switch\codex_api_provider_switch.py
```

关闭开关只注释默认的 `model_provider = "cch_gz"`，保留完整的 `[model_providers.cch_gz]` 注册和五个供应商字段，以便已同步的历史会话仍能加载。首次修复旧布局时，会将 `notify`、`service_tier` 原样移动到供应商段之前，恢复其顶层 TOML 语义；后续切换不会触碰它们。

v1.0.8 集成独立 Codex 重启助手。切换顺序固定为：关闭整个 Codex 进程树、写入配置、同步历史会话、通过 Windows AppsFolder 重新启动。关闭前会先记录实际启动标识，确保关闭后无需枚举受限的 WindowsApps 目录也能重新打开 Codex。

v1.0.9 为桌面 16–48 像素图标提供专用的简化图层，移除小尺寸下不可辨识的箭头和纹理，提升快捷方式与窗口标题栏的清晰度。

v1.0.10 修复历史会话兼容与同步：Threadripper 始终以 `C:\Users\ASUS\.codex` 作为真实主目录运行，并在同步后校验状态；历史 `cch_gz` 会话不会再因关闭默认供应商而丢失注册配置。

v1.0.11 修复 Windows AppsFolder 启动标识中的 `!` 被批处理延迟展开吞掉的问题；已在不关闭现有 Codex 的情况下验证启动入口成功。

v1.0.12 将界面名称调整为 `Codex Provider Switch`，两个切换入口分别显示为“打开CCH”和“打开OpenAI”；对应的供应商配置逻辑保持不变。

v1.0.13 将可执行文件打包名称同步为 `Codex Provider Switch`，并同步项目对话标题与桌面快捷方式名称。

v1.0.14 将切换按钮名称更新为“打开Pro 20X”和“打开Plus”；原有的 CCH 默认供应商启用与 OpenAI 默认供应商恢复逻辑不变。

v1.0.15 将按钮名称统一为“打开Pro 20x”和“打开Plus”，并将当前状态显示为“已启用Pro 20x”或“已启用Plus”。

v1.0.16 修复切换完成后按钮未恢复可点击的问题，并将供应商写入提示改为简洁描述。

v1.0.17 修复 Codex 已收到 Windows AppsFolder 启动请求、但因进程路径暂时不可读而被误报为启动超时的问题。重启助手会同时校验安装路径和启动后新增的 ChatGPT 进程，并将等待上限与提示统一为 60 秒。

v1.0.18 新增“设置”入口，可自定义两个供应商切换按钮的显示名称。名称保存在 `.codex\\codex_provider_switch_labels.json`，仅影响界面文字，不改变原有切换功能。

v1.0.19 让当前状态随自定义按钮名称同步变化；按钮名称以“打开”“启用”“切换到”或“切换至”开头时，状态栏会去掉动作前缀后显示“已启用…”名称。

v1.0.20 在设置中增加 API 1（Pro 20x）和 API 2（Pro 5x）Key 的录入与保存；Pro 5x 按钮增加 OPENAI/API 5x 下拉选项。选择 API 5x 时启用 cch_gz 并写入 API 2 Key，选择 OPENAI 时恢复 OpenAI 默认供应商。

v1.0.21 优化浅色半透明 UI：启用 Windows Acrylic/Mica 背景回退、浅蓝白玻璃卡片、高光描边、状态呼吸光点和背景柔和光晕；切换逻辑保持不变。

v1.0.22 根据视觉反馈进一步降低边界感：移除玻璃卡片硬描边和按钮焦点框，改用同色系低对比层次与留白，让文字、按钮和浅色背景更自然地融合。

v1.0.23 应用户要求恢复到毛玻璃改动前的原生 Tk/ttk UI 风格；API Key、OPENAI/API 5x 下拉及状态联动功能保留。
