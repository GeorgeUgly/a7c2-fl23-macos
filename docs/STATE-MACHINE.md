# 阶段、退出与失败处理

`prepare` → `prepared` → `install` → `installed_pending_cold_start` → 人工完整断电 → `verify` → `verified_after_cold_start`。

准备创建新的机身目录，默认在 original/ 中保存一份备份，逐文件 fsync、读回核验；即使后续失败，已经保存的文件也保留。部分备份或预检查失败不能当作可安装工作目录。

`install` 仅接受完成的预检查；`verify` 仅接受已完成安装/验证；`restore` 仅接受已完成安装/验证及实际电量、ST/冷启动确认。参数/阶段不合规时先拒绝，不接管USB。

## 普通USB返回超时

`NormalReturnTimeout` 只有在以下条件同时满足时才能从会话退出抛出：

1. 操作主体没有抛出异常。
2. 第二次认证已经完成，会话通信仍健康。
3. 服务停止命令正常完成，句柄关闭没有异常。
4. 等待相同物理端口的普通USB身份超时，且没有其他退出错误。

CLI 再根据当前阶段和已保存证据确认完整结果，记录 `prepared_usb_unconfirmed`、`installed_pending_cold_start_usb_unconfirmed`、`restored_pending_cold_start_usb_unconfirmed` 或 `verified_after_cold_start_usb_unconfirmed`，退出码2。没有盲目重试写入，也不把未知身份当作正常相机。

这不同于相机操作途中 USB 超时：任何这种超时或 KeyboardInterrupt 都使会话不健康，禁止进一步设备命令，包括停止认证。不会强制复位USB、关机或尝试修复未知镜像。

## needs_review

已开始的操作发生其他异常，保留当前阶段、错误、完整日志和读回。没有通用强制重试、强制推进阶段或自动恢复命令。请先排查，不要删除 state.json 或把阶段手改成 prepared。

## 恢复

`restore` 调用上游已知候选的完整哈希检查及原文件恢复，成功为 `restored_pending_cold_start`。如果主体完成但普通USB返回超时，会带 `_usb_unconfirmed` 后缀。随后需人工冷启动和检查；本工具的 `verify` 针对已安装FL2/FL3，不验证恢复后的原版运行状态。

默认一份备份；只有显式指定可选的 `--backup-copy` 才额外保存副本。为兼容未修改的上游加载器，单副本状态中的 `backup_copy` 指向 `original/` 自身，`backup_copies: 1` 记录实际份数。该字段不代表存在第二份独立备份。

## 已知 writing_settings 停止点的专项收尾

`finish-install --accept-experimental-finish` 仅处理完整历史证据与实时状态均匹配的原FL2/FL3、固定 +60 失败。详见 [适用条件与风险](INTERRUPTED-INSTALL.md#可选已知停止点的实验性-wbi-收尾)。它不修改计数白名单、不重写程序或保存参数。写WBI之前创建持久化尝试标记并进入 finishing_snapshot；重复尝试一律拒绝。完成后才进入 installed_pending_cold_start（或已完成主体的USB未确认变体）；WBI结果不明则 needs_review / failed_action=finish-install，不允许再次自动续写。此新增路径仅有离线测试，尚无实机结果。
