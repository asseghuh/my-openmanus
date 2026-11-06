from app.config import config
from app.tool.python_execute import PythonExecute


class NormalPythonExecute(PythonExecute):
    """A tool for executing Python code with timeout and safety restrictions."""

    name: str = "python_execute"
    description: str = """执行 Python 代码，用于深度数据分析 / 数据报告（任务结论）/ 其他不直接可视化的常规任务。"""
    parameters: dict = {
        "type": "object",
        "properties": {
            "code_type": {
                "description": "代码类型：数据处理 / 数据报告 / 其他",
                "type": "string",
                "default": "process",
                "enum": ["process", "report", "others"],
            },
            "code": {
                "type": "string",
                "description": """要执行的 Python 代码。
# 注意
1. 代码应生成内容完整的文本报告，包含数据集概览、列详情、基础统计、派生指标、时间序列对比、异常值和关键洞察。
2. 所有输出都用 print()，以便分析内容（包括「数据集概览」或「预处理结果」等章节）清晰可见，同时保存下来
3. 将报告 / 处理后的文件 / 每项分析结果保存到工作目录：{directory}
4. 数据报告需要内容充实，包含整体分析过程和相应的数据可视化。
5. 可以分步调用本工具，从摘要到深入分析，并保存数据报告""".format(
                    directory=config.workspace_root
                ),
            },
        },
        "required": ["code"],
    }

    async def execute(self, code: str, code_type: str | None = None, timeout=5):
        return await super().execute(code, timeout)
