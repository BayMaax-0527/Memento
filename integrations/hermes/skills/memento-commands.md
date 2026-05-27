---
name: memento-commands
description: "自然语言调用 Memento 记忆系统：注入记忆、查XXX、查知识库、查决策"
version: 1.0.0
author: Memento Community
tags: [memento, memory, knowledge, retrieval, injection]
---

# Memento 命令接口

## 前提

Memento 安装在 `~/workspace/Memento/`。如果路径不同，请替换下面所有 `{MEMENTO_ROOT}`。

## 指令表

当你听到以下关键词时，执行对应命令：

### 注入记忆

你说"注入记忆" → 执行:

```bash
cd {MEMENTO_ROOT}/Engine && python3 hooks/remember.py --source session
```

### 注入知识

你说"注入知识" → 需要你先提供文档文件(.md)。然后执行:

```bash
cd {MEMENTO_ROOT}/Engine && python3 hooks/remember.py --source doc --file <文档路径>
```

### 查记忆

你说"查 XXX"（如"查 LM Studio 配置"） → 执行:

```bash
cd {MEMENTO_ROOT}/Engine && python3 src/retriever.py --query "XXX" --global
```

### 查知识库

你说"查知识库 XXX" → 执行:

```bash
cd {MEMENTO_ROOT}/Engine && python3 src/retriever.py --query "XXX" --domain knowledge
```

### 查决策

你说"查决策" → 执行:

```bash
cd {MEMENTO_ROOT}/Engine && python3 src/retriever.py --decisions
```

### 语义搜索

你说"语义查 XXX" → 执行:

```bash
cd {MEMENTO_ROOT}/Engine && python3 src/retriever.py --query "XXX" --semantic
```

### 状态检查

你说"记忆状态"或"系统状态" → 执行:

```bash
cd {MEMENTO_ROOT}/Engine && python3 hooks/auto.py status
```

## 自动召回

你说"开启自动召回" → 记住开关标记，在每轮回答前执行 `auto_inject()`:
你说"关闭自动召回" → 清除开关标记，停止自动检索。

## 安装

将此 .md 文件放入你的 Agent 技能目录（如 `~/.hermes/skills/`），替换 `{MEMENTO_ROOT}` 为实际路径。
