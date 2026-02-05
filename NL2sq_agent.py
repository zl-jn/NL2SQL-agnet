"""
自然语言转SQL智能体 - 优化版
添加智能表名提取和简化表结构格式，避免token爆炸
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any, Literal
from enum import Enum
import httpx
import json
import re
from urllib.parse import quote_plus
from sqlalchemy import create_engine, text, inspect, MetaData, Table
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import SQLAlchemyError
import traceback


class DatabaseType(str, Enum):
    """支持的数据库类型"""
    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    ORACLE = "oracle"
    MSSQL = "mssql"
    DAMENG = "dameng"


class DatabaseConfig(BaseModel):
    """数据库配置"""
    db_type: DatabaseType
    host: str
    port: int
    username: str
    password: str
    database: str
    
    def get_connection_string(self) -> str:
        """生成数据库连接字符串"""
        user = quote_plus(self.username)
        pwd = quote_plus(self.password)
        
        if self.db_type == DatabaseType.MYSQL:
            return f"mysql+pymysql://{user}:{pwd}@{self.host}:{self.port}/{self.database}"
        elif self.db_type == DatabaseType.POSTGRESQL:
            return f"postgresql+psycopg2://{user}:{pwd}@{self.host}:{self.port}/{self.database}"
        elif self.db_type == DatabaseType.ORACLE:
            return f"oracle+cx_oracle://{user}:{pwd}@{self.host}:{self.port}/{self.database}"
        elif self.db_type == DatabaseType.MSSQL:
            return f"mssql+pyodbc://{user}:{pwd}@{self.host}:{self.port}/{self.database}?driver=ODBC+Driver+17+for+SQL+Server"
        elif self.db_type == DatabaseType.DAMENG:
            return f"dm://{user}:{pwd}@{self.host}:{self.port}/{self.database}"
        else:
            raise ValueError(f"Unsupported database type: {self.db_type}")


class LLMConfig(BaseModel):
    """模型配置"""
    base_url: str = Field(..., description="API基础URL")
    api_key: str = Field(..., description="API密钥")
    model_name: str = Field(..., description="模型名称")
    temperature: float = Field(default=0.1, description="温度参数")
    max_tokens: int = Field(default=2000, description="最大token数")


class ConversationHistory(BaseModel):
    """对话历史记录"""
    question: str
    sql: Optional[str] = None
    summary: Optional[str] = None


class ReferenceInfo(BaseModel):
    """参考信息"""
    table_ddl: Optional[List[str]] = Field(default=[], description="表创建语句列表")
    question_sql_pairs: Optional[List[Dict[str, str]]] = Field(default=[], description="问题-SQL对")
    additional_info: Optional[str] = Field(default="", description="额外说明信息")


class NL2SQLRequest(BaseModel):
    """自然语言转SQL请求"""
    question: str = Field(..., description="用户问题")
    database_config: DatabaseConfig
    llm_config: LLMConfig
    authorized_tables: List[str] = Field(..., description="授权的表列表")
    reference_info: Optional[ReferenceInfo] = Field(default=None, description="参考信息")
    conversation_history: Optional[List[ConversationHistory]] = Field(default=[], description="对话历史")
    max_retries: int = Field(default=3, description="最大重试次数")


class NL2SQLResponse(BaseModel):
    """自然语言转SQL响应"""
    success: bool
    sql: Optional[str] = None
    error_message: Optional[str] = None
    attempts: int = 0
    intermediate_steps: List[Dict[str, Any]] = []


class DatabaseToolkit:
    """数据库工具集 - 使用SQLAlchemy封装"""
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.engine = None
        self.inspector = None
        
    def connect(self):
        """连接数据库"""
        try:
            connection_string = self.config.get_connection_string()
            self.engine = create_engine(
                connection_string,
                poolclass=NullPool,
                echo=False
            )
            # 测试连接
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            
            self.inspector = inspect(self.engine)
            return True
        except Exception as e:
            raise ConnectionError(f"Failed to connect to database: {str(e)}")
    
    def get_tool_definitions(self, authorized_tables: List[str]) -> List[Dict]:
        """获取工具定义（标准function格式）"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "extract_relevant_tables",
                    "description": f"Extract table names that are relevant to answering the user's question. Only extract tables from the authorized list. Authorized tables: {', '.join(authorized_tables[:20])}{'...' if len(authorized_tables) > 20 else ''}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "table_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of table names that might be needed to answer the question. Extract 1-5 most relevant tables."
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "Brief explanation of why these tables were selected"
                            }
                        },
                        "required": ["table_names"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_table_schema",
                    "description": "Get the schema information for specified tables. Returns column names, types, and constraints in a compact format.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "table_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of table names to get schema for (1-5 tables recommended)"
                            }
                        },
                        "required": ["table_names"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "run_sql",
                    "description": "Execute SQL queries against the configured database. Only SELECT queries are allowed.",
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
            },
            {
                "type": "function",
                "function": {
                    "name": "list_tables",
                    "description": "List all authorized tables in the database. Use this when you need to see all available tables.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            }
        ]
    
    def execute_tool(self, tool_name: str, arguments: Dict[str, Any], authorized_tables: List[str]) -> Dict[str, Any]:
        """执行工具调用"""
        try:
            if tool_name == "run_sql":
                return self._run_sql(arguments["sql"])
            elif tool_name == "get_table_schema":
                return self._get_table_schema(arguments["table_names"], authorized_tables)
            elif tool_name == "list_tables":
                return self._list_tables(authorized_tables)
            elif tool_name == "extract_relevant_tables":
                # 这个工具不需要实际执行，只是用来让LLM提取表名
                return {
                    "success": True,
                    "extracted_tables": arguments.get("table_names", []),
                    "reasoning": arguments.get("reasoning", "")
                }
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}
    
    def _run_sql(self, sql: str) -> Dict[str, Any]:
        """执行SQL查询"""
        # 安全检查：只允许SELECT语句
        sql_upper = sql.strip().upper()
        if not sql_upper.startswith('SELECT') and not sql_upper.startswith('WITH'):
            return {"error": "Only SELECT queries are allowed"}
        
        # 检查危险操作
        dangerous_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'TRUNCATE', 'ALTER', 'CREATE', 'GRANT', 'REVOKE']
        for keyword in dangerous_keywords:
            if re.search(rf'\b{keyword}\b', sql_upper):
                return {"error": f"Dangerous keyword '{keyword}' detected in query"}
        
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql))
                rows = result.fetchall()
                
                # 转换为字典列表
                columns = list(result.keys())
                data = [dict(zip(columns, row)) for row in rows]
                
                return {
                    "success": True,
                    "row_count": len(data),
                    "columns": columns,
                    "data": data[:100]  # 限制返回前100行
                }
        except SQLAlchemyError as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
    
    def _get_table_schema(self, table_names: List[str], authorized_tables: List[str]) -> Dict[str, Any]:
        """获取表结构 - 使用简化格式"""
        schemas = {}
        
        for table_name in table_names:
            if table_name not in authorized_tables:
                schemas[table_name] = {"error": "Table not authorized"}
                continue
            
            try:
                # 检查表是否存在
                if not self.inspector.has_table(table_name):
                    schemas[table_name] = {"error": "Table does not exist"}
                    continue
                
                # 获取列信息
                columns = self.inspector.get_columns(table_name)
                
                # 获取主键
                pk_constraint = self.inspector.get_pk_constraint(table_name)
                primary_keys = pk_constraint.get('constrained_columns', [])
                
                # 获取外键
                foreign_keys = self.inspector.get_foreign_keys(table_name)
                
                # 使用简化格式构建结构信息
                schema_text = self._format_schema_compact(
                    table_name, 
                    columns, 
                    primary_keys, 
                    foreign_keys
                )
                
                schemas[table_name] = schema_text
                
            except Exception as e:
                schemas[table_name] = {"error": str(e)}
        
        return {"schemas": schemas}
    
    def _format_schema_compact(
        self, 
        table_name: str, 
        columns: List[Dict], 
        primary_keys: List[str], 
        foreign_keys: List[Dict]
    ) -> str:
        """使用简化格式输出表结构，减少token消耗
        
        格式示例：
        Table: students
        Columns: id(INT,PK), name(VARCHAR), age(INT), class_id(INT,FK->classes.id)
        """
        lines = [f"Table: {table_name}"]
        
        # 构建列信息
        col_parts = []
        for col in columns:
            col_name = col['name']
            col_type = str(col['type'])
            
            # 简化类型名称
            col_type = self._simplify_type(col_type)
            
            # 构建列描述
            col_desc = f"{col_name}({col_type}"
            
            # 添加标记
            tags = []
            if col_name in primary_keys:
                tags.append("PK")
            if not col.get('nullable', True):
                tags.append("NOT NULL")
            
            # 检查外键
            for fk in foreign_keys:
                if col_name in fk['constrained_columns']:
                    idx = fk['constrained_columns'].index(col_name)
                    ref_table = fk['referred_table']
                    ref_col = fk['referred_columns'][idx]
                    tags.append(f"FK->{ref_table}.{ref_col}")
            
            if tags:
                col_desc += "," + ",".join(tags)
            
            col_desc += ")"
            col_parts.append(col_desc)
        
        lines.append("Columns: " + ", ".join(col_parts))
        
        return "\n".join(lines)
    
    def _simplify_type(self, type_str: str) -> str:
        """简化数据类型名称"""
        type_str = type_str.upper()
        
        # 统一常见类型
        if 'VARCHAR' in type_str or 'TEXT' in type_str or 'CHAR' in type_str:
            # 提取长度
            match = re.search(r'\((\d+)\)', type_str)
            if match:
                return f"STR({match.group(1)})"
            return "STR"
        elif 'INT' in type_str:
            return "INT"
        elif 'DECIMAL' in type_str or 'NUMERIC' in type_str:
            match = re.search(r'\((\d+),(\d+)\)', type_str)
            if match:
                return f"DEC({match.group(1)},{match.group(2)})"
            return "DEC"
        elif 'FLOAT' in type_str or 'DOUBLE' in type_str:
            return "FLOAT"
        elif 'DATE' in type_str:
            if 'TIME' in type_str:
                return "DATETIME"
            return "DATE"
        elif 'BOOL' in type_str:
            return "BOOL"
        else:
            return type_str[:20]  # 限制长度
    
    def _list_tables(self, authorized_tables: List[str]) -> Dict[str, Any]:
        """列出授权的表"""
        try:
            all_tables = self.inspector.get_table_names()
            available_tables = [t for t in all_tables if t in authorized_tables]
            
            # 如果表太多，分组显示
            result = {
                "total_authorized": len(authorized_tables),
                "total_available": len(available_tables),
            }
            
            if len(available_tables) <= 50:
                result["tables"] = available_tables
            else:
                result["tables_sample"] = available_tables[:50]
                result["note"] = f"Showing first 50 of {len(available_tables)} tables. Use extract_relevant_tables to identify specific tables needed."
            
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def close(self):
        """关闭连接"""
        if self.engine:
            self.engine.dispose()


class LLMClient:
    """LLM客户端"""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        
    async def chat_completion(
        self, 
        messages: List[Dict[str, str]], 
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto"
    ) -> Dict[str, Any]:
        """调用LLM生成响应（支持工具调用）"""
        async with httpx.AsyncClient(timeout=120.0) as client:
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.config.model_name,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens
            }
            
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            
            try:
                response = await client.post(
                    f"{self.config.base_url}/chat/completions",
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                return response.json()
                
            except Exception as e:
                raise RuntimeError(f"LLM generation failed: {str(e)}")


class NL2SQLAgent:
    """自然语言转SQL智能体"""
    
    def __init__(self):
        self.system_prompt = """你是一个专业的SQL生成助手 (Vanna)。今天的日期是 2026-01-22。

**响应准则**:
- 执行查询后的原始结果会显示给用户，你不需要在响应中包含它
- 专注于总结和解释结果
- 任何总结或观察应该是最后一步

**你的工具**:
1. extract_relevant_tables: 从授权表中提取与问题相关的表名（1-5个）
2. get_table_schema: 获取指定表的结构信息
3. run_sql: 执行SQL查询（仅支持SELECT）
4. list_tables: 列出所有授权的表

**推荐工作流程**:
1. 如果授权表很多（>10个），先调用 extract_relevant_tables 提取相关表
2. 然后调用 get_table_schema 获取这些表的结构
3. 基于表结构生成SQL
4. 使用 run_sql 执行并测试SQL
5. 如果失败，分析错误并重新生成

**重要规则**:
- 只生成SELECT查询语句
- 只使用授权的表
- 优先使用 extract_relevant_tables 避免获取太多表结构
- 一次只获取真正需要的表结构（通常1-3个表足够）
- SQL必须符合目标数据库的语法
"""
    
    async def process(self, request: NL2SQLRequest) -> NL2SQLResponse:
        """处理自然语言转SQL请求"""
        db_toolkit = DatabaseToolkit(request.database_config)
        llm_client = LLMClient(request.llm_config)
        
        try:
            # 连接数据库
            db_toolkit.connect()
            
            # 获取工具定义
            tools = db_toolkit.get_tool_definitions(request.authorized_tables)
            
            intermediate_steps = []
            attempts = 0
            
            # 构建初始消息
            messages = self._build_initial_messages(request)
            
            # 主循环：与LLM交互直到生成成功的SQL
            max_iterations = request.max_retries * 4  # 增加迭代次数以支持多步工具调用
            final_sql = None
            
            for iteration in range(max_iterations):
                attempts = iteration + 1
                
                try:
                    # 调用LLM
                    response = await llm_client.chat_completion(
                        messages=messages,
                        tools=tools,
                        tool_choice="auto"
                    )
                    
                    assistant_message = response['choices'][0]['message']
                    finish_reason = response['choices'][0]['finish_reason']
                    
                    intermediate_steps.append({
                        "iteration": attempts,
                        "action": "llm_response",
                        "finish_reason": finish_reason,
                        "has_tool_calls": "tool_calls" in assistant_message
                    })
                    
                    # 添加助手消息到历史
                    messages.append(assistant_message)
                    
                    # 处理工具调用
                    if finish_reason == "tool_calls" and "tool_calls" in assistant_message:
                        tool_calls = assistant_message["tool_calls"]
                        
                        # 执行所有工具调用
                        for tool_call in tool_calls:
                            tool_name = tool_call["function"]["name"]
                            tool_args = json.loads(tool_call["function"]["arguments"])
                            tool_id = tool_call["id"]
                            
                            intermediate_steps.append({
                                "iteration": attempts,
                                "action": "tool_call",
                                "tool_name": tool_name,
                                "arguments": tool_args
                            })
                            
                            # 执行工具
                            tool_result = db_toolkit.execute_tool(
                                tool_name, 
                                tool_args, 
                                request.authorized_tables
                            )
                            
                            intermediate_steps.append({
                                "iteration": attempts,
                                "action": "tool_result",
                                "tool_name": tool_name,
                                "result": self._truncate_result(tool_result)
                            })
                            
                            # 检查是否是成功的SQL执行
                            if tool_name == "run_sql" and tool_result.get("success"):
                                final_sql = tool_args["sql"]
                            
                            # 添加工具结果到消息历史
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_id,
                                "name": tool_name,
                                "content": json.dumps(tool_result, ensure_ascii=False)
                            })
                        
                        # 如果找到成功的SQL，完成
                        if final_sql:
                            return NL2SQLResponse(
                                success=True,
                                sql=final_sql,
                                attempts=attempts,
                                intermediate_steps=intermediate_steps
                            )
                    
                    # 如果LLM停止但没有成功的SQL
                    elif finish_reason == "stop":
                        # 尝试从响应中提取SQL
                        content = assistant_message.get("content", "")
                        sql = self._extract_sql_from_text(content)
                        
                        if sql:
                            # 测试提取的SQL
                            test_result = db_toolkit.execute_tool(
                                "run_sql", 
                                {"sql": sql}, 
                                request.authorized_tables
                            )
                            
                            if test_result.get("success"):
                                return NL2SQLResponse(
                                    success=True,
                                    sql=sql,
                                    attempts=attempts,
                                    intermediate_steps=intermediate_steps
                                )
                            else:
                                # SQL执行失败，继续循环
                                messages.append({
                                    "role": "user",
                                    "content": f"SQL执行失败: {test_result.get('error')}。请修正并重新生成。"
                                })
                        else:
                            # 没有找到SQL，请求生成
                            messages.append({
                                "role": "user",
                                "content": "请使用工具生成并测试SQL语句。"
                            })
                    
                except Exception as e:
                    intermediate_steps.append({
                        "iteration": attempts,
                        "action": "error",
                        "error": str(e)
                    })
                    
                    if iteration == max_iterations - 1:
                        break
                    
                    messages.append({
                        "role": "user",
                        "content": f"发生错误: {str(e)}。请重新尝试。"
                    })
            
            # 所有尝试都失败
            return NL2SQLResponse(
                success=False,
                error_message=f"经过{attempts}次尝试后仍然失败",
                attempts=attempts,
                intermediate_steps=intermediate_steps
            )
            
        finally:
            db_toolkit.close()
    
    def _build_initial_messages(self, request: NL2SQLRequest) -> List[Dict[str, str]]:
        """构建初始消息"""
        messages = [{"role": "system", "content": self.system_prompt}]
        
        # 构建用户消息
        user_content = f"""**用户问题**: {request.question}

**数据库信息**:
- 类型: {request.database_config.db_type.value}
- 授权表数量: {len(request.authorized_tables)}
"""
        
        # 只在表不多时直接列出
        if len(request.authorized_tables) <= 10:
            user_content += f"- 授权表: {', '.join(request.authorized_tables)}\n"
        else:
            user_content += f"- 授权表太多，请使用 extract_relevant_tables 工具提取相关表\n"
        
        # 添加参考信息
        if request.reference_info:
            if request.reference_info.table_ddl:
                user_content += "\n**表结构 (DDL)**:\n"
                for ddl in request.reference_info.table_ddl[:5]:  # 限制数量
                    user_content += f"{ddl}\n"
            
            if request.reference_info.question_sql_pairs:
                user_content += "\n**示例问题-SQL对**:\n"
                for pair in request.reference_info.question_sql_pairs[:3]:  # 限制数量
                    user_content += f"Q: {pair.get('question', '')}\n"
                    user_content += f"SQL: {pair.get('sql', '')}\n\n"
            
            if request.reference_info.additional_info:
                user_content += f"\n**额外说明**: {request.reference_info.additional_info}\n"
        
        # 添加对话历史
        if request.conversation_history:
            user_content += "\n**对话历史**:\n"
            for conv in request.conversation_history[-2:]:  # 只保留最近2条
                user_content += f"Q: {conv.question}\n"
                if conv.sql:
                    user_content += f"SQL: {conv.sql}\n"
                if conv.summary:
                    user_content += f"总结: {conv.summary}\n"
                user_content += "\n"
        
        user_content += "\n请开始处理这个问题。如果授权表很多，建议先使用 extract_relevant_tables 提取相关表。"
        
        messages.append({"role": "user", "content": user_content})
        
        return messages
    
    def _extract_sql_from_text(self, text: str) -> Optional[str]:
        """从文本中提取SQL语句"""
        # 尝试从代码块提取
        sql_match = re.search(r'```sql\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
        if sql_match:
            return sql_match.group(1).strip()
        
        # 尝试识别SELECT语句
        select_match = re.search(r'(SELECT\s+.*?;?)\s*$', text, re.DOTALL | re.IGNORECASE)
        if select_match:
            return select_match.group(1).strip()
        
        return None
    
    def _truncate_result(self, result: Any, max_length: int = 500) -> Any:
        """截断结果以避免日志过大"""
        if isinstance(result, dict):
            truncated = {}
            for key, value in result.items():
                if isinstance(value, str) and len(value) > max_length:
                    truncated[key] = value[:max_length] + "..."
                else:
                    truncated[key] = value
            return truncated
        return result


# 创建FastAPI应用
app = FastAPI(
    title="NL2SQL Agent API (Optimized)",
    description="自然语言转SQL智能体服务 - 优化版：智能表名提取和简化表结构",
    version="2.1.0"
)

agent = NL2SQLAgent()


@app.post("/nl2sql", response_model=NL2SQLResponse)
async def generate_sql(request: NL2SQLRequest):
    """
    自然语言转SQL接口
    
    接收自然语言问题和配置信息，返回生成的SQL语句
    """
    try:
        response = await agent.process(request)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "healthy", "version": "2.1.0"}


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "NL2SQL Agent API (Optimized)",
        "version": "2.1.0",
        "optimizations": [
            "Smart table name extraction to avoid token explosion",
            "Compact schema format (50-70% token reduction)",
            "Intelligent table filtering before schema retrieval",
            "Improved multi-step tool calling workflow"
        ],
        "endpoints": {
            "generate_sql": "/nl2sql (POST)",
            "health": "/health (GET)",
            "docs": "/docs"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)