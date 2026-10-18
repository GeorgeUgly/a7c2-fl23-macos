# Mac 给 A7C2 安装 FL2 / FL3

本项目直接基于 **[QAQLeonard/a7c2-fl23-research](https://github.com/QAQLeonard/a7c2-fl23-research)**（QAQLeonard／小红书「QAQLeonard（读研版）」）进行 Mac 适配。感谢原作者开放 A7C II 的补丁研究、机内工具和验证记录。

## 原项目与贡献来源

把直接上游及其引用的研究、工具一并列在这里，方便追溯：

| 仓库 | 贡献与本项目的关系 |
| --- | --- |
| [QAQLeonard/a7c2-fl23-research](https://github.com/QAQLeonard/a7c2-fl23-research) | 本项目直接使用的 A7C II 2.01 移植与安装工具：补丁定位、色彩映射、机内安装/恢复、诊断与验证实现。 |
| [lzh2016zzz/a7cr-fl23-research](https://github.com/lzh2016zzz/a7cr-fl23-research) | 上游引用的 A7CR FL2/FL3 移植、参数、标签与 WBI 研究；A7C II 由上游另行定位实现，不能直接套用 A7CR 偏移。 |
| [YuuLuo/a7c2-service-preload](https://github.com/YuuLuo/a7c2-service-preload) | 上游引用的 A7C II 2.01 服务认证与机内执行链研究。 |
| [ma1co/Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE) | 上游使用的 USB/Senser 通信、机身备份操作和解析实现。 |
| [ma1co/fwtool.py](https://github.com/ma1co/fwtool.py/pull/52) | 上游引用的官方固件离线解包分析工具，具体为 PR #52；本项目运行时不依赖它。 |

我们的新增工作是 macOS USB 接管、流程与错误处理、备份核验及简明/AI安装教程。上述移植研究与基础工具的贡献归各原作者；固定提交、许可和完整致谢见 [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md)。

> **先看这一句：这是实验性相机改装，有无法正常开机的风险，备份不保证能救砖。** 前身脚本已在一台机身完成安装，本仓库的 `verify` 已实机通过；本仓库通用版的首次备份、安装和恢复仍未实机测试。[查看验证范围](docs/VALIDATION.md)

## 先确认设备和版本

| 需要什么 | 要求 |
| --- | --- |
| 相机 | **索尼 A7C II / A7C2，型号 ILCE-7CM2**；不适用于 A7CR 或其他型号 |
| 相机固件 | **原版 2.01**；工具还会核对完整程序指纹，不能只看菜单版本号 |
| 电脑 | **Mac**；实测为 **MacBook Air M4 / arm64**，其他芯片未验证 |
| 系统 | 实测 **macOS 27.2**；尚未确定最低兼容版本，不要求为此升级到这个版本 |
| Python | **3.10 或更新版本**；实测 **3.12.14**。没有合适版本可交给下面的 AI 流程处理 |
| 连接线 | **能传数据的 USB 线**，相机直连 Mac；纯充电线不行 |
| 电池与存储卡 | 相机实际电量 **至少 50%**；准备可正常拍摄的存储卡，用于安装后试拍 |
| 其他 | Mac 接电、首次下载需要网络；Mac 管理员密码由你在系统/终端中输入 |

已经装过 FL2/FL3 的相机，**不要从头再安装**，让 AI 检查现有工作目录并执行验证。

## 方式一：让 Codex / Claude 帮你操作

使用**能访问本机文件和终端**的 Codex、Claude Code 或其他本地 AI 工具。普通网页聊天不能直接操作你的 USB 相机。

把下面整段复制给它：

```text
请帮我使用这个项目，在我的 Mac 上给 Sony A7C II 安装 FL2/FL3：
https://github.com/MDHan/a7c2-fl23-macos

先阅读 README.md、AGENTS.md 和 docs/AI-INSTALL.md，检查设备、固件、
Python 和已有安装状态；不满足支持条件就停止并说明。
我了解这是实验性改装，授权你完成环境准备、单份备份、预检查、安装和验证。
能自动完成的请直接执行，不要每一步都询问。
需要我连接相机、选择 MSC、提供实际电量、输入管理员密码或取电池时，
一次告诉我具体操作，等我完成再继续，不要猜测或代填。
如果已经安装过，保留已有工作目录，只验证，不重复安装。
遇到报错先检查阶段和日志，不强制重试，不修改校验或伪造成功状态。
完成后告诉我验证结果和备份位置，不要上传我的机身数据。
```

**你需要亲手做的事很少：连接相机并选 MSC、输入实际电量和管理员密码、按提示取电池重启、试拍。** AI 不能替你拔电池，也不应索取你的密码。详细流程见 [AI 安装指南](docs/AI-INSTALL.md)。

## 方式二：自己按步骤操作

下面命令在 Mac 的「终端」运行。默认只保存**一份原始备份**。每一步成功后，再进行下一步。

### 1. 下载工具

先输入 `python3 --version`，确认至少是 3.10。然后复制：

```sh
git clone https://github.com/MDHan/a7c2-fl23-macos.git
cd a7c2-fl23-macos
./setup.sh
```

看到「套件源码和资产完整」即可。缺少 Git/Python、系统提示安装开发者工具或下载失败时，先处理环境；也可以把提示交给 AI。**这一步不加 sudo。**

### 2. 连接相机，选第 2 项

相机开机，用数据线直连 Mac。出现四个选项时：

| 选项 | 怎么选 |
| --- | --- |
| 1 实时流式传输（USB流式传输） | 不选 |
| **2 影像传输（MSC）** | **选这个 ✅** |
| 3 影像传输（MTP） | 不选 |
| 4 遥控拍摄（电脑遥控） | 不选 |

后面再次出现这个界面，仍选 **第 2 项**。关闭正在浏览相机存储卡的窗口，然后运行：

```sh
./a7c2-mac usb-status
```

能显示相机磁盘信息即可继续；报错就先停下排查。

### 3. 备份并检查

```sh
sudo ./a7c2-mac prepare --workspace "$HOME/Pictures/A7C2-work"
```

输入 Mac 密码时，终端**不会显示字符**，输完按回车即可。等到：

```text
RESULT: prepared
```

备份在「图片 → A7C2-work → original」。此步会运行临时诊断模块，尚未写入固件。若提示目录已存在，**不要删除旧目录或覆盖备份**，先让 AI 检查已有状态。

### 4. 安装

复制下面整段，按提示输入**相机屏幕显示的实际电量数字**（50–100）：

```sh
(
  printf '相机当前实际电量（50–100，只输入数字）：'
  read -r A7C2_BATTERY
  sudo ./a7c2-mac install --workspace "$HOME/Pictures/A7C2-work" --battery-percent "$A7C2_BATTERY"
)
```

开始后保持连接，不拔线、不关机。等待：

```text
RESULT: installed_pending_cold_start
```

### 5. 取电池重启，再验证

**只有上一步完成后**才按这个顺序操作：

1. 拔 USB，正常关机，等存取灯熄灭。
2. 取出电池约 **10 秒**，装回开机。
3. 查看 FL2/FL3 是否出现，分别使用相机内建重设；用 ST、FL2、FL3 各拍一张新照片并回放。
4. 重新连接 Mac，选 **第 2 项「影像传输（MSC）」**，运行：

```sh
sudo ./a7c2-mac verify --workspace "$HOME/Pictures/A7C2-work" --cold-start-confirmed
```

看到 **`RESULT: verified_after_cold_start`**，并且拍摄、回放正常，就完成了基本验证。保留整个 `A7C2-work` 文件夹。

## 报错了怎么办？

**先停，不重复安装、不手改状态、不直接取电池。** 把终端最后几行交给 AI 检查。

- `sudo: a password is required`：需要你在终端启动对应命令、输入密码；AI 不一定能沿用你终端的授权。
- 其他 `STOPPED`、`needs_review` 或带 `usb_unconfirmed` 的结果：先看 [故障处理](docs/ADVANCED.md#本次遇到的错误怎么处理)，确认阶段后再继续。

恢复原版、完整命令与技术说明放在 [进阶文档](docs/ADVANCED.md)。请勿公开机身备份、原始程序、私人日志或密码。
