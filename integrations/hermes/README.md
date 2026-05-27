# Hermes Integration

如果你是 [Hermes Agent](https://hermesagent.org.cn) 用户，安装此技能后可以说"注入记忆"、"查 XXX"来调用 Memento，不用手敲命令。

## 安装

```bash
# 复制技能文件（替换 {MEMENTO_ROOT} 为你的实际路径）
cp integrations/hermes/skills/memento-commands.md ~/.hermes/skills/

# 启动新会话即可使用
```

## 可用指令

| 你说 | 效果 |
|------|------|
| 注入记忆 | 压缩当前会话 → 提取摘要 → 写入双库 |
| 注入知识 | 处理文档 → 写入知识库 |
| 查 XXX | 检索记忆库 |
| 查知识库 XXX | 检索知识库 |
| 查决策 | 列出最近决策 |
| 语义查 XXX | 向量语义搜索 |
| 开启/关闭自动召回 | 开关自动检索 |

## 非 Hermes 用户

参考根目录 [README.md](../../README.md) 中的命令手册，手动执行对应命令即可。
