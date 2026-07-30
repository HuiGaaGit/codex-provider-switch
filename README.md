# 11CodexAPI供应商开关

Windows 桌面工具：切换 Codex 的 `cch_gz` API 供应商配置，并可调用已安装的 `codex-threadripper` 同步历史会话。

## 事实源

- `work/codex_api_provider_switch/codex_api_provider_switch.py`：应用逻辑与版本号。
- `work/codex_api_provider_switch/assets/`：应用图标资源。
- `outputs/codex_api_provider_switch/Codex Provider Switch-1.0.13.exe`：当前可运行交付版本。

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
