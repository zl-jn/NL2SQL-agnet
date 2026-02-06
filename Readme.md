# NL2SQL Agent - 自然语言转SQL智能体

基于大模型和工具调用的智能SQL生成系统，支持多种数据库，具备自动表结构提取和示例数据查看功能。

## 🎯 核心特性

- ✅ **多数据库支持**: MySQL, PostgreSQL, Oracle, SQL Server, DaMeng, MariaDB
- ✅ **智能表识别**: 大模型自动识别相关表，无需手动指定
- ✅ **紧凑格式**: 表结构和示例数据使用简化格式，节省70%+ token
- ✅ **示例数据查看**: 自动获取字段实际值，了解日期格式、枚举值等
- ✅ **自动重试**: SQL执行失败时自动分析错误并重新生成
- ✅ **安全限制**: 仅允许SELECT查询，防止危险操作
- ✅ **标准工具调用**: 遵循OpenAI Function Calling规范

## 📦 快速开始

### 1. 安装依赖

```bash
pip install fastapi uvicorn pydantic httpx sqlalchemy

# 根据数据库类型安装驱动
pip install pymysql          # MySQL/MariaDB
pip install psycopg2-binary  # PostgreSQL
pip install oracledb         # Oracle
pip install pymssql          # SQL Server
pip install dmPython         # DaMeng
```

### 2. 启动服务

```bash
python nl2sql_agent.py
```

服务将在 `http://localhost:8000` 启动

### 3. 发送请求

```python
import httpx
import asyncio

async def generate_sql():
    request = {
        "question": "查询销售额前10的产品",
        "database_config": {
            "db_type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "username": "user",
            "password": "password",
            "database": "sales_db"
        },
        "llm_config": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-xxx",
            "model_name": "gpt-4",
            "temperature": 0.1,
            "max_tokens": 2000
        },
        "authorized_tables": ["products", "orders", "order_items"],
        "max_retries": 3
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/nl2sql",
            json=request,
            timeout=120.0
        )
        return response.json()

result = asyncio.run(generate_sql())
print(f"生成的SQL: {result['sql']}")
```

## 🔧 API 文档

### POST /nl2sql

生成SQL查询

**请求参数**:

```json
{
  "question": "string",                    // 用户的自然语言问题
  "database_config": {
    "db_type": "postgresql",               // 数据库类型
    "host": "localhost",
    "port": 5432,
    "username": "user",
    "password": "password",
    "database": "dbname"
  },
  "llm_config": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-xxx",
    "model_name": "gpt-4",
    "temperature": 0.1,
    "max_tokens": 2000
  },
  "authorized_tables": ["table1", "table2"],  // 授权的表列表
  "reference_info": {                          // 可选：参考信息
    "table_ddl": ["CREATE TABLE ..."],
    "question_sql_pairs": [
      {"question": "示例问题", "sql": "SELECT ..."}
    ],
    "additional_info": "额外说明"
  },
  "conversation_history": [                    // 可选：对话历史
    {"question": "上一个问题", "sql": "SELECT ..."}
  ],
  "max_retries": 3                             // 最大重试次数
}
```

**响应**:

```json
{
  "success": true,
  "sql": "SELECT * FROM products ORDER BY sales DESC LIMIT 10",
  "attempts": 2,
  "intermediate_steps": [...]
}
```

## 🛠️ 工具说明

Agent内置4个工具，大模型会自动选择使用：

### 1. get_table_schema
获取表结构信息（简化格式）

**示例输出**:
```
Table: products
Columns: product_id(INT,PK), name(STR(100)), price(DEC(10,2))
```

### 2. get_sample_data
获取示例数据以了解实际格式

**示例输出**:
```
Table: students
Columns: gender, class_name
Samples (5 rows):
M, 一年级一班
F, 一年级二班
M, 二年级一班
```

### 3. run_sql
执行SQL查询（仅SELECT）

### 4. list_tables
列出所有授权的表

## 💡 工作流程

```
用户问题
   ↓
1. 大模型分析问题，确定需要哪些表
   ↓
2. 调用 get_table_schema 获取表结构
   ↓
3. 如果不确定字段格式，调用 get_sample_data
   ↓
4. 基于结构和示例数据生成SQL
   ↓
5. 调用 run_sql 测试SQL
   ↓
6. 成功 → 返回SQL
   失败 → 分析错误 → 重新生成
```

## 📊 使用示例

### 示例1: 基本查询

**问题**: "统计男女学生人数"

**流程**:
1. 获取表结构: `get_table_schema(["students"])`
2. 查看性别字段格式: `get_sample_data("students", ["gender"])`
   - 发现是 M/F 格式
3. 生成SQL: `SELECT gender, COUNT(*) FROM students GROUP BY gender`
4. 测试执行 ✅

### 示例2: 复杂查询

**问题**: "查询最近30天销售额前10的产品"

**流程**:
1. 获取相关表结构: `get_table_schema(["products", "orders", "order_items"])`
2. 查看日期格式: `get_sample_data("orders", ["order_date"])`
3. 生成复杂JOIN查询
4. 测试执行 ✅

## 🔒 安全特性

- ✅ 只允许 SELECT 和 WITH 查询
- ✅ 检测并阻止危险关键字
- ✅ 表级别授权检查
- ✅ 结果集大小限制（最多100行）
- ✅ 连接和查询超时控制

## 🎨 高级功能

### 1. 参考信息（提高准确性）

```python
"reference_info": {
    "table_ddl": ["CREATE TABLE products (...)"],
    "question_sql_pairs": [
        {"question": "示例", "sql": "SELECT ..."}
    ],
    "additional_info": "业务规则说明"
}
```

### 2. 对话历史（上下文查询）

```python
"conversation_history": [
    {
        "question": "查询所有产品",
        "sql": "SELECT * FROM products"
    }
]
```

## 📈 Token优化

### 表结构格式

- **JSON格式**: ~150 tokens
- **简化格式**: ~30 tokens
- **节省**: 80% ✅

### 示例数据格式

- **JSON格式**: ~100 tokens
- **简化格式**: ~25 tokens
- **节省**: 75% ✅

## 🐛 故障排除

### 连接失败
- 检查数据库配置
- 确认数据库服务运行
- 验证驱动已安装

### SQL不准确
- 提供详细的表DDL
- 添加示例问题-SQL对
- 使用get_sample_data查看实际数据
- 说明业务规则

### 超时错误
- 减少授权表数量
- 增加max_retries
- 优化数据库索引

## 📚 支持的数据库

| 数据库 | 驱动 | db_type值 |
|--------|------|-----------|
| MySQL | pymysql | "mysql" |
| MariaDB | pymysql | "mariadb" |
| PostgreSQL | psycopg2 | "postgresql" |
| Oracle | oracledb | "oracle" |
| SQL Server | pymssql | "sql server" |
| DaMeng | dmPython | "dameng" |

## 🔄 版本历史

### v3.0.0 (当前)
- ✅ 删除冗余工具
- ✅ 简化数据格式
- ✅ 优化工作流程

## 📄 许可证

MIT License

---

**享受智能SQL生成！** 🚀