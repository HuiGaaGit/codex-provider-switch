# Codex Provider Switch v1.2.25

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
python -m PyInstaller --noconfirm --distpath ..\..\outputs\codex_api_provider_switch "Codex Provider Switch-1.2.25.spec"
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp "installer\Codex Provider Switch-1.2.25.iss"
.\installer\verify_install.ps1
```

打包配置会内置 `assets/` 下的 Tabler 衍生窗口图标、第三方许可、重启助手和 GLM `models.json`。单文件产物为 `outputs/codex_api_provider_switch/Codex Provider Switch-1.2.25.exe`，安装包为同目录下的 `Codex Provider Switch-Setup-1.2.25.exe`。安装版支持系统托盘常驻和可选开机后台监控。版本号的运行时事实源为 `provider_switch/constants.py`，Windows 文件版本由 `assets/version_info-1.2.25.txt` 提供。

额度查询支持在供应商未使用时单独执行。点击卡片上的“查额度”只调用已保存的额度接口，不切换当前供应商，也不执行健康探测。对于 aqyimin.chat，切回时会恢复 GPT 图像能力相关的原模型目录配置；切换 GLM 时使用 GLM 专用模型目录。

完整功能、部署流程、安全边界和额度口径见项目根目录 `README.md`。







