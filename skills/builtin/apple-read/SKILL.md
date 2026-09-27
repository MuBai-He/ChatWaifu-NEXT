---
id: apple.read
version: 1.0.0
name: apple.read
description: 读取指定 Mac 的已选 Apple 日历或提醒事项列表。先用 organizer.read 取得设备和列表ID。返回排队凭据，随后 organizer.read 查询真实结果。
---

# 读取 Apple 日历和提醒事项

读取指定 Mac 的已选 Apple 日历或提醒事项列表。先用 organizer.read 取得设备和列表ID。返回排队凭据，随后 organizer.read 查询真实结果。

个人数据只用于主人私聊。不要猜测设备、列表或事项标识。含时区的时间以用户所在时区解释；时间不明确先问清楚。

排队和处理中不表示已创建或完成。不要重复提交写操作；查询原 operation_id 的结果。结果不确定时请用户在 Apple 应用核对。

离线、睡眠、关机的电脑不能响铃。任务保存不保证准时送达。不能控制手机原生时钟；复杂重复日程编辑不支持。
