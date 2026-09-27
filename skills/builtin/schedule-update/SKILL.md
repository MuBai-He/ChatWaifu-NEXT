---
id: schedule.update
version: 1.0.0
name: Update scheduled reminder
---

先查询 organizer.read，保持原 request_id 和读取时的 revision。明确用户希望改动的字段，再提交完整 task 与 expected_revision。冲突时重新读取，不要自动覆盖其他修改。写入需要现有 Skill 权限和确认。
