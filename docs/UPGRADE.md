# 旧版 FL2/FL3 升级到两类新版本

**仍为实验流程。** 新版组合已在一台机身上经[专项WBI收尾](UPGRADE-FINISH.md)后通过冷启动程序、参数和9处运行区域核验；使用者另确认拍摄回放、M1–M4保存调出和NTSC提示表现正常。0.2.1 已针对这次观察到的时间相关字段变化调整新升级的设置检查，通过时直接完成原有收尾，无需另跑命令。这个自动全流程仅做离线测试和历史快照重放，未重新上机全程执行。默认保留提示版的实机升级、首次安装及恢复尚未验证。原版 2.01、从未改装的机身使用[主 README 的首次安装流程](../README.md)，不用本页升级入口。

## 适用范围与选择

当前支持原有项目的旧版 FL2/FL3：必须保留该机身完整的八项原始备份和候选，原目录阶段为 `verified_after_cold_start`。型号是 ILCE-7CM2，原始固件为 2.01。校验的是这些版本共同的完整程序指纹，并非某一台机身的专用固件。

目标只有两种，均内置 M1–M4 校验修复：FL2/FL3，或者 FL2/FL3＋跳过 NTSC 提示。旧版 NTSC 组合、其他改装、缺失备份或未知阶段会拒绝升级，不自动恢复、不重试写入。也不能将此入口用于切换已经安装的新版本。

## 1. 离线准备新目录

保留旧目录，不更换其中候选、不手改状态。下面示例将其称为 `A7C2-old-work`，新目录 `A7C2-upgrade` 必须尚不存在。此步不加 sudo、不连接相机。

```sh
# FL2/FL3：内置 M1–M4 修复，保留开机提示
./a7c2-mac prepare-upgrade \
  --source-workspace "$HOME/Pictures/A7C2-old-work" \
  --workspace "$HOME/Pictures/A7C2-upgrade"
```

或者明确选择跳过提示版本，**二选一，不要两条都执行**：

```sh
./a7c2-mac prepare-upgrade \
  --source-workspace "$HOME/Pictures/A7C2-old-work" \
  --workspace "$HOME/Pictures/A7C2-upgrade" --disable-ntsc-warning
```

工具校验原备份、原候选及冷启动完成状态，将该机身原始文件复制到独立目录，并重建所选版本候选。原目录不改动；新目录默认一份原始备份。两个目录在同一磁盘不能视为物理容灾。成功阶段为 `upgrade_prepared`，此时还未操作相机。

## 2. 明确执行单次实验升级

先正常关机、等存取灯熄灭，取电池约10秒后装回开机；USB连接选择第2项影像传输（MSC）。读取相机当前实际电量，至少50%。将下方占位符替换为实际值。

```sh
sudo ./a7c2-mac upgrade --workspace "$HOME/Pictures/A7C2-upgrade" \
  --battery-percent ACTUAL_PERCENT --cold-start-confirmed \
  --accept-experimental-upgrade
```

升级前会再次核对机身身份、完整 AP/CP、当前/保存/直接读取的原有 FL2/FL3 参数、运行内存及 WBI 状态。FL2/FL3 只追加一个 AP 窗口，带跳过提示则追加三个 AP 窗口；不重写 CP 程序，不写入或保存拍摄设置。程序完整读回和设置保持性检查通过后，才处理旧启动快照。

0.2.1 的设置检查要求保存设置的键集合、数据和属性逐项完全一致；当前设置仅为 chip0/0x02770015 增加有界例外：属性必须为2、长度4字节，增量为60的正倍数且与本轮升级经过的秒数之差不超过120秒。本轮用时由单调时钟测量，须在24小时内；不变的字段照常通过。其他当前字段仍由固定上游的原有保持性检查处理，机身身份、完整指纹、运行窗口、单次标记和WBI结果核验均保留。字段含义仍未确认，默认规则依据本次观察到的行为，不是全局放行。

新执行的升级符合上述规则时，会在同一次命令内完成WBI，无需 `finish-upgrade` 或额外时间例外选项。若已有目录处于 `needs_review`，本次修正不自动续写，仍须保留证据按专项审查处理；其他不符合条件的变化继续停止。

写入前创建 `upgrade-attempt.private.json`，结果未知时不允许重复。不要删除标记、改阶段或改校验来重跑。发生失败先保留证据；原生工作器只在其自身写入/读回失败时尝试反向回退，不能将它视为硬件救援保证。

## 3. 冷启动并验证

只有最终输出 `installed_pending_cold_start` 后，才按主教程正常关机、取电池约10秒、重新开机。随后用**新目录**验证：

```sh
sudo ./a7c2-mac verify --workspace "$HOME/Pictures/A7C2-upgrade" --cold-start-confirmed
```

分别重设 FL2/FL3、用 ST/FL2/FL3 拍新照片并回放；将 FL2、FL3 存入 M1–M4，重新调出并试拍。跳过提示版本还需检查完整断电启动、PAL/NTSC切换及视频。计算机验证通过不能替代这些人工检查。任何 `_usb_unconfirmed` 或错误按[状态说明](STATE-MACHINE.md)处理，不自动续写。

## 恢复原版

成功升级后仍使用新目录，选择 ST 并完成冷启动，再按主教程执行 `restore --battery-percent ACTUAL_PERCENT --st-selected --cold-start-confirmed`。它对所选版本的完整指纹和41/43窗口做反向恢复，同时恢复自己的原始目标设置并处理快照。

恢复完成后再次冷启动，新版目录的 `verify --cold-start-confirmed` 会核验原版完整程序、原始目标设置及7/9个运行区域，成功阶段为 `restored_verified_after_cold_start`。真实恢复尚未验证；组合追加升级的有限实机结果与剩余边界见[验证记录](VALIDATION.md)。
