# Codex Provider Switch v1.3.1

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
python -m PyInstaller --noconfirm --distpath ..\..\outputs\codex_api_provider_switch "Codex Provider Switch-1.3.1.spec"
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp "installer\Codex Provider Switch-1.3.1.iss"
.\installer\verify_install.ps1
```

打包配置会内置 `assets/` 下的 Tabler 衍生窗口图标、第三方许可、重启助手和 GLM `models.json`。安装注入使用独立的 `models-glm.json`，避免污染 GPT 中转的模型目录。单文件产物为 `outputs/codex_api_provider_switch/Codex Provider Switch-1.3.1.exe`，安装包为同目录下的 `Codex Provider Switch-Setup-1.3.1.exe`。安装版支持系统托盘常驻和可选开机后台监控。版本号的运行时事实源为 `provider_switch/constants.py`，Windows 文件版本由 `assets/version_info-1.3.1.txt` 提供。

1.3.1 修复安装器关闭常驻进程：非静默安装仍会询问是否关闭程序；确认后优先通过本地 IPC 让托盘进程干净退出，旧版本或无响应进程只会按文件过滤强制关闭 `Codex Provider Switch.exe`，不会结束 Codex 或其他应用。
1.3.1 产物 SHA-256：便携版 `0674EBB245283ED2DD634DA57572C241237826F94664837B68E8BEFD9BDCAABA`；安装包 `4BCF1281A7240B67A631330CA460F4F6A9777B56D69821A71A0EE52C5BCD1B4C`。

额度查询支持在供应商未使用时单独执行。点击卡片上的“查额度”只调用已保存的额度接口，不切换当前供应商，也不执行模型链路探测；只要有额度地址（或 API 地址）和 Key，即使还没有默认模型也可以查询，API1/API2 会查询配置的 `/v1/usage?days=30`。对于 aqyimin.chat，切回时会恢复 GPT 模型、图像能力相关的原模型目录和推理配置；切换 GLM 时使用独立的 `models-glm.json`。API1 / API2 / GLM 互相切换直接执行，不弹出共存选择。

完整功能、部署流程、安全边界和额度口径见项目根目录 `README.md`。







