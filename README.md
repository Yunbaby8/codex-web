# Codex Web

最新变更见 [更新公告](CHANGELOG.md)。

支持原生 Windows 和 Linux 的 Codex 网页界面。提供聊天、会话恢复、附件、目录选择、Monaco 文件编辑、只读 Git 差异、账号额度和主题。两种系统共用 frontend 和主要后端代码，后端通过 stdio 调用当前系统的 Codex CLI。

## Windows 安装与启动

需要 Python 3.12+、Git，以及已经安装并登录的原生 Codex CLI。

1. 运行 `setup.ps1` 创建 `.venv` 并安装依赖；也可用 `setup.ps1 -Python 'Python完整路径'` 指定解释器。
2. 编辑生成的 `config.yaml`，填写非空密码和实际存在的 `allowed_roots`，例如 `C:/repo`。
3. 双击 `start.cmd`，或运行 `start.ps1`。打开 http://127.0.0.1:5200，前台按 Ctrl+C 停止。
4. 后台运行用 `start.ps1 -Background`，停止用 `stop.ps1`。

启动脚本优先从 PATH 查找 Codex、Git，再检查当前用户的 Codex 安装目录和常见 Git 路径。依赖安装在项目虚拟环境中，不修改 Codex App 自带环境。已有 config.yaml 和 data 会保留。

本机已有的上一级 `后台启动.py` 存在时继续使用它；独立克隆的项目没有这个辅助脚本时，后台直接运行 `.venv/Scripts/pythonw.exe -m backend.run`。启动记录为 `data/web.pid`；独立启动的日志为 `data/web.stdout.log`、`data/web.stderr.log`。停止脚本核对进程身份后清理所属进程树，兼容已有的本机后台入口。

## Linux 安装与启动

需要 Python 3.12+（包含 venv/pip）、Git，以及已经安装并登录的 Linux Codex CLI。进入项目目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n config.linux.example.yaml config.yaml
```

编辑 config.yaml，填写自己的非空密码，以及实际存在的 allowed_roots，例如 `/home/your-user/projects`。然后运行：

```bash
bash start.sh
```

默认打开 http://127.0.0.1:5200，按 Ctrl+C 停止。Linux 不会导入 Windows 专用接口，也不需要 Windows、WSL 或 PowerShell。启动脚本优先使用 `.venv/bin/python`，否则使用 `python3`。

后台服务可参考 `codex-web.service.example`：修改 WorkingDirectory、ExecStart 和 PATH 后，安装为当前用户的 systemd 服务。也可把 ExecStart 写成 `/usr/bin/bash /实际安装目录/start.sh`。项目不会自动安装或启用服务。

## 配置与数据

Windows 示例为 `config.example.yaml`，Linux 示例为 `config.linux.example.yaml`。`config.yaml`、data、登录凭据、虚拟环境不纳入 Git；两个系统使用各自的配置和运行数据。

| 字段 | 用途 |
| --- | --- |
| host / port | 默认仅本机 127.0.0.1:5200 |
| username / password | 网页登录账号，密码必须非空；独立于 Codex 登录 |
| allowed_roots | 网页可选和访问的目录，必须存在；支持 ~，不展开环境变量 |
| allowed_origins | 实际浏览器来源，包括协议、主机和端口 |
| codex_binary | 可省略：Windows 默认 codex.exe，Linux 默认 codex；也可填完整路径 |
| sandbox | read-only、workspace-write 或 danger-full-access |
| models | 建议 []，从当前 CLI 获取可用模型 |
| data_dir | 默认项目 data，保存网页会话数据库和附件 |
| auth_file | 账号识别及登录文件变更检测，不改变 CLI 的登录位置 |
| auth_accounts_dir | 可选账号快照标签目录 |
| cookie_secure | HTTP 为 false，HTTPS 为 true |

可用 CODEX_WEB_CONFIG 指定外置配置。Windows 启动脚本沿用当前 Windows 用户的 `.codex`；Linux 沿用 CODEX_HOME，未设置时使用当前用户的 `.codex`。配置修改后重启 Web；页面“重载”只重启内部 Codex。

allowed_roots 约束网页文件接口，不等于 Codex 进程的权限隔离。开放远程访问时需同时配置监听地址、Origin 和防火墙。

不会自动导入另一系统的数据库、附件或 Codex 原生会话。跨系统迁移必须核对旧目录映射和原生上下文，不能只替换路径字符串。备份运行中的 SQLite 应使用 backup 接口，或停服务后复制。

## 会话执行权限

输入框左下角提供“只读、工作区写入、完全访问”三档。选择在下一次发送任务时应用并保存到当前会话；未发送的选择不会写入会话。执行中禁止切换。旧会话尚未选择时沿用服务器配置，切换会话、重载后恢复已保存的选择。只读和工作区写入沿用 Codex 的按需审批；完全访问不逐项审批，可访问工作区外文件并执行命令。该选项约束 Codex 执行，不改变网页编辑器自身的保存权限。

## 共用界面与系统差异

- 会话、文件和 Git 列表共用固定侧栏，切换时不改变右侧编辑区与对话区尺寸。
- 拖动后的侧栏宽度和编辑/对话比例保存在浏览器中；双击分隔线恢复默认。
- 关闭当前文件优先选择右侧相邻标签，其次左侧；输入内容只更新未保存标记等状态。
- 文件接口使用正斜杠相对路径。Windows 绝对路径兼容反斜杠且不区分大小写；Linux 保留大小写区别。
- 编辑器支持不超过 2 MiB 的 UTF-8 普通文件，保留 BOM 和换行，保存前检查版本冲突。
- Windows 使用原有句柄校验和 Job Object；Linux 使用目录描述符、O_NOFOLLOW、flock 和进程组。两边都排除文件接口的链接越界。
- Git 只读。Linux 调用本机 Git；Windows 普通目录调用 Windows Git，WSL UNC 目录继续通过 wsl.exe 调用对应发行版的 Git。

## 开发与测试

前端资源在 frontend/vendor，启动服务无需 npm。只有重建 Monaco 资源时才运行 `npm ci` 和 `npm run build:editor`。

后端离线测试（在相应系统的虚拟环境内执行）：

```bash
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -p 'test_*.py'
node tests/workbench_interactions.cjs
```

浏览器测试需要 Playwright 和 Chromium，可用 CHROMIUM_EXECUTABLE 指定浏览器。未安装 Playwright 时跳过浏览器测试。Windows 专用进程测试在 Linux 跳过，Linux 专用文件/进程测试在 Windows 跳过。`tests/smoke_browser.py` 是手动的真实模型联调，会消耗额度，不属于离线测试。

第三方图标和 Monaco 许可见 frontend/vendor。
