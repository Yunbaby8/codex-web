# Codex Web

在自己的 Linux 上运行 Codex，通过浏览器聊天、编辑项目代码、查看 Git 差异、选择工作目录、模型和思考强度。无需 VS Code。每个人独立部署，使用自己的 Codex 登录、项目文件和会话数据。适合单个可信用户在内网使用。

## 依赖与独立部署范围

- 启动链路：`start.sh` → `backend/run.py` → FastAPI 后端 → `codex app-server`（stdio）。
- 源代码管理页面另外调用系统 Git，读取状态和差异。
- 不调用构建、日报、监控或账号切换脚本。Codex 执行用户任务时，仍可按任务要求运行服务器上的命令。
- 可选读取 `~/.codex-auth-snap/accounts/*.auth.json`，仅用于匹配账号显示标签；不会调用该工具。没有账号快照也能聊天、编辑和查询额度，标签可能为空。
- 共享当前 Linux 用户的 Codex 配置、登录与原生会话；这些由使用者自己的 Codex 环境管理，不包含在仓库中。
- `frontend/vendor/` 包含已生成的 Monaco 资源及第三方许可，必须保留；运行时无需安装 npm 依赖。
- Git 不跟踪本地 `config.yaml`、`codex-web.service`、`data/`、`.venv/`、`node_modules/` 或缓存。配置示例和服务模板可提交，真实密码、令牌及登录文件不可提交。

## 环境与安装

- Linux、Python 3.10+、已经登录且可工作的 Codex CLI。
- 当前实现曾在 Codex CLI **0.154.0** 下验证；App Server 协议和实验功能可能随 CLI 版本变化，升级后应重新检查兼容性。
- 后端运行依赖只有 FastAPI、Uvicorn、PyYAML；聊天与工作台使用浏览器原生模块，部署使用已生成的本地 Monaco 资源，运行时无需 npm 或 CDN。
- 全新环境安装：

```bash
git clone https://github.com/Yunbaby8/codex-web.git
cd codex-web
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Ubuntu 如果提示缺少 ensurepip，先安装与 Python 对应的 `python3-venv` 系统包；也可在已有依赖的 Python 环境中直接启动。`start.sh` 优先使用 `.venv/bin/python`，否则使用 `python3`。

检查 CLI：

```bash
codex --version
codex --help
codex app-server --help
codex login status
```

服务必须使用能正常运行 Codex CLI 的 Linux 用户、PATH、Codex 登录配置和网络代理，Web 不要求另填 API Key。使用自己已配置的 Codex 登录方式即可。用户级 systemd 服务不会自动继承交互终端的 `HTTP_PROXY`/`HTTPS_PROXY`；使用代理的环境必须在服务的 `[Service]` 中用 `Environment=` 显式配置，否则页面可能一直显示“正在工作”但没有输出。

## 配置与登录

新安装复制 `config.example.yaml` 为 `config.yaml`，填写以下配置：

- `username`、非空的 `password`：自己的网页登录凭据，与 Codex 账号密码无关。
- `allowed_roots`：自己的 Linux 项目目录，必须已存在。
- `allowed_origins`：浏览器实际使用的完整访问地址，包含协议和端口；从其他电脑访问时，加入服务器 IP 或域名。
- `codex_binary`：默认从 PATH 找到 `codex`；找不到时填 `command -v codex` 返回的绝对路径，并确保 PATH 能找到它所需的 Node。

密码仅保存在自己的 `config.yaml` 中，不写在源码、README 或日志中。

```bash
cp config.example.yaml config.yaml
# 用编辑器填写上述配置后再启动。
chmod 600 config.yaml
./start.sh
```

输出：`Codex Web listening on: http://0.0.0.0:5200`。

长期运行可安装用户级 systemd 服务。模板默认项目位于 `~/work/codex-web`；先根据实际安装位置、Node/Codex 的 PATH 和代理修改自己的服务文件：

```bash
mkdir -p ~/.config/systemd/user
cp codex-web.service.example ~/.config/systemd/user/codex-web.service
# 用编辑器检查并修改服务路径、PATH 和代理后执行：
systemctl --user daemon-reload
systemctl --user enable --now codex-web.service
```

服务状态和日志分别使用 `systemctl --user status codex-web.service`、`journalctl --user -u codex-web.service` 查看。服务文件中的 PATH 必须能找到当前安装的 Codex 和 Node；升级或移动后需同步更新。如需退出登录后仍运行或开机即启动，可由管理员为对应用户启用 systemd linger。

本机浏览器访问 **http://127.0.0.1:5200**；其他电脑访问 `http://服务器IP:5200`，并确保网络、监听地址、防火墙和 `allowed_origins` 配置对应。用自己的 `config.yaml` 中的用户名和密码登录。

运行配置默认取项目根目录下的 `config.yaml`；也可用 `CODEX_WEB_CONFIG` 指定其他配置文件。修改配置后重启服务。不要提交或分享 `config.yaml`、`data/`。

### 工作目录

`allowed_roots` 必须填写自己的目录，可配置多个绝对路径。页面只列目录；选中目录并点击 **New Session** 后生效。已存在 Session 绑定原来的 cwd，切换目录后必须创建新 Session，避免把项目上下文混在一起。

所有目录选择、新建/恢复会话、导入操作均验证真实路径，禁止 `../` 和指向根目录外的软链接绕过；指向允许范围内的软链接可以使用。

**这是目录浏览与 cwd 选择的范围限制，不是 Codex 整个进程的文件读取隔离。** Codex 默认使用 `workspace-write`：可读取 Linux 用户可读文件，并在工作目录内写入；需要额外权限时显示审批。以当前 Linux 用户权限为最终上限。单用户 MVP 不适合作为不同权限用户共用的终端服务。

### Model / Reasoning

默认 `models: []`，后台从 `model/list` 获取当前 CLI 返回的模型和每个模型支持的 effort，前端不硬编码选项。

也可按当前账号实际返回的模型和强度手动维护白名单。配置中的名称不代表服务端必然有访问权限；模型拒绝或额度错误会显示在页面。推荐保留 `models: []`，不复制其他人的模型配置。

### 登录与网络

- HttpOnly、SameSite=Strict Cookie；登录有效期 12 小时，后端重启需要重新登录，会话仍保留。
- 登录限速；写操作要求自定义校验头；Origin 必须属于 `allowed_origins`。
- 修改访问主机名或端口时，同步修改 `allowed_origins`。
- 按需求默认 HTTP，用户名密码和聊天内容在网络传输时不加密。仅在可信内网使用；需要加密时在前面加 HTTPS 反向代理，并设置 `cookie_secure: true` 和 HTTPS Origin。代理 SSE 时关闭响应缓冲、允许长连接。
- `sandbox` 默认 `workspace-write`，也可设为 `read-only`。设为 `danger-full-access` 时，后端同时使用 `approvalPolicy: never`，即取消文件系统、网络沙箱和操作审批；只应在完全信任的单用户环境中启用。
- 无需对外暴露 Codex App Server，它只通过本机 stdio 与后台通信。

## 使用流程

1. 登录，点击 Workspace 选择项目目录。
2. 选择 Model 和 Reasoning，点击 New Session。
3. 输入消息，Enter 发送，Shift+Enter 换行。模型左侧的“加速”按钮可切换 Fast 模式，选择会保存在当前浏览器；开启后速度更快，但会增加额度消耗，不支持的模型会自动禁用该按钮。点击“＋ 附件”多选图片或文件，也可拖入输入区、粘贴截图；发送前可移除附件，可仅发送附件。Codex 执行期间输入框仍可使用，此时发送的内容通过原生 `turn/steer` 引导当前任务，不会创建并发任务；成功接受的补充指令会立即显示为当前轮中的用户气泡，并写入 Web 事件记录供刷新恢复。页面按“首次消息 → 此前执行过程 → 补充引导 → 后续执行过程”分段展示，多次引导也保持先后顺序；刷新恢复时同样按消息顺序分段。
4. 逐步查看 Codex 消息和 Shell 输出；需要时批准/拒绝操作。Codex 需要你抉择或补充信息时，会自动弹出小窗口，显示问题、可选答案和选项说明，也可自行填写答案；点击“提交回答”后继续。Esc 或 × 可暂时收起，通过输入框上方“打开提问”重新打开，收起期间保留已填内容；收起不提交答案，也不停止任务。
5. Stop 调用原生 `turn/interrupt`。停止不能撤销任务已经完成的写入操作。
6. 左侧常驻会话栏自动合并 Web 会话与 Linux 上允许目录内的原生 Codex 会话，点击即可恢复，当前会话高亮，支持刷新和加载更多；“会话 / 归档”Tab 可切换查看未归档与已归档会话。当天新建的会话会在标题下显示“今日新开”，带附件时同时显示附件名，悬停可查看 Linux 文件路径。侧栏底部用 `additional`、`personal`、`business` 等短标签显示当前 `auth.json` 对应账号。标签通过当前 `auth.json` 的账号身份与 `~/.codex-auth-snap/accounts/*.auth.json` 配套文件匹配，不展示令牌、邮箱或附件路径。长标题悬停可查看完整标题、目录与会话 ID。

“新对话”下方的“界面皮肤”按钮会弹出皮肤列表，可以即时切换“科技蓝”和类似 VS Code Dark+ 配色的“经典黑”；选择保存在浏览器 localStorage 中，刷新或重新登录后继续使用。旧版保存的 `graphite` 皮肤会自动迁移到“经典黑”。

#### 皮肤开发约束

所有皮肤必须共用同一套 DOM、布局和字体规则，切换皮肤时控件、标签页、文字和图标的大小及位置不能变化，只允许配色不同。新增或修改皮肤时遵守以下规则：

- 在 `frontend/index.html` 增加皮肤菜单项，在 `frontend/workbench.js` 注册皮肤键。
- 通用布局、尺寸和字体只能写在 `frontend/style.css` 的基础规则中，不能写进任何 `html[data-theme="..."]` 主题块。
- 主题块只允许覆盖 CSS 颜色变量，以及 `color`、`background`、`border-color`、`box-shadow`、`text-shadow`、`scrollbar-color` 等不参与布局的绘制属性。
- 主题块禁止覆盖 `font`、字号、字重、行高、字间距、`display`、定位、宽高、内外边距、间距、边框宽度、圆角、变换和动画等会改变排版、尺寸、位置或交互节奏的属性。
- 新皮肤完成后必须在相同窗口尺寸下往返切换，确认侧栏、会话标签、输入区、下拉框、按钮和消息区没有位移或尺寸变化，并运行前端主题约束测试。

`tests/test_frontend.py` 会扫描所有 `data-theme` 规则；主题中出现布局或字体属性时测试会失败。

如果在服务运行期间用 `codex-auth-snap` 或其他方式切换了 `~/.codex/auth.json`，Web 服务会检测文件替换并只重启本机 `codex app-server` 子进程，然后重新读取模型列表。没有任务时立即重载；仍有任务执行时等待该任务结束再重载，FastAPI、浏览器连接和会话数据库不会重启。左下角 **重载 Codex** 仍可用于手动重载。浏览器继续使用本项目 `config.yaml` 中的本地用户名和密码，不需要登录 ChatGPT 网页或使用官方 Remote 配对。

执行期间，回答区按 VS Code Codex 插件的布局显示当前轮时间线：顶部持续更新“已处理”时长，App Server 标记为 `commentary` 的进度文字、Reasoning、Shell 命令和文件修改按到达顺序直接排列，命令输出与文件 diff 可点击展开，底部显示“正在思考”。`final_answer` 到达后显示在时间线末尾。执行过程提供统一折叠入口；正常完成且用户仍跟随最新消息时自动收起，正在回看或手动展开时保留当前状态，失败或停止时展开供检查；任务完成或停止后隐藏“正在思考”。系统开启“减少动态效果”时，思考提示改为静态文字。旧会话中没有阶段标记的回答继续兼容显示。正式回答支持标题、列表、表格、引用、行内代码和围栏代码块；代码块对常用的 C/C++、Python、JavaScript/TypeScript、Shell、JSON、YAML 和 diff 提供安全的本地语法高亮。回答开头误写的“本轮新增读取：”或“本轮新增核对：”会统一显示为规则要求的“本轮已读：”。回答操作栏支持复制、浏览器朗读、本机赞踩标记和带确认提示的再次运行，代码块也可单独复制。同一轮的所有文件改动合并显示，同一路径只显示一次，多个修改片段合并到该文件的聚焦 diff 中。HTTP/HTTPS URL 和 Markdown 文件链接显示为蓝色链接，并在新标签页打开。Linux 文件链接只能打开 `allowed_roots` 内的普通文件，真实路径和软链接都会重新检查，不能借链接访问允许目录外的文件。执行中和历史会话都会显示 App Server 返回或计算得到的处理时长。

同一 Session 同时只运行一个任务，但执行期间可以多次发送引导指令；其他 Session 可以独立执行。切换 Session 或关闭浏览器不会停止后台任务。不要让多个会话同时修改相同文件；不要同时在 CLI 和 Web 操作同一个原生会话。

阅读时上滚会暂停跟随新内容，点击“↓ 最新消息”恢复；切换会话返回原阅读位置。流式回答保留未变更的正文节点和代码块滚动位置。发送期间继续编辑的文字不会被发送成功响应清空，输入框随文字增高，最高 240px。顶部菜单按钮可收起侧栏，窄窗口默认收起。

## 图片与文件附件

- 每条消息最多 8 个附件，每个不超过 20 MB；上传按钮、拖放和粘贴图片均可使用。
- PNG、JPEG、WebP、GIF 根据文件头识别为图片，通过 Codex 原生 `localImage` 输入。其他格式作为普通文件交给 Codex 读取；文档能否解析取决于 Linux 上可用工具，不承诺所有二进制格式都能直接解析。
- 附件保存在 `data/uploads/<随机ID>/`，不写入项目代码目录。原文件名保存为元数据；后台提供真实 Linux 路径供当前会话读取。
- 上传、读取附件均要求登录，且附件绑定会话；其他会话不能引用该附件 ID。附件下载不开放静态目录。
- 已发送附件保留在 Linux，刷新后会话仍可引用。待发送附件仅保存在当前页面，刷新会丢失；失败后保留待发送列表供重试。
- 删除待发送项只是取消发送。已上传文件在会话保留期间不会自动删除，避免破坏已有引用；永久删除整个会话时会一并清理其附件。备份 `data/` 时包含附件。

## 会话保存与恢复

- Codex 本身负责保存真实上下文，使用启动用户的 Codex Home（默认 `~/.codex`）。本服务不复制 API 凭据。
- `data/sessions.sqlite3` 保存 Web 会话索引、模型选择及事件记录；SQLite 使用 WAL。
- 浏览器 localStorage 保存当前 Session ID、皮肤、桌面侧栏偏好及已有的回答反馈；同一标签页的 sessionStorage 按会话保存文字草稿、阅读位置和过程展开状态，刷新后可恢复，关闭标签页后不保证保留。待发送附件在切换会话时保留在各自草稿中，刷新后需重新添加。刷新或切换会话时优先通过 `thread/read` 一次性加载完整快照，再从快照对应的事件游标开始接收 SSE 新增事件，避免把历史文本增量逐条重放；遇到不支持 `list_turns` 的会话来源时，自动改用最近一次 Web 历史快照及后续事件并在首次绘制前同步恢复。SSE 后续断线重连仍通过事件序号补发，避免漏掉输出。
- 服务重启后调用原生 `thread/resume` 继续对话；重启前的执行任务会被终止，不会自动重跑。
- 导入原生会话通过 `thread/read` 检查 cwd，再执行 resume；不扫描或解析 Codex 内部会话文件。
- 空会话在第一次发送前可能尚未被 CLI 持久化；若重启后该空会话无法恢复，创建新 Session 即可。
- 会话首次发送后保留初始标题，后续追问不再覆盖；菜单支持重命名，标题保存在 Web 会话索引中，不同步修改原生 CLI 标题。侧栏搜索只匹配当前列表已加载的会话标题，可加载更多后继续搜索。会话菜单同时支持归档和永久删除，归档会话可从左侧“归档”Tab 查看；点击永久删除后立即执行，不再二次确认。如果 Codex 原生会话记录已经不存在，删除按幂等成功处理，并继续清理 Web 索引、事件和附件。备份时停服务，备份 `data/` 及用户自己的 Codex 会话存储；只备份 Web 数据库不能还原完整模型上下文。

## 实现结构与协议

```text
Windows Browser
  ├─ HTTP：登录、目录、会话、发送、停止、审批
  └─ SSE：持续事件推送 / 断线补发
        ↓
FastAPI（单进程，0.0.0.0:5200）
        ↓ asyncio stdin/stdout：逐行 JSON RPC
codex app-server --listen stdio://（常驻 Linux 子进程）
        ↓
Linux workspace / shell / filesystem
```

后端按 HTTP 接口、会话业务和进程通信分工：

| 文件 | 职责 |
| --- | --- |
| `backend/main.py` | HTTP、认证、SSE，以及应用生命周期；接口调用会话方法，不直接操作 RPC 或业务锁 |
| `backend/codex_manager.py` | 会话创建、导入、恢复、归档、删除，任务与审批状态，账号重载，以及事件写入 |
| `backend/codex_client.py` | 常驻 App Server 子进程、初始化握手、逐行 JSON RPC、请求与回复配对、通知分发和进程回收 |
| `backend/session_manager.py` | SQLite 会话索引及事件记录 |
| `backend/workspace.py`、`editor_files.py`、`git_view.py`、`attachments.py` | 工作区边界、文件读写、只读 Git 和附件 |
| `backend/config.py` | 配置读取；`backend/run.py` 和 `start.sh` 仍是启动入口 |

`create_app()` 只组装路由和中间件，导入 `backend.main` 不读取登录配置、不打开数据库，也不启动 Codex。配置、数据库、附件目录和 Codex 实例在服务启动阶段创建，保存在该应用的 `app.state` 中；退出时停止账号监控和子进程，再关闭数据库。启动失败同样清理已创建的资源。接口测试可传入临时配置并进入真实应用生命周期，无需反复动态加载模块。

历史快照的事件游标在 RPC 回复被读取时同步捕获，先于下一条通知，保持快照与后续 SSE 补发的边界。已取得执行编号的 Stop 仍直接发送中断，不等待额度查询等业务操作；执行编号尚未返回时保留启动与停止的先后顺序。

前端通过 `index.html` 中的 `type="module"` 加载 `app.js`，使用原生 ES 模块，不增加聊天页面的打包步骤。`app.js` 只负责创建各功能模块、连接回调和启动页面。

| 文件 | 职责与状态归属 |
| --- | --- |
| `frontend/sessions.js` | 登录、账号信息、工作区选择、会话列表、当前会话、运行状态及 SSE 连接；负责切换会话与拒绝过期响应 |
| `frontend/conversation.js` | 消息时间线、Markdown、审批与提问、上下文用量、阅读位置；内部保留消息和页面节点映射 |
| `frontend/composer.js` | 模型与思考强度控件、发送和引导、再次运行、文字与附件草稿、发送中的状态 |
| `frontend/workbench.js` | 主题、活动栏、面板显隐、分隔条拖动；统一修改布局并同步按钮状态 |
| `frontend/editor.js` | Monaco 文件模型、未保存内容、文件树、快速打开及 Git 对比；通过工作台接口显示编辑器 |
| `frontend/ui.js` | 共用的 DOM 查询、HTTP 请求和错误显示；401 响应通知会话模块回到登录界面 |

功能模块通过明确的回调与方法协作，不直接修改其他模块的私有状态。布局不再依赖多个模块监听 CSS 类变化互相同步；Monaco 的主题监听仍保留。`window.codexEditor` 是保留的编辑器联动入口，其他内部状态不放在浏览器全局变量中。修改这些源文件后刷新页面即可加载，无需重新生成 Monaco 资源。

调用顺序：`initialize` → `initialized` → `model/list` → `thread/start` 或 `thread/resume` → 多次 `turn/start`。执行中补充指令使用 `turn/steer`，并携带当前 `expectedTurnId`；停止用 `turn/interrupt`。模型作为 `thread/start.model` / `turn/start.model`，思考强度作为 `turn/start.effort` 传入；Fast 选择使用模型列表返回的 tier id，通过 `turn/start.serviceTierForTurn` 仅作用于新启动的当前轮。

stdin/stdout 是结构化消息，不是终端画面；stderr 独立持续读取至服务日志，避免管道堵塞。Shell 输出中的 ANSI 控制序列在显示时清除；Markdown 渲染只创建受控 DOM 节点，文本仍通过 `textContent` 写入，不接受模型输出的原始 HTML。异步进程不受单次 HTTP 请求寿命影响；关闭服务会回收其进程组。

常见命令/文件审批和 `requestUserInput` 有页面交互；未实现的原生客户端请求明确返回“不支持”，不会无限等待，也不会自动批准。App Server 是实验接口；后续可增添更多请求类型和更深的编辑器联动，无需更换会话架构。

选择弹窗需要 Codex 实际调用 `request_user_input`；普通文字中的提问或选项不会自动变成弹窗。Web 启动 App Server 时通过 `--enable default_mode_request_user_input` 开启普通对话的提问工具，并在初始化时声明 `experimentalApi` 能力；此配置仅作用于 Web 子进程，不修改全局 Codex 配置。启动配置修改后须重启 Web 后端，单纯刷新页面或点击页面“重载”不会加载修改后的 Python 代码。

同时支持 `request_user_input_async`：App Server 将问题放在 `agentMessage.questions` 中，Web 将其显示为相同的选择弹窗。异步提问不会阻塞模型，当前轮结束后仍可回答；答案在任务运行中通过 `turn/steer` 发送，任务结束后通过 `turn/start` 继续对话。未回答的问题可从会话历史恢复，已提交的答案不会在刷新后再次弹出。

协议依据：[Codex App Server 官方文档](https://learn.chatgpt.com/docs/app-server)；实现字段同时依据本机 `codex app-server generate-json-schema --out <临时目录>` 生成的协议。

## 检查与测试

离线检查无需启动真实 Codex，也不会执行真实模型任务。

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q backend
for file in frontend/*.js; do node --input-type=module --check < "$file" || break; done
```

`tests/smoke_browser.py` 是可选的真实 Chromium/模型测试，需另装 Playwright 和 Chromium，不属于运行依赖。对已启动服务测试登录、目录选择、实际 shell cwd、多轮聊天、刷新、Stop 和列表。它会创建测试会话、消耗少量模型额度，默认使用 `gpt-5.6-sol / medium`。

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
python3 tests/smoke_browser.py
```

可通过 `CODEX_WEB_TEST_URL` 指定地址，通过 `CHROMIUM_EXECUTABLE` 使用已安装浏览器。

## 验证范围

离线测试覆盖登录与接口、路径边界、会话保存、模拟 Codex 通信，以及浏览器中的编辑、Git 差异、阅读位置和流式渲染。HTTP 测试需安装 httpx，浏览器测试需安装 Playwright 和 Chromium；缺少可选依赖的测试会跳过。真实 Codex 登录、模型权限、审批和跨电脑网络访问，需要在部署者自己的环境中验证；离线测试通过不能证明这些链路可用。

## 常见问题

- **Windows 打不开**：确认 VM 运行中、监听地址为 `0.0.0.0:5200`，Windows 到该 IP 可达，虚拟机防火墙允许 TCP 5200。本机测试不能证明 Windows 网络链路已畅通。
- **端口已占用**：检查是否已有 Web 服务运行；使用 systemd 时，可用 `systemctl --user status codex-web.service` 查看状态。
- **找不到 codex**：用已登录 Codex 的同一用户和 PATH 启动，或在 `codex_binary` 配置实际绝对路径。
- **登录失败**：检查本地配置，不是 Codex 账号密码。服务重启后重新登录。
- **Codex 无输出/网络报错**：先确认同一终端中 CLI 能正常对话、代理环境正确、账号额度有效；页面错误和启动终端 stderr 提供诊断信息。
- **切换 auth.json 后仍使用旧账号**：通常等待最多一秒；若当时正在执行任务，会在任务结束后自动重载。也可以 Stop 后点击左下角“重载 Codex”。已有会话索引不会删除，下一次打开时由新的 App Server 恢复。
- **App Server 退出**：页面会显示连接错误，重启 Web 服务后恢复原 Session。MVP 不自动重试模型任务，避免重复执行操作。
- **执行中等待许可**：查看输入框上方审批区域；选择允许本次或拒绝。Stop 可以中断当前任务。
- **执行中补充要求**：直接在输入框发送，页面会通过 `turn/steer` 引导当前任务；某些不可引导的特殊任务会显示 App Server 返回的错误，此时等待完成或 Stop 后重新发送。
- **刚改目录却仍是旧项目**：已有会话 cwd 固定，请点击 New Session。
- **网页与编辑器仍有差异**：网页已支持常用 Markdown、代码高亮和聚焦 diff，但没有 VS Code 编辑器提供的语义分析、行内诊断、暂存、撤销及源码编辑能力。
- **升级协议不兼容**：以本机 help/schema 为准检查 `codex_client.py` 的通信及 `codex_manager.py` 的业务字段，重新执行测试。


## 项目代码编辑器（Monaco 第一版）

界面采用 VS Code 风格的标题栏、活动栏、侧栏、文件标签和状态栏，默认使用纯黑主题（主背景与 Monaco 背景为 #000000）。左侧活动栏的“资源管理器”和“会话”切换对应侧栏，中间为 Monaco，右侧为 Codex 对话；只展示已实现功能。点击顶部工作区路径会在其下方展开目录面板，可切换根目录、浏览子目录并点击“使用此目录”；点击外部或按 Esc 收起，不弹出独立窗口。文件树跟随顶部所选 Workspace（选择新目录不会修改既有聊天会话的 cwd，新对话才使用新目录），按需展开。资源管理器和会话列表各自的右侧分界线均可左右拖动调宽，两者共享同一个宽度，切换侧栏时沿用最近调整的宽度；编辑器与对话之间也可拖动调宽；窄屏默认收起代码区，通过资源管理器入口展开。编辑器顶部、与 Codex 的分界线左侧提供显示／隐藏 Codex 对话按钮，悬停显示操作名称，填充区域表示对话面板的显示状态；隐藏后保留编辑和聊天内容，按钮继续保留在编辑器右上角（窄屏仅显示聊天时移到聊天标题栏）。会话和文件继续使用活动栏原有入口。底部显示任务状态及当前文件的行列位置、编码、换行符和语言；活动栏下方可切换颜色主题，窄屏时相关按钮移到标题栏。

点击文件打开 Tab，支持行号、折叠、自动缩进、Ctrl+F 查找、Ctrl+S（macOS 为 Cmd+S）保存。未保存文件带圆点；切换 Tab 保留内容、撤销历史和光标，关闭 Tab 释放 Monaco model。切换工作区保留各工作区的打开文件和草稿；刷新/关闭网页会提示未保存内容，但草稿不跨页面重载持久化。重新加载会先确认是否放弃编辑。

在 Monaco 中选中代码，点击工具栏或右键菜单的“询问 Codex”，会将工作区、文件相对路径、行号和当前选区原文追加到对话草稿。未保存内容会明确标注，支持多个选区；保留原有草稿和附件，由用户补充问题后手动发送。工作区与当前会话不一致时提示切换会话，避免误发。

按 Ctrl+P（macOS 为 Cmd+P），或点击编辑器工具栏的“打开文件…”，按文件名、相对路径或不连续字母筛选。上下键选择，Enter 打开，Esc 关闭；支持 `src/main.cpp:120` 和 `src/main.cpp:120:5`。打开已有 Tab 保留未保存内容。

快速打开只扫描当前工作区的文件名，沿用文件树的忽略目录规则，不跟随符号链接。每次打开重新读取列表；扫描最多 2 秒、100000 个目录条目、20000 个文件，深度最多 40 层。达到上限或部分目录无法访问时会明确提示；匹配结果显示前 100 个，可继续输入缩小范围。


支持 C/C++、Python、Shell、YAML、JSON、Markdown、Makefile、BitBake（`.bb` / `.bbappend` / `.inc` / `.bbclass`）和 systemd 文件的基础语法着色。Makefile 使用 Tab 缩进。BitBake、Makefile、systemd 为本地 Monarch 着色规则，不提供语言服务器、跳转定义或补全。

C/C++ 增强配色：类型名称为青绿色、函数为黄色系、成员为蓝色、宏为紫色；七套皮肤自动使用深浅两组文字颜色。沿用 Monaco 对注释、普通／原始字符串、预处理指令的识别。根据 class／struct／union／enum 声明、调用括号、成员访问、命名习惯（大写开头、`_t`、全大写）提供着色提示；没有类型分析，宏／构造函数／自定义类型的个别用法可能无法准确区分，不提供 LSP 能力。

### 文件接口与限制

- `GET /api/editor/tree?workspace=...&path=...`：列出相对目录，根目录 path 为空。
- `GET /api/editor/file?workspace=...&path=...`：返回 UTF-8 内容及 revision。
- `PUT /api/editor/file`：JSON 字段为 `workspace`、`path`、`content`、打开/上次保存返回的 `revision`。
- 复用登录 Cookie、来源限制和 `X-Codex-Web: 1` 校验；workspace 必须在配置的 allowed_roots 内，文件路径必须相对于该 workspace。目录句柄逐层打开并拒绝符号链接、绝对文件路径、`..` 和设备/FIFO 等特殊文件。
- 支持最大 2 MiB 的 UTF-8 普通文本（含 BOM）；拒绝二进制及其他编码。一次目录枚举最多 10000 项。`config.yaml` 的 `editor_ignored_names` 可覆盖默认忽略名称；示例见 `config.example.yaml`，修改后重启后端。
- 保存前核对内容摘要、inode 和时间戳，冲突返回 HTTP 409 并保留编辑内容。可先复制自己的修改，再点“重新加载”查看磁盘版本，不提供强制覆盖。保存直接写原文件，保留权限；本服务内并发保存使用文件锁。外部程序不一定遵守文件锁，因此此机制不是跨程序事务锁，无法完全排除检查之后同时写入的极短竞争窗口。

### 本地静态资源

锁定 Monaco 0.57.0 和 esbuild 0.28.2，使用 ESM。`frontend/vendor/` 包含已生成的编辑器、worker、CSS、字体和许可证，运行时不需要 npm 或外部 CDN。修改 `frontend/editor-entry.js` 或升级依赖后，在项目目录执行 `npm ci`，然后 `npm run build:editor` 重新生成资源；不会构建 OpenBMC。部署时需携带完整 vendor 目录，后端接口变更需重启已有 Web 服务。

Monaco 动态注入布局及着色样式，因此 CSP 允许内联样式；脚本和 worker 仍仅允许同源，未开放内联脚本或 eval。

### 后续 Codex 联动入口

`window.codexEditor.openFile(relativePath, {line: 10})` 打开当前工作区文件并定位行；`refreshFile(relativePath)` 刷新已打开且未修改的文件，遇到未保存编辑则提示并保留；`refreshTree()` 刷新目录。现有 `setWorkspace` 发出 `codex:workspace` 事件，保存成功发出 `codex:file-saved`，detail 为 `{workspace, path}`。model URI 使用 `workspace://<编码后的工作区绝对路径>/<真实相对路径>`，避免不同或嵌套工作区之间 URI 冲突。

聊天中的工作区文件链接已连接到编辑器；Codex 文件修改完成、任务结束以及切换会话时，会刷新对应工作区的文件树、Git 状态和已打开文件。未保存的编辑会保留并提示磁盘变化；尚未加入后台文件监听。

测试：`python3 -m unittest discover -s tests -p 'test_*.py'`。浏览器测试需另装 Playwright 和 Chromium；HTTP 鉴权测试需 httpx。测试使用临时文件、模拟 Codex 数据和本地静态资源，不调用真实模型或修改生产项目文件。

图标来源：“聚焦 Codex 对话”使用 OpenAI 官方白色 Blossom SVG，原始素材来自 https://cdn.openai.com/brand/OpenAI-Logos-2025.zip ，使用说明见 https://openai.com/brand/ 。图标及相关商标归 OpenAI 所有。


## 只读源代码管理（Git 第一版）

左侧活动栏的分支图标打开“源代码管理”，沿用文件树和会话列表共享的侧栏宽度。显示当前分支（分离 HEAD 时显示提交短哈希），按“已暂存的更改／更改／未跟踪的文件”分组，文件旁显示 M、A、D、R、? 等 Git 状态。点击右上角刷新按钮重新读取列表；工作区切换后读取对应仓库，也会随 Codex 文件修改完成和任务结束刷新。

点击文件，在 Monaco 中只读左右对比：已暂存为 HEAD 对比暂存区；未暂存为暂存区对比磁盘；未跟踪为新文件对比空白。删除文件的一侧为空。同一文件暂存后又修改，会同时出现在两组中。对比使用磁盘快照，不包含网页编辑器里尚未保存的草稿。点击差异视图右上角 × 或已打开文件的 Tab 返回编辑器，原草稿和撤销历史保留。

后端只提供 `GET /api/git/status?workspace=...` 和 `GET /api/git/diff?workspace=...&path=...&group=staged|unstaged|untracked`，复用原有登录、来源和工作区边界校验；没有仓库写操作接口，不暂存、提交、切换分支、推送或丢弃修改。Git 使用参数数组启动，不调用 shell；禁用可选索引更新、fsmonitor、clean/process filter，不执行 textconv。对于依赖内容转换过滤器的仓库，第一版按原始内容读取，状态可能与启用过滤器的 Git 客户端不同。

支持普通仓库、linked worktree、尚无提交的新分支以及选定仓库的子目录。仅显示当前工作区范围内的改动；如果重命名前的路径在工作区之外，拒绝读取该对比。非 Git 工作区显示明确提示，不初始化仓库。合并冲突只列出，不提供冲突处理；符号链接、子模块、非 UTF-8、二进制及单侧超过 2 MiB 的文件提示无法对比。每条 Git 命令最多运行 15 秒，状态输出限 4 MiB、改动列表限 10000 项。刷新和对比不是跨程序事务快照；若其他程序同时改动仓库，请刷新重试。

后端测试在临时仓库覆盖不同暂存状态、增删、重命名、特殊文件名、worktree、分离 HEAD、空仓库、冲突及越界访问，并核对查看前后 index 内容／时间戳、HEAD 与磁盘文件不变；浏览器测试覆盖分组、只读对比、刷新、模型释放及草稿保留。运行方式沿用上述 unittest 命令。


### 账号与额度

顶栏右侧始终显示当前加载的账号标签和“重载”。点击账号展开额度面板，通过 App Server 的 `account/rateLimits/read` 读取 Codex 通用额度，按窗口时长识别 5 小时和每周额度，显示剩余百分比、浏览器本地时间的重置时间及更新时间。每次展开或点击“刷新额度”会重新查询；刷新额度不会重载 Codex。缺失数据或查询失败显示“暂不可用”，不当作 0%。账号配置变更但尚未重载时暂停额度展示，避免账号混淆。
