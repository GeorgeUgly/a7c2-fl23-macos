# Mac 给 A7C II 安装 FL2 / FL3

适用于 **Sony A7C II（ILCE-7CM2）、固件 2.01、macOS**。当前工具版本为 **0.2.0**，提供 FL2/FL3，以及 FL2/FL3＋跳过 NTSC 开机提示两种选择。

本项目直接基于 **[QAQLeonard/a7c2-fl23-research](https://github.com/QAQLeonard/a7c2-fl23-research)** 做 Mac 适配。FL2/FL3 移植与基础安装工具的贡献归原作者及其引用的研究者；[贡献来源](#贡献来源)和[完整致谢与许可](ACKNOWLEDGEMENTS.md)保留在本文及仓库中。

> **当前仍是实验版本。** 2026-10-19，跳过 NTSC 提示的组合版已在一台机身上从旧 FL2/FL3 追加升级，经专项 WBI 收尾后通过冷启动完整程序、参数和9处运行区域核验；使用者确认 ST/FL2/FL3 新照片拍摄回放、FL2/FL3 存储卡 **M1–M4 保存调出**及 NTSC 开机提示跳过均正常。升级途中出现未知字段变化，需使用者明确接受风险后专项处理，不能视为通用升级已无问题。默认保留提示版的实机升级、两类首次安装和恢复仍未验证；相机可能无法正常开机，备份不保证能救砖。首次安装的已知中断原因尚未根治，**建议首次安装用户暂缓尝试**。[查看验证范围](docs/VALIDATION.md) · [新版升级停止点](docs/UPGRADE-FINISH.md)

## 先选版本

| 版本 | FL2/FL3 | NTSC 开机提示 | 准备时怎样选择 |
| --- | --- | --- | --- |
| **FL2/FL3（默认）** | 加入 | 保留 | 不加额外选项 |
| **FL2/FL3＋跳过 NTSC 提示** | 加入 | 使用新版目标提示跳过补丁 | 加 `--disable-ntsc-warning` |

**两个版本都加入了针对以下问题的修复：FL2/FL3 保存到存储卡 M1–M4 后，重新调出时提示“文件错误”。** 不需要再单独选择“预设修复版”。

### 这次具体改了什么

- **处理 FL2/FL3 卡上预设调出报“文件错误”的问题。** 收到的反馈是：存入机身的 1/2/3 可以调出，存入存储卡的 M1–M4 后却报错。我们发现，旧补丁加入了 FL2/FL3，但另一处参数检查仍不接受它们使用的新外观绑定值；现在补齐这处检查，只修改两个字节。**离线原生代码测试通过后，2026-10-19 使用者在本次组合版升级的同一台机身上确认 M1–M4 保存调出均正常。** 尚未取得原始报错预设文件，也没有其他机身验证；这一结果不能代表所有旧预设文件都已验证兼容。
- **收窄 NTSC 提示跳过的影响范围。** 新补丁保留原有制式和显示判断，只在程序已经选中目标 NTSC 提示时跳到正常后续路径。它不会主动把相机切换成 NTSC；PAL/NTSC 切换及拍摄仍需实机检查。
- **补齐两类版本的使用流程。** 未改装机身走首次安装；已装旧版 FL2/FL3 的机身走独立升级。两种新版本都接入了完整程序、运行状态验证和原版恢复。
- **增加升级已写完程序后的受限收尾。** 遇到本次明确的设置检查停止点时，可保留原证据、重新核验后只处理启动快照，不把已改装机身当作原版重刷。未知字段的额外时间例外必须由使用者另行明确选择；不是默认忽略报错。[适用条件与风险](docs/UPGRADE-FINISH.md)

技术细节、固定程序指纹和离线报告见[两类版本的研究记录](research/two-profiles/README.md)。

## 你应该走哪条流程

| 相机现在的情况 | 应该怎么做 |
| --- | --- |
| 从未改装，仍是原版 2.01 | 阅读风险后，按[首次安装](#首次安装)进行备份、预检查、安装和验证 |
| 已装旧版 FL2/FL3，且保留已完成冷启动验证的工作目录 | 按[旧版升级](#旧版升级)创建独立新目录；不要重复首次安装 |
| 已装本项目 0.2.0 的其中一种版本 | 使用对应工作目录验证；不要重装，也不要手改状态切换版本 |
| 已装旧版 NTSC 组合、其他改装，或没有完整备份、上次操作未完成 | 当前不支持直接升级；保留已有目录，先查看[状态与报错](#状态与报错) |

这两类版本面向通过校验的 ILCE-7CM2 2.01，**不是某一台相机专用的固件**。完整程序指纹用于确认公共固件代码的版本；机身身份用于确保读取、安装和恢复对应自己的设备。不能拿其他机身的备份替代自己的备份。

## 使用条件

| 项目 | 要求 |
| --- | --- |
| 相机 | **Sony A7C II / A7C2，型号 ILCE-7CM2**；不适用于 A7CR 或其他型号 |
| 固件 | 首次安装须为**原版 2.01**，且完整程序指纹匹配；只看菜单版本号不够 |
| 电脑 | Mac；旧版实机验证环境为 MacBook Air M4 / arm64，其他芯片尚未验证 |
| 系统 | 旧版实测 macOS 27.2；最低兼容版本尚未确定，不要求为此升级系统 |
| Python | **3.10 或更新版本**；实测 3.12.14 |
| 连接与供电 | 能传数据的 USB 线，相机直连 Mac，Mac 接电；写入前相机实际电量至少 **50%** |
| 存储卡 | 能正常拍摄，用于安装后的预设保存、调出和试拍检查 |

工作目录必须放在项目仓库外。首次准备默认在该目录的 `original/` 保存**一份原始备份**，不要删除、覆盖或公开上传整个工作目录。

## 下载工具

以下命令在 Mac 的「终端」运行。先用 `python3 --version` 确认版本，再下载和准备：

```sh
git clone https://github.com/MDHan/a7c2-fl23-macos.git
cd a7c2-fl23-macos
./setup.sh
```

`setup.sh` 下载并核验固定版本依赖，**不加 sudo，不连接相机**。成功时会显示：

```text
固定版本上游下载与全部文件哈希核验通过；未连接相机。
套件源码和资产完整；未连接相机。
```

已有仓库时保留本地改动，核对后再更新；不要为了重新开始而删除旧仓库或相机工作目录。后续命令均在 `a7c2-fl23-macos` 目录执行，只复制 `sh` 命令框，不复制输出示例。

如果希望由本地 AI 帮忙执行，可跳到[让本地 AI 帮你操作](#让本地-ai-帮你操作)。

## 连接与冷启动

**连接：**相机用 USB 直连 Mac，选择 **第 2 项「影像传输（MSC）」**。不选实时流式传输、MTP 或电脑遥控；后续再次出现连接选择时仍选 MSC。关闭正在浏览相机存储卡的窗口。

**冷启动：**在工具明确完成、相机无错误且存取灯熄灭后，拔 USB、正常关机，取出电池约 **10 秒**，再装回开机。随后重新连接 MSC。`--cold-start-confirmed` 表示你实际完成了这些动作，工具不会替你断电。

**写入尚未结束、报错或结果未知时，不要直接拔线或取电池。** 先按[状态与报错](#状态与报错)核对。

## 首次安装

仅适用于**未改装的原版 2.01**。首次安装已知中断问题尚未根治，以下是实验操作说明；已安装用户请走[旧版升级](#旧版升级)。

### 1. 检查连接

按上文连接 MSC，运行：

```sh
./a7c2-mac usb-status
```

输出中的 `media` 应列出相机磁盘，`camera_commands_sent: false` 表示只读取了电脑端设备信息。磁盘编号因电脑而异；若 `media` 为空，先排查，不继续。此步不等于固件版本或备份已核验。

### 2. 选择版本，备份并做机内预检查

下面**二选一，只执行所选的一条**。`A7C2-work` 必须尚不存在，父目录须存在。

```sh
# FL2/FL3：保留 NTSC 开机提示
sudo ./a7c2-mac prepare --workspace "$HOME/Pictures/A7C2-work"
```

```sh
# 或者：FL2/FL3＋跳过 NTSC 开机提示
sudo ./a7c2-mac prepare --workspace "$HOME/Pictures/A7C2-work" --disable-ntsc-warning
```

两个版本都包含前文所述的卡上预设“文件错误”修复。选择会保存在工作目录中，后续安装、验证和恢复自动沿用，不再重复加版本选项。

此步会读取原始程序和设置、保存并核对备份、生成候选，并运行临时机内诊断模块，**尚未写入固件或保存拍摄设置**。看到 `Password:` 时，在终端输入 Mac 管理员密码；没有字符或星号显示是正常现象，不要把密码发给他人。

**只有命令正常结束并输出 `RESULT: prepared`，才进入下一步。** “备份及候选已核验”只是中间输出。若目录已存在，保留它并先用 `status` 查看，不能删掉重来。

### 3. 明确执行安装

复制整段，按提示输入相机屏幕显示的**当前实际电量整数**：

```sh
(
  printf '相机当前实际电量（50–100，只输入数字）：'
  read -r A7C2_BATTERY
  sudo ./a7c2-mac install --workspace "$HOME/Pictures/A7C2-work" --battery-percent "$A7C2_BATTERY"
)
```

写入期间保持连接，不关机，不并行运行其他相机工具。“完整读回”是检查过程，不能据此认定安装完成。相机询问连接方式时选择 MSC，等待最终结果：

```text
RESULT: installed_pending_cold_start
```

该结果表示本轮安装和读回完成，**接下来还要冷启动**。没有明确完成结果、出现 `STOPPED` 或结果带 `_usb_unconfirmed` 时，先读[状态说明](docs/STATE-MACHINE.md)，不要自行重跑。

### 4. 冷启动、试拍和电脑端验证

按[连接与冷启动](#连接与冷启动)完成断电重启，然后：

- 查看 FL2/FL3 菜单，分别使用相机内建重设；用 ST、FL2、FL3 各拍新照片并回放。
- 测试把 FL2/FL3 保存到存储卡 M1–M4，重新调出并试拍，检查是否仍出现“文件错误”。
- 选择跳过提示版本时，检查 NTSC 下完整断电开机是否跳过提示，并检查 PAL/NTSC 切换、原有帧率、录像与回放。

重新连接 MSC，运行电脑端验证：

```sh
sudo ./a7c2-mac verify --workspace "$HOME/Pictures/A7C2-work" --cold-start-confirmed
```

成功结果为 `RESULT: verified_after_cold_start`。它检查完整程序、当前/保存设置及运行区域；人工试拍结果另行确认。**两个版本都需要这一步，菜单出现或试拍正常不能替代程序核验。** 完成后保留整个工作目录，日常使用无需反复安装。

## 旧版升级

仅适用于**已装旧版 FL2/FL3**，且原工作目录为 `verified_after_cold_start`、八项原始备份和候选齐全的情况。旧版 NTSC 组合、其他改装和未知阶段暂不支持直接升级。[完整升级说明](docs/UPGRADE.md)

### 1. 离线准备独立升级目录

下面假设原目录是 `A7C2-old-work`，请换成你自己的路径；新目录 `A7C2-upgrade` 必须尚不存在。这一步不加 sudo、不连接相机，原目录保持不变。

同样**二选一**：

```sh
# FL2/FL3：保留 NTSC 开机提示
./a7c2-mac prepare-upgrade \
  --source-workspace "$HOME/Pictures/A7C2-old-work" \
  --workspace "$HOME/Pictures/A7C2-upgrade"
```

```sh
# 或者：FL2/FL3＋跳过 NTSC 开机提示
./a7c2-mac prepare-upgrade \
  --source-workspace "$HOME/Pictures/A7C2-old-work" \
  --workspace "$HOME/Pictures/A7C2-upgrade" --disable-ntsc-warning
```

工具从自己的原始备份重建所选版本，并把状态记为 `upgrade_prepared`。不要把新候选替换进旧目录，也不要手改 `features`。

### 2. 执行升级并验证

先按[连接与冷启动](#连接与冷启动)完成一次冷启动并连接 MSC。确认接受实验升级风险及已知字段检查停止问题后，输入当前实际电量：

```sh
(
  printf '相机当前实际电量（50–100，只输入数字）：'
  read -r A7C2_BATTERY
  sudo ./a7c2-mac upgrade --workspace "$HOME/Pictures/A7C2-upgrade" \
    --battery-percent "$A7C2_BATTERY" --cold-start-confirmed \
    --accept-experimental-upgrade
)
```

升级会重新核对机身、程序、设置和运行状态，只追加所选版本需要的 AP 窗口，不重写 CP 程序、不写入或保存拍摄设置。写入前建立单次尝试标记，结果未知时不能删标记重跑。

明确输出 `installed_pending_cold_start` 后，再次冷启动、完成上文的试拍与预设检查，并用**新目录**验证：

```sh
sudo ./a7c2-mac verify --workspace "$HOME/Pictures/A7C2-upgrade" --cold-start-confirmed
```

成功为 `verified_after_cold_start`。以后验证和恢复均使用新目录；旧目录保留作原始记录。

## 恢复原版

本节针对本项目 **0.2.0 新建的工作目录**，且安装或升级已经确认完成。未知失败不能直接套用恢复命令。

先在相机选择 **ST**，完成冷启动，再连接 MSC。下面以首次安装目录为例；升级用户将路径换成 `"$HOME/Pictures/A7C2-upgrade"`，不能用旧目录代替当前版本目录。

```sh
(
  printf '相机当前实际电量（50–100，只输入数字）：'
  read -r A7C2_BATTERY
  sudo ./a7c2-mac restore --workspace "$HOME/Pictures/A7C2-work" \
    --battery-percent "$A7C2_BATTERY" --st-selected --cold-start-confirmed
)
```

工具核对当前完整程序，恢复自己的原版程序和目标设置，并处理旧启动快照。**真实恢复尚未实机验证。** 明确输出 `restored_pending_cold_start` 后，再次冷启动，然后用同一个目录验证：

```sh
sudo ./a7c2-mac verify --workspace "$HOME/Pictures/A7C2-work" --cold-start-confirmed
```

升级用户同样把路径换成自己的 `A7C2-upgrade` 目录。新版目录会检查恢复后的原版程序、设置和运行区域，成功为 `restored_verified_after_cold_start`。

## 状态与报错

没有其他相机命令运行时，可以查看本机记录。路径换成当前使用的工作目录：

```sh
./a7c2-mac status --workspace "$HOME/Pictures/A7C2-work"
```

`status` 不需要 sudo、不连接相机，也不会重新核验设备。

| `phase` / 提示 | 含义和下一步 |
| --- | --- |
| `prepared` | 首次备份和机内预检查完成，下一步是 `install` |
| `upgrade_prepared` | 独立升级目录已准备，下一步是 `upgrade`，不是 `install` |
| `installed_pending_cold_start` | 安装或升级主体完成，冷启动后用当前目录 `verify` |
| `verified_after_cold_start` | 电脑端冷启动核验完成，人工试拍和预设调出另行确认 |
| `restored_pending_cold_start` | 恢复主体完成，冷启动后用当前新版目录 `verify` |
| `restored_verified_after_cold_start` | 恢复原版后的电脑端核验完成 |
| 带 `_usb_unconfirmed` 后缀 | 对应主体已核验，但普通 USB 返回未确认；按[阶段说明](docs/STATE-MACHINE.md)处理，不直接重跑 |
| `needs_review`、`STOPPED`、其他中间阶段或未知超时 | 不能当作完成；保留现场、目录和日志，先排查 |

常见问题：

| 问题 | 处理 |
| --- | --- |
| 工作目录已存在 | 先查看状态，保留原文件，不能删除或覆盖来重做 |
| 密码输入不显示 | 正常；只在本机终端输入管理员密码 |
| 即使 sudo 也报权限不足、磁盘未卸载 | 按[USB与故障说明](docs/ADVANCED.md#本次遇到的错误怎么处理)排查占用，不关闭 SIP、不绕过校验 |
| 在 `writing_settings` 停止，提示额外设置变化 | 程序可能已经写入，不能直接重装；阅读[中断安装说明](docs/INTERRUPTED-INSTALL.md)。旧版专项 `diagnose` / `finish-install` 不适用于0.2.0新目录 |
| 新版 `upgrade_programs_written` 因 `0x02770015` 停止 | 先保存证据，不能重复首次安装或升级；仅经[专项审查](docs/UPGRADE-FINISH.md)后决定是否进行 WBI 收尾 |
| 指纹、参数或运行内存不匹配 | 停止，保留证据；不改哈希、候选或状态来放行 |

成功退出码为 `0`，明确的普通 USB 返回未确认为 `2`，运行错误一般为 `1`；参数错误也可能为 `2`。**必须结合 `RESULT` 和报错文字判断，不能只看退出码。** 在 [Issues](https://github.com/MDHan/a7c2-fl23-macos/issues) 反馈时提供版本、命令和脱敏错误文字，不上传完整固件、机身备份、身份或私人日志。

## 让本地 AI 帮你操作

需要能访问本机文件、终端和 USB 的 Codex、Claude Code 等工具。普通网页聊天不能直接完成本机操作。可复制下面这段，并告诉它你选哪种版本、是首次安装还是旧版升级：

```text
请使用 https://github.com/MDHan/a7c2-fl23-macos 帮我操作 A7C II。
先阅读 README.md、AGENTS.md、docs/AI-INSTALL.md 和 docs/VALIDATION.md。
先确认机型、原始固件、当前安装状态、工作目录和已知风险。
版本默认选 FL2/FL3；只有我明确选择跳过 NTSC 提示时才加相应选项。
我了解这是实验改装，授权你执行我明确选择的首次安装或旧版升级流程及验证。
已安装机身不能重复首次安装，升级须创建独立目录；未知阶段先停下检查。
默认一份原始备份，保留旧目录，不改哈希、features或阶段绕过校验。
能自动完成的直接执行；连接MSC、实际电量、管理员认证、取电池和试拍需要我配合时一次说明。
不要通过聊天索取密码，不猜测电量或假定已完成冷启动，不自动重试写入。
完成后分别报告程序核验、人工功能检查和备份位置，不上传我的机身数据。
```

## 验证记录与更多文档

0.2.0 当前通过的离线检查包括：77项主机测试、8项固定上游测试、24项安装器 C 测试，以及24,576组开机窗口模拟。旧版在一台机身上的安装和 `verify` 记录，**不代表这两类新版本已实机通过**。

| 文档 | 内容 |
| --- | --- |
| [验证范围](docs/VALIDATION.md) | 已完成的测试、旧版实机证据和剩余边界 |
| [两类版本研究记录](research/two-profiles/README.md) | 卡上预设修复、收窄的 NTSC 补丁、固定指纹及离线报告 |
| [升级说明](docs/UPGRADE.md) | 旧版 FL2/FL3 升级的适用条件和完整流程 |
| [AI 安装指南](docs/AI-INSTALL.md) | 本地 AI 的详细操作规则 |
| [进阶与故障说明](docs/ADVANCED.md) | USB、恢复及常见故障 |
| [状态机](docs/STATE-MACHINE.md) | 阶段、正常返回超时与停止规则 |

## 贡献来源

| 仓库 | 贡献与本项目的关系 |
| --- | --- |
| [QAQLeonard/a7c2-fl23-research](https://github.com/QAQLeonard/a7c2-fl23-research) | 本项目直接使用的 A7C II 2.01 移植与安装工具：补丁定位、色彩映射、机内安装/恢复、诊断与验证实现。 |
| [lzh2016zzz/a7cr-fl23-research](https://github.com/lzh2016zzz/a7cr-fl23-research) | 上游引用的 A7CR FL2/FL3 移植、参数、标签与 WBI 研究；A7C II 由上游另行定位实现，不能直接套用 A7CR 偏移。 |
| [YuuLuo/a7c2-service-preload](https://github.com/YuuLuo/a7c2-service-preload) | 上游引用的 A7C II 2.01 服务认证与机内执行链研究。 |
| [ma1co/Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE) | 上游使用的 USB/Senser 通信、机身备份操作和解析实现。 |
| [ma1co/fwtool.py](https://github.com/ma1co/fwtool.py/pull/52) | 上游引用的官方固件离线解包分析工具，具体为 PR #52；本项目运行时不依赖它。 |

本项目新增 Mac USB 接管、流程与错误处理、备份核验和教程，并独立研究了卡上预设校验缺口及 NTSC 提示补丁。原生安装、恢复、WBI和运行读取模块继续派生自固定上游源码，保留原作者贡献与许可。完整来源、固定提交与许可见 [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md)。
