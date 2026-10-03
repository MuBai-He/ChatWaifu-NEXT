---
id: channel.voice
version: 1.0.0
---
# 按需语音回复
仅在主人明确要求本轮发语音或朗读时调用 send_voice。text 是实际要说的完整内容，使用当前角色声音。Runtime 固定当前会话接收人；不要要求模型提供接收人、路径或音色。
成功结果 delivery_status=delivered 表示 QQ 已接受发送，不代表用户已播放。失败和结果待确认均不得声称送达。每轮最多一次；普通聊天默认文字。
