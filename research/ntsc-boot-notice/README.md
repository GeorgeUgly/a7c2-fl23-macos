# 保留 NTSC，只取消开机提示（实验性）

**组合方案尚未实机验证。** 本目录公开离线研究脚本和验证摘要；仓库另外提供显式选择的组合安装路径，但没有发布 Sony 完整固件、机身备份、保存设置或私人日志。

## 使用时怎样选择

从[主 README](../../README.md)完成环境准备后，在第一次 `prepare` 时二选一：

```sh
# 默认：仅 FL2/FL3，保留 NTSC 开机提示
sudo ./a7c2-mac prepare --workspace "$HOME/Pictures/A7C2-work"

# 或者：FL2/FL3 + 取消 NTSC 开机提示（尚未实机验证）
sudo ./a7c2-mac prepare --workspace "$HOME/Pictures/A7C2-work" --disable-ntsc-warning
```

只执行所选的一条；工作目录必须尚不存在，备份、设备及电量要求与主教程相同。组合选择保存为 `features: ["fl23", "no_ntsc_notice"]`，后续 `install`、`verify`、`restore` 自动沿用。默认是 `["fl23"]`，旧工作目录也按此处理。没有单独安装 NTSC 补丁或给已装 FL2/FL3 机身追加升级的入口；不要编辑状态文件、更换候选或重复安装来切换。

新增选项不会自动将拍摄制式切换到 NTSC，也不会关闭 PAL/NTSC 选择器。目标是保留用户选择的 NTSC，只绕过它在默认制式不同时触发的开机提示。PAL 原有判断分支保留。

## 固件定位与最小修改

限定本地完整指纹匹配的 ILCE-7CM2 2.01。AP 的 `rep_boot_controller::init` 日志记录地区开关、当前制式、默认制式及网络重置状态：

```text
<NPAL> dst:%08x, setting:%08x, default:%08x, nw_rst:%02x
```

日志位于 `0x4121d51`，函数签名位于 `0x4121e48`。两处启动条件窗口分别从 `0x206179c`、`0x2061cf8` 开始，随后根据当前制式选择 NTSC/PAL 提示。定位依据是命名枚举、读取/模型更新链及原生分支，未提取或渲染本地化文字资源。

| AP 文件偏移／VA | 原字节 | 候选字节 | 指令 |
| --- | --- | --- | --- |
| `0x20617b0` | `1f00016b` | `3f00206a` | `cmp w0, w1` → `bics wzr, w1, w0` |
| `0x2061d14` | `3f00006b` | `3f00206a` | `cmp w1, w0` → `bics wzr, w1, w0` |

两处比较中 `w1` 为当前制式，`w0` 为默认制式。按 NTSC=0、PAL=1 的正常枚举域，`current & ~default == 0` 产生 Z 标志：

| 当前 | 默认 | 候选行为 |
| --- | --- | --- |
| NTSC | NTSC | 沿用原有正常后续路径 |
| NTSC | PAL | 跳过 NTSC 制式提示，进入原有正常后续路径 |
| PAL | NTSC | 保留原有 PAL 制式提示路径 |
| PAL | PAL | 沿用原有正常后续路径 |

指令不写通用寄存器或内存；紧随其后的消费者只使用 Z 条件，不依赖不同于 `cmp` 的 N/C/V 标志。这两处窗口在原版与 FL23 AP 中相同，与 FL2/FL3 窗口不重叠。实际改变5个字节，分布在两条4字节指令中。

## 验证结果与限制

[verification.json](verification.json) 保存离线输入完整哈希、补丁窗口、输出哈希及统计。1024组有效枚举组合同时覆盖原版/FL23、两处窗口、地区开关、当前/默认制式、网络重置、四种启动类型、显示条件及其他状态：

- 24组原来选择 NTSC 提示的情况转入正常后续入口。
- 24组 NTSC 不同于默认、但本来没有选择该可见提示的情况，也跳过该制式条件；它们不是24次可见提示消除。
- 其他976组局部结果保持一致，其中包含24组 PAL 提示保留。
- 另8组无效枚举抽查保持断言入口，不代表覆盖任意损坏状态。

执行的是原生条件窗口指令；锁和显示条件读取使用明确的测试替代。没有执行完整启动、真实显示、录像或相机恢复。正常后续入口中的其他提示优先级也没有完成整机验证。

组合安装器基于固定上游安装器，保持原40个窗口顺序并追加2处 AP 窗口。检查原版完整 AP/CP 指纹、42处窗口、写入后的完整组合指纹；恢复反向验证同一精确组合。组合 `verify` 同时读取原有6处和新增2处运行区域。上游文件、默认哈希及默认模块均不改动。

组合 AP SHA-256：`31b7336d39e211bdd9d2cb51d17e831a81703b86b9fab81bd40c97081fd4e8c9`。CP 仍为原 FL23 候选。独立临时模块的哈希及 ELF 检查记录见 [ntsc-manifest.json](../../macfl23/assets/ntsc-manifest.json)。这些小型模块是安装/读取工具，不是完整 Sony 固件。

**组合 prepare/install/verify/restore 均未实机验证；专项 diagnose 暂不支持组合目录，会在USB操作前停止。** 已安装机身的追加升级、任意版本适配、断电救援及长期稳定性也未提供或验证。原有FL2/FL3实机记录不能作为本组合实机证据。

## 离线复现

研究依赖与普通安装分离；下面不连接相机，也不运行 `prepare`。使用自己合法保留、与摘要完整指纹相符的原有 FL23 工作目录，输入需要 `original/` 和 `candidate/`。生成文件只能保存在仓库外的私有目录。

```sh
python3 -m venv "$HOME/a7c2-ntsc-research-env"
"$HOME/a7c2-ntsc-research-env/bin/python" -m pip install -r research/ntsc-boot-notice/requirements.txt
"$HOME/a7c2-ntsc-research-env/bin/python" research/ntsc-boot-notice/analyze_and_verify.py \
  --workspace "$HOME/Pictures/A7C2-work" \
  --output "$HOME/Pictures/A7C2-ntsc-analysis"
```

输出包含组合AP实验副本、局部反汇编及逐项结果，均不要上传。本目录的研究脚本本身不包含设备写入接口；实际可选安装另由 `macfl23` 管理。

构建独立组合原生模块时，先按主教程完成 `setup.sh`，准备 Zig 0.13.0 和上面的研究依赖，再从仓库根目录运行：

```sh
"$HOME/a7c2-ntsc-research-env/bin/python" tools/build_ntsc_native.py --zig /path/to/zig
```

构建器只从经固定指纹核验的上游源码生成组合窗口表和8区域读取器，不修改 `.upstream`。输出移除调试路径；检查 AArch64 ELF、无动态依赖/未解析符号、仅相对重定位，并逐一反查编译后42处窗口的元数据与前后字节。工具链或源码变化必须重新验证，不能跳过资产校验。

原生安装/读取工具及临时文件管理派生自 QAQLeonard 的固定上游代码，见[完整致谢与许可边界](../../ACKNOWLEDGEMENTS.md)。
