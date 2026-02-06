# NL2SQL Agent - 重构版

## 🎯 重构亮点

本版本参考Vanna AI的设计模式，进行了以下重构：

### 1. ✅ SQLAlchemy数据库封装
- 使用SQLAlchemy统一管理数据库连接
- 支持连接池和事务管理
- 更好的跨数据库兼容性

### 2. ✅ 标准工具调用格式
- 遵循OpenAI Function Calling标准
- 工具定义使用标准的JSON Schema格式
- 与主流LLM API完全兼容

### 3. ✅ 智能工具集
封装了三个核心工具：

#### `run_sql`
执行SQL查询（仅SELECT）
```json
{
  "type": "function",
  "function": {
    "name": "run_sql",
    "description": "Execute SQL queries against the configured database",
    "parameters": {
      "type": "object",
      "properties": {
        "sql": {
          "type": "string",
          "description": "SQL query to execute (SELECT only)"
        }
      },
      "required": ["sql"]
    }
  }
}
```

#### `get_table_schema`
获取表结构信息
```json
{
  "type": "function",
  "function": {
    "name": "get_table_schema",
    "description": "Get the schema information for tables",
    "parameters": {
      "type": "object",
      "properties": {
        "table_names": {
          "type": "array",
          "items": {"type": "string"},
          "description": "List of table names"
        }
      },
      "required": ["table_names"]
    }
  }
}
```

#### `list_tables`
列出所有授权表
```json
{
  "type": "function",
  "function": {
    "name": "list_tables",
    "description": "List all authorized tables",
    "parameters": {
      "type": "object",
      "properties": {}
    }
  }
}
```

## 🔄 工作流程

### 标准工作流

```
用户问题
    ↓
LLM分析问题
    ↓
需要表结构? → 调用 get_table_schema
    ↓
生成SQL
    ↓
调用 run_sql 测试
    ↓
成功? → 返回SQL
    ↓
失败? → 分析错误 → 重新生成
```

### 工具调用示例

**1. LLM收到工具定义**
```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_table_schema",
            "parameters": {...}
        }
    }
]
```

**2. LLM返回工具调用**
```json
{
  "tool_calls": [
    {
      "id": "call_123",
      "type": "function",
      "function": {
        "name": "get_table_schema",
        "arguments": "{\"table_names\": [\"students\"]}"
      }
    }
  ]
}
```

**3. 执行工具并返回结果**
```json
{
  "role": "tool",
  "tool_call_id": "call_123",
  "name": "get_table_schema",
  "content": "{\"schemas\": {...}}"
}
```

**4. LLM基于结果生成SQL**
```json
{
  "tool_calls": [
    {
      "id": "call_456",
      "type": "function",
      "function": {
        "name": "run_sql",
        "arguments": "{\"sql\": \"SELECT COUNT(*) FROM students;\"}"
      }
    }
  ]
}
```

## 🚀 快速开始

### 安装

```bash
pip install -r requirements_refactored.txt
```

### 启动服务

```bash
python nl2sql_agent_refactored.py
```

### 测试

```bash
python test_client_refactored.py
```

## 📋 API使用

### 请求示例

```python
import httpx
import asyncio

async def query():
    request = {
        "question": "一共有多少学生",
        "database_config": {
            "db_type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "username": "user",
            "password": "pass",
            "database": "school"
        },
        "model_config": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-xxx",
            "model_name": "gpt-4"
        },
        "authorized_tables": ["students"],
        "max_retries": 3
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/nl2sql",
            json=request
        )
        return response.json()

result = asyncio.run(query())
print(result['sql'])
```

### 响应格式

```json
{
  "success": true,
  "sql": "SELECT COUNT(*) FROM students;",
  "attempts": 2,
  "intermediate_steps": [
    {
      "iteration": 1,
      "action": "tool_call",
      "tool_name": "get_table_schema",
      "arguments": {"table_names": ["students"]}
    },
    {
      "iteration": 1,
      "action": "tool_result",
      "tool_name": "get_table_schema",
      "result": {"schemas": {...}}
    },
    {
      "iteration": 2,
      "action": "tool_call",
      "tool_name": "run_sql",
      "arguments": {"sql": "SELECT COUNT(*) FROM students;"}
    },
    {
      "iteration": 2,
      "action": "tool_result",
      "tool_name": "run_sql",
      "result": {"success": true, "row_count": 1}
    }
  ]
}
```

## 🔧 核心组件

### DatabaseToolkit
使用SQLAlchemy封装的数据库工具集

```python
toolkit = DatabaseToolkit(database_config)
toolkit.connect()

# 获取工具定义
tools = toolkit.get_tool_definitions(authorized_tables)

# 执行工具
result = toolkit.execute_tool("get_table_schema", 
                              {"table_names": ["students"]},
                              authorized_tables)
```

### LLMClient
标准的LLM客户端，支持工具调用

```python
client = LLMClient(model_config)

response = await client.chat_completion(
    messages=[...],
    tools=[...],
    tool_choice="auto"
)
```

### NL2SQLAgent
主智能体，协调工具调用和SQL生成

```python
agent = NL2SQLAgent()
response = await agent.process(request)
```

## 🛡️ 安全特性

### SQL注入防护
- 只允许SELECT和WITH查询
- 检测并阻止危险关键字
- 使用参数化查询

### 权限控制
- 表级别授权检查
- 工具级别权限验证
- 结果集大小限制

## 📊 表结构提取

### 使用SQLAlchemy Inspector

```python
inspector = inspect(engine)

# 获取列信息
columns = inspector.get_columns(table_name)

# 获取主键
pk = inspector.get_pk_constraint(table_name)

# 获取外键
fks = inspector.get_foreign_keys(table_name)

# 获取索引
indexes = inspector.get_indexes(table_name)
```

### 返回的结构信息

```json
{
  "schemas": {
    "students": {
      "columns": [
        {
          "name": "student_id",
          "type": "INTEGER",
          "nullable": false,
          "primary_key": true
        }
      ],
      "primary_keys": ["student_id"],
      "foreign_keys": [
        {
          "columns": ["class_id"],
          "referred_table": "classes",
          "referred_columns": ["class_id"]
        }
      ],
      "indexes": [...]
    }
  }
}
```

## 🎨 与Vanna AI的对比

### 相似之处
- ✅ 工具调用模式
- ✅ 自动表结构获取
- ✅ SQL测试验证
- ✅ 错误重试机制

### 差异
- 📦 **无内存系统**: 通过传入对话历史实现
- 🔧 **FastAPI封装**: 作为独立服务运行
- 🎯 **更灵活**: 支持动态配置数据库和模型
- 🛡️ **更严格**: 增强的安全检查

## 🔍 调试技巧

### 查看中间步骤

响应中的`intermediate_steps`包含详细的执行日志：

```python
for step in result['intermediate_steps']:
    print(f"[{step['iteration']}] {step['action']}")
    if step['action'] == 'tool_call':
        print(f"  Tool: {step['tool_name']}")
        print(f"  Args: {step['arguments']}")
    elif step['action'] == 'tool_result':
        print(f"  Result: {step['result']}")
```

### 常见问题

**Q: 工具调用失败**
- 检查工具定义格式
- 验证参数schema
- 查看tool_result中的error

**Q: SQL生成不准确**
- 提供更详细的table_ddl
- 增加question_sql_pairs示例
- 使用get_table_schema自动获取结构

**Q: 多次重试仍失败**
- 查看intermediate_steps
- 检查数据库连接
- 验证表权限

## 📚 扩展开发

### 添加新工具

```python
def get_tool_definitions(self, authorized_tables: List[str]) -> List[Dict]:
    tools = [
        # 现有工具...
        {
            "type": "function",
            "function": {
                "name": "your_new_tool",
                "description": "Tool description",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param1": {
                            "type": "string",
                            "description": "Parameter description"
                        }
                    },
                    "required": ["param1"]
                }
            }
        }
    ]
    return tools

def execute_tool(self, tool_name: str, arguments: Dict, authorized_tables: List[str]):
    if tool_name == "your_new_tool":
        return self._your_new_tool(arguments)
    # ...
```

### 自定义System Prompt

```python
self.system_prompt = """
你的自定义提示词...
包含工具使用说明和特定领域知识
"""
```

## 🎉 总结

这个重构版本：
- ✅ 使用SQLAlchemy统一数据库访问
- ✅ 遵循标准工具调用格式
- ✅ 自动提取表结构辅助生成
- ✅ 完整的安全和错误处理
- ✅ 易于扩展和集成

完全符合现代LLM应用的最佳实践！